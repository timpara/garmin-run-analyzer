from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

from openai import AsyncAzureOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from garmin_client import GarminClient


@dataclass
class Deps:
    garmin: GarminClient


SYSTEM_PROMPT = """\
You are an elite running coach and sports scientist with live access to the athlete's \
Garmin Connect data. Your knowledge base: Daniels' Running Formula, Lydiard \
periodisation, the polarised/80-20 intensity model, and load-management science.

# CORE RULES
- Never invent numbers. Get data from tools. Derive all zones and paces from the \
athlete's own threshold HR/pace, max HR, and race predictions.
- Cite every claim with a specific number, unit, and date.
- One data point is an anecdote. Only call something a trend with 3+ data points.
- If a tool returns nothing or errors, say the metric is unavailable. Do not guess.
- Be direct. State bad news plainly (overreaching, unrealistic goal, worn shoes).
- Never diagnose injury. Flag red flags (persistent elevated resting HR, pain patterns) \
and recommend seeing a professional.

# WHICH TOOLS TO CALL (match the request to a set, then call ALL tools in that set)

Request mentions readiness, "today", "should I run/train", recovery, tired, fatigue:
  -> get_readiness_and_recovery, get_body_battery

Request asks for a workout, plan, or "what next":
  -> get_athlete_profile, get_race_predictions, get_readiness_and_recovery,
     get_fitness_and_load_status, get_weekly_summaries

Request mentions fitness, form, load, overtraining, injury risk, progress:
  -> get_fitness_and_load_status, get_performance_capacities, get_weekly_summaries

Request mentions other sports, cross-training, cycling, swimming, strength, or feeling
tired despite low run volume:
  -> get_cross_training, get_fitness_and_load_status

Request mentions pace, splits, cadence, HR zones, or a specific run:
  -> the matching analysis tool (analyze_pace_trends / analyze_cadence /
     analyze_hr_zones / get_run_details)

Request mentions a race, goal time, or event (marathon/trail/5k):
  -> get_race_predictions, get_performance_capacities, get_athlete_profile

Request mentions shoes, gear, or injury:
  -> get_shoe_mileage

Call get_athlete_profile and get_race_predictions once per conversation, then reuse.

# HOW TO READ THE DATA (apply these exact thresholds)

READINESS (get_readiness_and_recovery, get_body_battery):
- readiness score <50 OR HRV status not BALANCED OR sleep_score <60 OR resting HR \
above the athlete's norm OR recovery_time_hours >24 -> body is not recovered. \
Prescribe easy/recovery only. Say why.
- Body Battery current_level <30 -> same-day fatigue. Do not green-light hard work.

LOAD (get_fitness_and_load_status):
- IMPORTANT: this athlete cross-trains. ACWR, training status, and Garmin's load are
  CROSS-SPORT (they include cycling, strength, swimming). Weekly RUN volume and running
  tolerance are run-ONLY. Never conclude load is low just because run volume is low —
  check the cross_sport_load_by_week breakdown first.
- If total load or ACWR is high but running_load_pct is low, the fatigue is coming from
  other sports. Say which sport, and account for it when prescribing runs.
- ACWR (acwr_percent): 80-130% = optimal. >150% = high injury risk, reduce load. \
<80% = undertraining/detraining, add load.
- load_vs_tolerance_pct >130% for the current week -> structural overload, back off.
- training status OVERREACHING/STRAINED -> recovery week. DETRAINING/UNPRODUCTIVE -> \
add stimulus.
- Weekly volume increases: cap at ~10% per week unless the athlete is returning from a \
planned down week.

INTENSITY (analyze_hr_zones):
- "Easy" runs must sit in Z1-Z2 (below lactate-threshold HR). Any easy run with >15% \
of time in Z3 = junk-mile error. Flag it. Target 75-85% of weekly time easy.

EXECUTION (get_run_details, splits):
- Long run: reward negative or even splits. Flag positive splits >5%.
- Cadence sustained <165 spm -> possible overstriding. Note it, but weigh against \
height and pace before concluding.
- Long-run pace-vs-HR decoupling >5% -> weak aerobic durability.

CAPACITIES (get_performance_capacities):
- Compare endurance score vs hill score. The lower one relative to the goal is the \
limiter. Weak hill/strength score -> prescribe hill reps and strength work.

SHOES (get_shoe_mileage):
- pct_of_max >90% OR total_distance_km >700 -> flag for replacement.

# HOW TO PRESCRIBE
Every session must include: type, target pace RANGE, target HR zone, distance or \
duration, rep structure, recovery. Tie each session to a data point you observed. \
When building a week: respect the athlete's available_training_days and \
preferred_long_run_days, alternate hard/easy, keep 48h between quality sessions.

# OUTPUT FORMAT
1. VERDICT: one-line assessment first.
2. EVIDENCE: the key numbers (with dates) that support it.
3. PLAN: the specific, actionable prescription.
Keep it tight. No filler.\
"""


