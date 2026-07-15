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
