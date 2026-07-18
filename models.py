from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel, Field


class Split(BaseModel):
    """A single km/mile split from a run."""
    split_number: int
    distance_km: float
    elapsed_seconds: float
    pace_min_per_km: float | None = None
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    avg_cadence: int | None = None


class HeartRateZone(BaseModel):
    """Time spent in a single heart rate zone."""
    zone: int = Field(description="Zone number 1-5")
    low_bpm: int | None = None
    high_bpm: int | None = None
    duration_seconds: float = 0
    percentage: float = Field(0, description="Percentage of total time in this zone")


class RunActivity(BaseModel):
    """A single run activity from Garmin Connect."""
    activity_id: int
    activity_name: str = ""
    start_time: datetime | None = None
    activity_date: date | None = None
    distance_km: float = 0
    duration_seconds: float = 0
    avg_pace_min_per_km: float | None = None
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    avg_cadence: int | None = None
    max_cadence: int | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    calories: float | None = None
    avg_stride_length_m: float | None = None
    training_effect_aerobic: float | None = None
    training_effect_anaerobic: float | None = None
    vo2_max: float | None = None
    splits: list[Split] = Field(default_factory=list)
    hr_zones: list[HeartRateZone] = Field(default_factory=list)

    @property
    def pace_str(self) -> str:
        if self.avg_pace_min_per_km is None:
            return "N/A"
        mins = int(self.avg_pace_min_per_km)
        secs = int((self.avg_pace_min_per_km - mins) * 60)
        return f"{mins}:{secs:02d} /km"

    @property
    def duration_str(self) -> str:
        h = int(self.duration_seconds // 3600)
        m = int((self.duration_seconds % 3600) // 60)
        s = int(self.duration_seconds % 60)
        if h > 0:
            return f"{h}h {m}m {s}s"
        return f"{m}m {s}s"


class WeeklySummary(BaseModel):
    """Aggregated weekly running summary."""
    week_start: date
    week_end: date
    total_runs: int = 0
    total_distance_km: float = 0
    total_duration_seconds: float = 0
    avg_pace_min_per_km: float | None = None
    avg_heart_rate: float | None = None
    total_elevation_gain_m: float = 0
    total_calories: float = 0


class TrainingReadiness(BaseModel):
    """Daily training readiness snapshot — a coach's morning check-in on the athlete."""
    calendar_date: date | None = None
    score: int | None = Field(None, description="Overall readiness 0-100")
    level: str | None = Field(None, description="e.g. LOW, MODERATE, HIGH, PRIME")
    feedback: str | None = None
    sleep_score: int | None = None
    sleep_history_factor_pct: int | None = None
    recovery_time_hours: float | None = None
    acute_load: int | None = None
    acwr_factor_pct: int | None = Field(None, description="Acute:chronic workload ratio factor")
    hrv_factor_pct: int | None = None
    hrv_status: str | None = None
    stress_history_factor_pct: int | None = None


class HRVStatus(BaseModel):
    """Heart rate variability status — the single best day-to-day recovery signal."""
    calendar_date: date | None = None
    last_night_avg_ms: int | None = None
    weekly_avg_ms: int | None = None
    baseline_low_ms: int | None = None
    baseline_balanced_low_ms: int | None = None
    baseline_balanced_upper_ms: int | None = None
    status: str | None = Field(None, description="e.g. BALANCED, UNBALANCED, LOW, POOR")
    feedback: str | None = None


class SleepSummary(BaseModel):
    """Overnight sleep and recovery physiology."""
    calendar_date: date | None = None
    total_sleep_hours: float | None = None
    deep_sleep_hours: float | None = None
    light_sleep_hours: float | None = None
    rem_sleep_hours: float | None = None
    awake_hours: float | None = None
    sleep_score: int | None = None
    sleep_feedback: str | None = None
    avg_overnight_hr: float | None = None
    avg_overnight_hrv_ms: float | None = None
    avg_sleep_stress: float | None = None
    avg_respiration: float | None = None
    resting_heart_rate: int | None = None


class TrainingStatus(BaseModel):
    """Fitness trend and training load balance — the macro view of the training block."""
    calendar_date: date | None = None
    status: str | None = Field(None, description="e.g. PRODUCTIVE, MAINTAINING, PEAKING, OVERREACHING, DETRAINING, RECOVERY")
    fitness_trend: str | None = None
    vo2_max_running: float | None = None
    acute_load: int | None = Field(None, description="7-day exponentially weighted training load")
    acwr_percent: int | None = Field(None, description="Acute:chronic workload ratio as a percentage")
    acwr_status: str | None = Field(None, description="LOW / OPTIMAL / HIGH — injury-risk sweet spot is roughly 80-130%")
    # Load focus over the trailing 4 weeks vs Garmin's target ranges
    load_focus_feedback: str | None = None
    monthly_load_aerobic_low: float | None = None
    monthly_load_aerobic_low_target_min: float | None = None
    monthly_load_aerobic_low_target_max: float | None = None
    monthly_load_aerobic_high: float | None = None
    monthly_load_aerobic_high_target_min: float | None = None
    monthly_load_aerobic_high_target_max: float | None = None
    monthly_load_anaerobic: float | None = None
    monthly_load_anaerobic_target_min: float | None = None
    monthly_load_anaerobic_target_max: float | None = None
    heat_acclimation_pct: int | None = None
    altitude_acclimation_pct: int | None = None


class RacePredictions(BaseModel):
    """Garmin's modelled race times, derived from VO2 max and training history."""
    calendar_date: date | None = None
    time_5k: str | None = None
    time_10k: str | None = None
    time_half_marathon: str | None = None
    time_marathon: str | None = None
    pace_5k: str | None = None
    pace_10k: str | None = None
    pace_half_marathon: str | None = None
    pace_marathon: str | None = None


class PersonalRecord(BaseModel):
    """A single running personal best."""
    label: str
    value_str: str
    activity_name: str | None = None
    achieved_on: date | None = None


class AthleteProfile(BaseModel):
    """Static physiological profile used to anchor zone and pace prescriptions."""
    gender: str | None = None
    age: int | None = None
    weight_kg: float | None = None
    height_cm: float | None = None
    vo2_max_running: float | None = None
    lactate_threshold_hr: int | None = None
    lactate_threshold_pace_min_per_km: float | None = None
    available_training_days: list[str] = Field(default_factory=list)
    preferred_long_run_days: list[str] = Field(default_factory=list)


class RunningToleranceWeek(BaseModel):
    """Weekly running-specific structural load vs the athlete's tolerance ceiling."""
    week_start: date | None = None
    week_end: date | None = None
    total_distance_km: float | None = None
    impact_load: int | None = None
    tolerance: int | None = Field(None, description="Estimated weekly load the body can absorb")
    load_vs_tolerance_pct: float | None = None


class EnduranceScore(BaseModel):
    """Garmin endurance score — long-duration aerobic capacity built from sustained load."""
    calendar_date: date | None = None
    overall_score: int | None = None
    classification: str | None = Field(None, description="e.g. TRAINED, WELL_TRAINED, EXPERT, SUPERIOR, ELITE")
    next_threshold: int | None = Field(None, description="Score needed to reach the next classification")


class HillScore(BaseModel):
    """Garmin hill score — the ability to run uphill (strength + endurance components)."""
    calendar_date: date | None = None
    overall_score: int | None = None
    strength_score: int | None = None
    endurance_score: int | None = None
    classification: str | None = None


class BodyBattery(BaseModel):
    """Daily energy reserves — a real-time gauge of accumulated fatigue vs recovery."""
    calendar_date: date | None = None
    current_level: int | None = Field(None, description="Most recent 0-100 reading")
    highest_level: int | None = None
    lowest_level: int | None = None
    charged: int | None = Field(None, description="Total charged (recovery) over the day")
    drained: int | None = Field(None, description="Total drained (stress/exertion) over the day")


class GearItem(BaseModel):
    """A piece of gear (typically running shoes) with accumulated mileage."""
    name: str
    gear_type: str | None = None
    status: str | None = None
    total_distance_km: float | None = None
    total_activities: int | None = None
    max_distance_km: float | None = Field(None, description="User-set retirement mileage, if any")
    pct_of_max: float | None = Field(None, description="How worn the shoe is vs its retirement limit")


class CrossTrainingActivity(BaseModel):
    """A single non-running (or running) activity — the full training picture."""
    activity_id: int
    sport: str = Field(description="e.g. cycling, mountain_biking, strength_training, lap_swimming, running")
    activity_name: str = ""
    activity_date: date | None = None
    distance_km: float = 0
    duration_minutes: float = 0
    training_load: float | None = Field(None, description="Garmin activity training load (device units)")
    aerobic_te: float | None = None
    anaerobic_te: float | None = None


class SportLoadWeek(BaseModel):
    """One week of total training load broken down by sport — reveals cross-sport fatigue."""
    week_start: date | None = None
    week_end: date | None = None
    total_load: float = 0
    total_duration_minutes: float = 0
    running_load: float = 0
    cycling_load: float = Field(0, description="Includes road and mountain biking")
    strength_load: float = 0
    swimming_load: float = 0
    other_load: float = 0
    running_load_pct: float | None = Field(None, description="Share of weekly load that is running")
    sessions_by_sport: dict[str, int] = Field(default_factory=dict)


class DailyStats(BaseModel):
    """Daily wellness stats from Garmin (steps, calories, stress, etc.)."""
    calendar_date: date | None = None
    total_steps: int | None = None
    step_goal: int | None = None
    total_distance_km: float | None = None
    floors_climbed: float | None = None
    floors_goal: float | None = None
    active_calories: float | None = None
    total_calories: float | None = None
    moderate_intensity_minutes: int | None = None
    vigorous_intensity_minutes: int | None = None
    intensity_minutes_goal: int | None = None
    avg_stress_level: int | None = None
    max_stress_level: int | None = None
    resting_heart_rate: int | None = None
    min_heart_rate: int | None = None
    max_heart_rate: int | None = None