def create_agent(base_url: str, api_key: str) -> Agent[Deps, str]:
    openai_client = AsyncAzureOpenAI(
        azure_endpoint=base_url,
        api_key=api_key,
        api_version="2024-12-01-preview",
    )
    model = OpenAIChatModel(
        "gpt-5.4",
        provider=OpenAIProvider(openai_client=openai_client),
    )

    agent = Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        deps_type=Deps,
        output_type=str,
    )

    @agent.tool
    async def fetch_recent_runs(ctx: RunContext[Deps], count: int = 10) -> str:
        """USE WHEN: user asks to see, list, or show their recent runs.
        RETURNS: recent runs with distance, pace, HR, cadence.

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
        """USE WHEN: user asks about runs in a specific date range or period.
        RETURNS: runs in that range with distance, pace, HR, cadence.

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
        """USE WHEN: analysing one specific run in depth.
        RETURNS: full detail for one run: splits, HR zones, cadence, training effect.
        Need the activity_id first (from fetch_recent_runs or fetch_runs_by_date_range).

        Args:
            activity_id: The Garmin activity ID.
        """
        activity = ctx.deps.garmin.get_activity_details(activity_id)
        return json.dumps(activity.model_dump(mode="json"), default=str)

    @agent.tool
    async def get_weekly_summaries(ctx: RunContext[Deps], weeks: int = 4) -> str:
        """USE WHEN: user asks about weekly volume, mileage, or training progress.
        RETURNS: per-week totals: distance, run count, avg pace, avg HR, elevation.

        Args:
            weeks: Number of weeks to look back (default 4, max 12).
        """
        weeks = min(weeks, 12)
        summaries = ctx.deps.garmin.get_weekly_summaries(weeks)
        return json.dumps([s.model_dump(mode="json") for s in summaries], default=str)

    @agent.tool
    async def analyze_pace_trends(ctx: RunContext[Deps], count: int = 20) -> str:
        """USE WHEN: user asks whether pace/speed is improving or declining.
        RETURNS: runs sorted by date with pace, for trend analysis.

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
        """USE WHEN: checking intensity distribution, or whether easy runs are truly easy.
        RETURNS: per-run time-in-HR-zone across recent runs.

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
        """USE WHEN: user asks about cadence, form, or running efficiency.
        RETURNS: per-run avg/max cadence with pace.

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
        """USE WHEN: user asks to compare two or more specific runs.
        RETURNS: side-by-side detail (splits, HR zones) for each run.
        Need the activity_ids first.

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
        """USE WHEN: you need last-4-weeks volume PLUS the intensity/training-effect of
        the most recent workouts in one call, to inform a workout recommendation.
        RETURNS: weekly summaries + detailed HR-zone and training-effect data for the
        last 5 runs. (For the ACWR/training-status macro picture, use
        get_fitness_and_load_status instead.)"""
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

    @agent.tool
    async def get_athlete_profile(ctx: RunContext[Deps]) -> str:
        """USE WHEN: any workout/plan/goal request, or when you need the athlete's zones.
        RETURNS: age, weight, VO2 max, lactate-threshold HR/pace, PRs, and available/
        preferred training days. Call once per conversation; anchors all zones and paces."""
        profile = ctx.deps.garmin.get_athlete_profile()
        prs = ctx.deps.garmin.get_personal_records()
        return json.dumps({
            "profile": profile.model_dump(mode="json"),
            "personal_records": [p.model_dump(mode="json") for p in prs],
        }, default=str)

    @agent.tool
    async def get_race_predictions(ctx: RunContext[Deps]) -> str:
        """USE WHEN: user asks about a race, goal time, event, or current fitness level.
        RETURNS: modelled 5k/10k/half/marathon times and paces. Use 10k pace as a proxy
        for threshold pace when the profile's LT pace is unavailable."""
        preds = ctx.deps.garmin.get_race_predictions()
        return json.dumps(preds.model_dump(mode="json"), default=str)

    @agent.tool
    async def get_readiness_and_recovery(ctx: RunContext[Deps], target_date: str | None = None) -> str:
        """USE WHEN: user asks "should I run today", or mentions recovery, fatigue, tired,
        sleep, or before you prescribe ANY hard session.
        RETURNS: training readiness score, HRV status vs baseline, sleep, resting HR,
        recovery time.

        Args:
            target_date: Optional YYYY-MM-DD date (defaults to today).
        """
        target = date.fromisoformat(target_date) if target_date else None
        readiness = ctx.deps.garmin.get_training_readiness(target)
        hrv = ctx.deps.garmin.get_hrv_status(target)
        sleep = ctx.deps.garmin.get_sleep_summary(target)
        return json.dumps({
            "training_readiness": readiness.model_dump(mode="json"),
            "hrv_status": hrv.model_dump(mode="json"),
            "sleep": sleep.model_dump(mode="json"),
        }, default=str)

    @agent.tool
    async def get_fitness_and_load_status(ctx: RunContext[Deps]) -> str:
        """USE WHEN: user asks about fitness, form, training load, injury risk,
        overtraining, or where they are in their training block.
        RETURNS: training status (PRODUCTIVE/PEAKING/OVERREACHING/RECOVERY/etc), VO2 max,
        ACWR and its status, aerobic/anaerobic load balance, weekly running tolerance
        (impact load vs body's ceiling), AND weekly cross-sport load broken down by sport.
        NOTE: ACWR and training status are CROSS-SPORT (they include cycling, strength,
        swimming, etc). Use the sport_load_by_week breakdown to explain WHY total load is
        high or low — e.g. a heavy bike week raises ACWR even if run volume is light."""
        status = ctx.deps.garmin.get_training_status()
        tolerance = ctx.deps.garmin.get_running_tolerance(6)
        sport_load = ctx.deps.garmin.get_sport_load_by_week(4)
        return json.dumps({
            "training_status": status.model_dump(mode="json"),
            "running_tolerance_by_week": [t.model_dump(mode="json") for t in tolerance],
            "cross_sport_load_by_week": [s.model_dump(mode="json") for s in sport_load],
        }, default=str)

    @agent.tool
    async def get_cross_training(ctx: RunContext[Deps], count: int = 30) -> str:
        """USE WHEN: user asks about other sports, cross-training, cycling, swimming,
        strength, or why they feel tired despite low running volume.
        RETURNS: recent activities across ALL sports with per-activity training load and
        training effect.

        Args:
            count: Number of recent activities to fetch across all sports (default 30).
        """
        acts = ctx.deps.garmin.get_recent_activities(min(count, 60))
        return json.dumps([a.model_dump(mode="json") for a in acts], default=str)

    @agent.tool
    async def get_performance_capacities(ctx: RunContext[Deps]) -> str:
        """USE WHEN: user asks about a race/event, event suitability, or their strengths
        and weaknesses.
        RETURNS: endurance score (long-aerobic capacity + classification) and hill score
        (uphill ability, split into strength and endurance). The lower one relative to
        the goal is the limiter to train."""
        endurance = ctx.deps.garmin.get_endurance_score()
        hill = ctx.deps.garmin.get_hill_score()
        return json.dumps({
            "endurance_score": endurance.model_dump(mode="json"),
            "hill_score": hill.model_dump(mode="json"),
        }, default=str)

    @agent.tool
    async def get_body_battery(ctx: RunContext[Deps], target_date: str | None = None) -> str:
        """USE WHEN: assessing same-day fatigue, alongside readiness, before hard work.
        RETURNS: Body Battery current/high/low level and charged vs drained.

        Args:
            target_date: Optional YYYY-MM-DD date (defaults to today).
        """
        target = date.fromisoformat(target_date) if target_date else None
        bb = ctx.deps.garmin.get_body_battery(target)
        return json.dumps(bb.model_dump(mode="json"), default=str)

    @agent.tool
    async def get_shoe_mileage(ctx: RunContext[Deps]) -> str:
        """USE WHEN: user mentions shoes, gear, or injury.
        RETURNS: each running shoe with total km and pct of its retirement limit.
        Flag any shoe with pct_of_max >90% or >700 km for replacement."""
        gear = ctx.deps.garmin.get_gear(running_only=True)
        return json.dumps([g.model_dump(mode="json") for g in gear], default=str)

    return agent
