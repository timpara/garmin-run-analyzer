from __future__ import annotations

import asyncio
import datetime
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
import uvicorn
from discord.ext import tasks
from dotenv import load_dotenv

from agent import Deps, create_agent
from dashboard import WorkoutCache, create_dashboard_app
from garmin_client import GarminClient

DISCORD_MSG_LIMIT = 2000
SEEN_RUNS_FILE = Path(os.getenv("SEEN_RUNS_FILE", "seen_runs.json"))
MAX_SEEN_IDS = 200

TRAINING_RECAP_CHANNEL = "training-recap"
TRAINING_RECAP_PROMPT = (
    "A new run just completed. Here are the basic stats:\n\n{run_summary}\n\n"
    "Use get_run_details (activity_id {activity_id}) and any other relevant tools "
    "to do a full analysis. Assess pacing consistency, HR drift, effort vs zones, "
    "and any notable patterns. Give a short verdict (1 line) followed by 1-2 key "
    "observations and one actionable takeaway. Keep it under 300 words."
)

DAILY_WORKOUT_CHANNEL = "daily-workout"
DAILY_WORKOUT_PROMPT = (
    "What should I do today? Look at my recovery/readiness, recent training across "
    "all sports (running, cycling, strength), and current load balance. Suggest the "
    "best session for today — could be a run, a ride, a strength workout, or rest. "
    "If I'm well recovered and my aerobic base is solid, lean toward quality work "
    "(intervals, threshold, tempo) rather than defaulting to easy. If today should be "
    "rest, say so and explain why. Keep it concise and actionable."
)


