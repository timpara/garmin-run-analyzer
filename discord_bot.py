from __future__ import annotations

import datetime
import os
import sys
from zoneinfo import ZoneInfo

import discord
from discord.ext import tasks
from dotenv import load_dotenv

from agent import Deps, create_agent
from garmin_client import GarminClient

DISCORD_MSG_LIMIT = 2000

DAILY_WORKOUT_CHANNEL = "daily-workout"
DAILY_WORKOUT_PROMPT = (
    "Give me a workout recommendation for today. Base it on my recent training, "
    "training load, and recovery/readiness, including last night's sleep and HRV. "
    "If today should be a rest day, say so clearly and explain why. Keep it concise "
    "and actionable."
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
            now = datetime.datetime.now(tzinfo)
            today = now.strftime("%A, %d %B %Y")
            label = "Updated workout" if now.hour > 7 else "Workout"
            header = f"**{label} for {today}** (as of {now.strftime('%H:%M')})\n\n"
            for chunk in split_message(header + result.output):
                await channel.send(chunk)
        except Exception as e:
            await channel.send(f"Could not generate today's workout: {e}")

    @tasks.loop(
        time=[
            datetime.time(hour=7, minute=0, tzinfo=tzinfo),
            datetime.time(hour=8, minute=0, tzinfo=tzinfo),
            datetime.time(hour=9, minute=0, tzinfo=tzinfo),
        ],
    )
    async def daily_workout_task() -> None:
        await post_daily_workout()

    @daily_workout_task.before_loop
    async def before_daily_workout() -> None:
        await client.wait_until_ready()

    @client.event
    async def on_ready() -> None:
        print(f"Discord bot connected as {client.user}. DM it to chat.")
        if not daily_workout_task.is_running():
            daily_workout_task.start()
            print(
                f"Scheduled daily workout posts at 07:00, 08:00, 09:00 {tz_name} "
                f"in #{DAILY_WORKOUT_CHANNEL}."
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

    client.run(discord_token)


if __name__ == "__main__":
    main()
