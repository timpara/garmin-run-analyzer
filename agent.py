from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel

from garmin_client import GarminClient


@dataclass
class Deps:
    garmin: GarminClient


SYSTEM_PROMPT = """\
You are an expert running coach and sports scientist with access to the user's \
Garmin Connect running data. You analyze their workouts and provide actionable insights.

Your capabilities:
- Fetch and display recent runs or runs in a date range
- Analyze pace trends, heart rate zones, cadence patterns
- Compare workouts side by side
- Provide weekly training summaries
- Generate personalized workout recommendations based on training load and progression

When analyzing data:
- Always cite specific numbers (pace, distance, HR, cadence)
- Identify patterns and trends across multiple runs
- Flag potential overtraining or undertraining signals
- Consider the 80/20 rule (80% easy, 20% hard) for training distribution
- Use heart rate zones to assess training intensity distribution

When recommending workouts:
- Base recommendations on the user's recent training load and fitness level
- Include specific paces, distances, and target heart rate zones
- Consider recovery needs based on recent hard efforts
- Suggest progressive overload that follows the 10% weekly mileage rule
- Include variety: easy runs, tempo runs, intervals, long runs, recovery runs

Format responses clearly with structured data when showing multiple runs.\
"""


def create_agent(base_url: str, api_key: str) -> Agent[Deps, str]:
    model = OpenAIModel(
        "gpt-5.4-mini",
        base_url=base_url,
        api_key=api_key,
    )

    agent = Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        deps_type=Deps,
        result_type=str,
    )

    @agent.tool
    async def fetch_recent_runs(ctx: RunContext[Deps], count: int = 10) -> str:
        """Fetch the most recent running activities. Returns a list of runs with key metrics.

        Args:
            count: Number of recent runs to fetch (default 10, max 50).
        """
        count = min(count, 50)
        runs = ctx.deps.garmin.get_recent_runs(count)
        return json.dumps([r.model_dump(mode="json") for r in runs], default=str)

    @agent.tool
    async def fetch_runs_by_date_range(
        ctx: RunContext[Deps], start_date: str, end_date: str
    ) -> str:
        """Fetch running activities within a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
        """
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        runs = ctx.deps.garmin.get_runs_in_range(start, end)
        return json.dumps([r.model_dump(mode="json") for r in runs], default=str)

    @agent.tool
    async def get_run_details(ctx: RunContext[Deps], activity_id: int) -> str:
        """Get detailed information about a specific run, including splits and heart rate zones.

        Args:
            activity_id: The Garmin activity ID.
        """
        activity = ctx.deps.garmin.get_activity_details(activity_id)
        return json.dumps(activity.model_dump(mode="json"), default=str)

    @agent.tool
    async def get_weekly_summaries(ctx: RunContext[Deps], weeks: int = 4) -> str:
        """Get weekly training summaries (total distance, runs, avg pace, etc).

        Args:
            weeks: Number of weeks to look back (default 4, max 12).
        """
        weeks = min(weeks, 12)
        summaries = ctx.deps.garmin.get_weekly_summaries(weeks)
        return json.dumps([s.model_dump(mode="json") for s in summaries], default=str)

    @agent.tool
    async def analyze_pace_trends(ctx: RunContext[Deps], count: int = 20) -> str:
        """Analyze pace trends over recent runs. Returns runs sorted by date with pace data for trend analysis.

        Args:
            count: Number of recent runs to analyze (default 20).
        """
        runs = ctx.deps.garmin.get_recent_runs(min(count, 50))
        runs.sort(key=lambda r: r.activity_date or date.min)
        data = [
            {
                "date": str(r.activity_date),
                "distance_km": r.distance_km,
                "avg_pace_min_per_km": r.avg_pace_min_per_km,
                "pace_str": r.pace_str,
                "duration_str": r.duration_str,
            }
            for r in runs
        ]
        return json.dumps(data)

    @agent.tool
    async def analyze_hr_zones(ctx: RunContext[Deps], count: int = 10) -> str:
        """Analyze heart rate zone distribution across recent runs. Fetches detailed HR zone data for each run.

        Args:
            count: Number of recent runs to analyze (default 10).
        """
        runs = ctx.deps.garmin.get_recent_runs(min(count, 20))
        results = []
        for run in runs:
            detailed = ctx.deps.garmin.get_activity_details(run.activity_id)
            results.append({
                "date": str(detailed.activity_date),
                "name": detailed.activity_name,
                "distance_km": detailed.distance_km,
                "avg_hr": detailed.avg_heart_rate,
                "max_hr": detailed.max_heart_rate,
                "hr_zones": [z.model_dump() for z in detailed.hr_zones],
            })
        return json.dumps(results)

    @agent.tool
    async def analyze_cadence(ctx: RunContext[Deps], count: int = 10) -> str:
        """Analyze running cadence patterns across recent runs.

        Args:
            count: Number of recent runs to analyze (default 10).
        """
        runs = ctx.deps.garmin.get_recent_runs(min(count, 30))
        data = [
            {
                "date": str(r.activity_date),
                "distance_km": r.distance_km,
                "avg_pace_min_per_km": r.avg_pace_min_per_km,
                "avg_cadence": r.avg_cadence,
                "max_cadence": r.max_cadence,
            }
            for r in runs
        ]
        return json.dumps(data)

    @agent.tool
    async def compare_runs(ctx: RunContext[Deps], activity_ids: list[int]) -> str:
        """Compare two or more runs side by side with detailed metrics including splits and HR zones.

        Args:
            activity_ids: List of Garmin activity IDs to compare.
        """
        results = []
        for aid in activity_ids[:5]:  # max 5 comparisons
            detailed = ctx.deps.garmin.get_activity_details(aid)
            results.append(detailed.model_dump(mode="json"))
        return json.dumps(results, default=str)

    @agent.tool
    async def get_training_load_summary(ctx: RunContext[Deps]) -> str:
        """Get a comprehensive training load summary for the last 4 weeks,
        useful for making workout recommendations. Includes weekly volumes,
        intensity distribution, and recent workout details."""
        summaries = ctx.deps.garmin.get_weekly_summaries(4)
        recent_runs = ctx.deps.garmin.get_recent_runs(7)

        # Get HR zone details for the last few runs
        detailed_recent = []
        for run in recent_runs[:5]:
            detailed = ctx.deps.garmin.get_activity_details(run.activity_id)
            detailed_recent.append({
                "date": str(detailed.activity_date),
                "name": detailed.activity_name,
                "distance_km": detailed.distance_km,
                "duration_str": detailed.duration_str,
                "avg_pace_min_per_km": detailed.avg_pace_min_per_km,
                "avg_hr": detailed.avg_heart_rate,
                "training_effect_aerobic": detailed.training_effect_aerobic,
                "training_effect_anaerobic": detailed.training_effect_anaerobic,
                "hr_zones": [z.model_dump() for z in detailed.hr_zones],
            })

        return json.dumps({
            "weekly_summaries": [s.model_dump(mode="json") for s in summaries],
            "recent_detailed_runs": detailed_recent,
        }, default=str)

    return agent