def split_message(text: str, limit: int = DISCORD_MSG_LIMIT) -> list[str]:
    """Split text into chunks that fit Discord's message length limit."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Prefer to split on a newline, then a space, then hard-cut.
        split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n ")
    if remaining:
        chunks.append(remaining)
    return chunks


def load_seen_runs() -> set[int]:
    """Load previously seen activity IDs from disk."""
    if SEEN_RUNS_FILE.exists():
        try:
            data = json.loads(SEEN_RUNS_FILE.read_text())
            return set(data)
        except (json.JSONDecodeError, TypeError):
            return set()
    return set()


def save_seen_runs(seen: set[int]) -> None:
    """Persist seen activity IDs, keeping only the most recent ones."""
    # Keep bounded to avoid unbounded growth
    ids = sorted(seen)[-MAX_SEEN_IDS:]
    SEEN_RUNS_FILE.write_text(json.dumps(ids))


def main() -> None:
    load_dotenv()

    garmin_email = os.getenv("GARMIN_EMAIL")
    garmin_password = os.getenv("GARMIN_PASSWORD")
    ai_base_url = os.getenv("AI_FOUNDRY_BASE_URL")
    ai_api_key = os.getenv("AI_FOUNDRY_API_KEY")
    discord_token = os.getenv("DISCORD_BOT_TOKEN")
    allowed_user_id_raw = os.getenv("DISCORD_ALLOWED_USER_ID")
    allowed_user_id = int(allowed_user_id_raw) if allowed_user_id_raw else None
    allowed_channels_raw = os.getenv("DISCORD_CHANNELS", "")
    allowed_channels = {
        ch.strip().lower()
        for ch in allowed_channels_raw.split(",")
        if ch.strip()
    }
    tz_name = os.getenv("DISCORD_TIMEZONE", "UTC")
    dashboard_port = int(os.getenv("DASHBOARD_PORT", "8080"))
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        print(f"Unknown timezone '{tz_name}', falling back to UTC.")
        tzinfo = ZoneInfo("UTC")

    if not all(
        [garmin_email, garmin_password, ai_base_url, ai_api_key, discord_token]
    ):
        print("Missing required environment variables. See .env.example")
        sys.exit(1)

    # Login to Garmin
    print("Logging in to Garmin Connect...")
    print("(If MFA is required, check your email for a verification code)")
    garmin = GarminClient(garmin_email, garmin_password)
    try:
        garmin.login()
    except Exception as e:
        err = str(e)
        if "429" in err or "rate" in err.lower():
            print("\nRate limited by Garmin. Wait a few minutes and try again.")
        else:
            print(f"\nFailed to login to Garmin Connect: {e}")
        sys.exit(1)
    print("Logged in successfully.\n")

    # Create agent
    agent = create_agent(ai_base_url, ai_api_key)
    deps = Deps(garmin=garmin)

    # Workout cache — shared between bot (writer) and dashboard (reader)
    workout_cache = WorkoutCache()

    # Dashboard web app
    dashboard_app = create_dashboard_app(garmin, workout_cache)

    # Single-user, in-memory conversation history for the bot lifetime.
    message_history: list = []

    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True
    intents.guilds = True
    client = discord.Client(intents=intents)

    def find_daily_workout_channel() -> discord.TextChannel | None:
        for channel in client.get_all_channels():
            if (
                isinstance(channel, discord.TextChannel)
                and channel.name.lower() == DAILY_WORKOUT_CHANNEL
            ):
                return channel
        return None

    async def post_daily_workout() -> None:
        channel = find_daily_workout_channel()
        if channel is None:
            print(f"No #{DAILY_WORKOUT_CHANNEL} channel found; skipping post.")
            return
        try:
            # Use a throwaway history so the daily post is self-contained.
            result = await agent.run(DAILY_WORKOUT_PROMPT, deps=deps)
            workout_cache.update(result.output)
            now = datetime.datetime.now(tzinfo)
            today = now.strftime("%A, %d %B %Y")
            label = "Workout"
            header = f"**{label} for {today}** (as of {now.strftime('%H:%M')})\n\n"
            for chunk in split_message(header + result.output):
                await channel.send(chunk)
        except Exception as e:
            await channel.send(f"Could not generate today's workout: {e}")

    @tasks.loop(
        time=[
            datetime.time(hour=7, minute=30, tzinfo=tzinfo),
        ],
            datetime.time(hour=9, minute=0, tzinfo=tzinfo),
        ],
    )
    async def daily_workout_task() -> None:
        await post_daily_workout()

    @daily_workout_task.before_loop
    async def before_daily_workout() -> None:
        await client.wait_until_ready()

    # --- Training recap polling ---
    seen_runs = load_seen_runs()

    def find_training_recap_channel() -> discord.TextChannel | None:
        for channel in client.get_all_channels():
            if (
                isinstance(channel, discord.TextChannel)
                and channel.name.lower() == TRAINING_RECAP_CHANNEL
            ):
                return channel
        return None

    @tasks.loop(minutes=15)
    async def training_recap_task() -> None:
        nonlocal seen_runs
        channel = find_training_recap_channel()
        if channel is None:
            return

        try:
            recent_runs = garmin.get_recent_runs(5)
        except Exception as e:
            print(f"[training-recap] Failed to fetch recent runs: {e}")
            return

        # On first ever run (empty state), seed with current IDs to avoid spam.
        if not seen_runs:
            seen_runs = {r.activity_id for r in recent_runs}
            save_seen_runs(seen_runs)
            print(f"[training-recap] Seeded {len(seen_runs)} existing run IDs.")
            return

        new_runs = [r for r in recent_runs if r.activity_id not in seen_runs]
        if not new_runs:
            return

        for run in new_runs:
            pace_str = (
                f"{int(run.avg_pace_min_per_km)}:{int((run.avg_pace_min_per_km % 1) * 60):02d}/km"
                if run.avg_pace_min_per_km
                else "N/A"
            )
            run_summary = (
                f"- Name: {run.activity_name}\n"
                f"- Date: {run.activity_date}\n"
                f"- Distance: {run.distance_km:.2f} km\n"
                f"- Duration: {int(run.duration_seconds // 60)}:{int(run.duration_seconds % 60):02d}\n"
                f"- Avg Pace: {pace_str}\n"
                f"- Avg HR: {run.avg_heart_rate or 'N/A'} bpm\n"
                f"- Elevation Gain: {run.elevation_gain_m or 0:.0f} m"
            )
            prompt = TRAINING_RECAP_PROMPT.format(
                run_summary=run_summary, activity_id=run.activity_id
            )
            try:
                result = await agent.run(prompt, deps=deps)
                header = f"**New Run: {run.activity_name}** ({run.activity_date})\n\n"
                for chunk in split_message(header + result.output):
                    await channel.send(chunk)
                seen_runs.add(run.activity_id)
                save_seen_runs(seen_runs)
            except Exception as e:
                print(f"[training-recap] Error posting recap for {run.activity_id}: {e}")

    @training_recap_task.before_loop
    async def before_training_recap() -> None:
        await client.wait_until_ready()

    @client.event
    async def on_ready() -> None:
        print(f"Discord bot connected as {client.user}. DM it to chat.")
        if not daily_workout_task.is_running():
            daily_workout_task.start()
            print(
                f"Scheduled daily workout post at 07:30 {tz_name} "
                f"in #{DAILY_WORKOUT_CHANNEL}."
            )
        if not training_recap_task.is_running():
            training_recap_task.start()
            print(
                f"Polling for new runs every 15 minutes; "
                f"recaps will post in #{TRAINING_RECAP_CHANNEL}."
            )

    @client.event
    async def on_message(message: discord.Message) -> None:
        nonlocal message_history

        # Ignore our own messages.
        if message.author == client.user:
            return
        # Allow DMs and messages in designated channels.
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_allowed_channel = (
            hasattr(message.channel, "name")
            and message.channel.name.lower() in allowed_channels
        )
        if not is_dm and not is_allowed_channel:
            return
        # Restrict to the single allowed user, if configured.
        if allowed_user_id is not None and message.author.id != allowed_user_id:
            return

        content = message.content.strip()
        if not content:
            return

        try:
            async with message.channel.typing():
                result = await agent.run(
                    content,
                    deps=deps,
                    message_history=message_history,
                )
            message_history = result.all_messages()
            for chunk in split_message(result.output):
                await message.channel.send(chunk)
        except Exception as e:
            await message.channel.send(f"Error: {e}")

    async def _run() -> None:
        # Start both the Discord bot and the dashboard web server concurrently
        config = uvicorn.Config(
            dashboard_app,
            host="0.0.0.0",
            port=dashboard_port,
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(config)
        print(f"Dashboard will be available at http://0.0.0.0:{dashboard_port}")
        await asyncio.gather(
            client.start(discord_token),
            server.serve(),
        )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
