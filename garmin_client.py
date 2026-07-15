from __future__ import annotations

from datetime import date, datetime, timedelta

from garminconnect import Garmin

from models import HeartRateZone, RunActivity, Split, WeeklySummary


class GarminClient:
    """Wrapper around garminconnect for fetching run activities."""

    RUNNING_TYPE_ID = 1  # Garmin activity type ID for running

    def __init__(self, email: str, password: str) -> None:
        self.api = Garmin(email, password)

    def login(self) -> None:
        self.api.login()

    def _parse_activity(self, raw: dict) -> RunActivity:
        distance_km = (raw.get("distance") or 0) / 1000
        duration_s = raw.get("duration") or 0
        avg_pace = None
        if distance_km > 0 and duration_s > 0:
            avg_pace = round((duration_s / 60) / distance_km, 2)

        start = raw.get("startTimeLocal") or raw.get("startTimeGMT")
        start_dt = None
        act_date = None
        if start:
            try:
                start_dt = datetime.fromisoformat(str(start))
                act_date = start_dt.date()
            except (ValueError, TypeError):
                pass

        return RunActivity(
            activity_id=raw["activityId"],
            activity_name=raw.get("activityName") or "",
            start_time=start_dt,
            activity_date=act_date,
            distance_km=round(distance_km, 2),
            duration_seconds=round(duration_s, 1),
            avg_pace_min_per_km=avg_pace,
            avg_heart_rate=raw.get("averageHR"),
            max_heart_rate=raw.get("maxHR"),
            avg_cadence=_double_cadence(raw.get("averageRunningCadenceInStepsPerMinute")),
            max_cadence=_double_cadence(raw.get("maxRunningCadenceInStepsPerMinute")),
            elevation_gain_m=raw.get("elevationGain"),
            elevation_loss_m=raw.get("elevationLoss"),
            calories=raw.get("calories"),
            avg_stride_length_m=raw.get("avgStrideLength"),
            training_effect_aerobic=raw.get("aerobicTrainingEffect"),
            training_effect_anaerobic=raw.get("anaerobicTrainingEffect"),
            vo2_max=raw.get("vO2MaxValue"),
        )

    def _enrich_with_splits(self, activity: RunActivity) -> RunActivity:
        try:
            splits_raw = self.api.get_activity_splits(activity.activity_id)
            lap_dtos = splits_raw.get("lapDTOs") or []
            splits = []
            for i, lap in enumerate(lap_dtos, 1):
                dist_km = (lap.get("distance") or 0) / 1000
                elapsed = lap.get("duration") or 0
                pace = None
                if dist_km > 0 and elapsed > 0:
                    pace = round((elapsed / 60) / dist_km, 2)
                splits.append(Split(
                    split_number=i,
                    distance_km=round(dist_km, 2),
                    elapsed_seconds=round(elapsed, 1),
                    pace_min_per_km=pace,
                    avg_heart_rate=lap.get("averageHR"),
                    max_heart_rate=lap.get("maxHR"),
                    avg_cadence=_double_cadence(lap.get("averageRunCadence")),
                ))
            activity.splits = splits
        except Exception:
            pass
        return activity

    def _enrich_with_hr_zones(self, activity: RunActivity) -> RunActivity:
        try:
            zones_raw = self.api.get_activity_hr_in_timezones(activity.activity_id)
            if zones_raw:
                total_secs = sum(z.get("secsInZone", 0) for z in zones_raw)
                hr_zones = []
                for i, z in enumerate(zones_raw, 1):
                    secs = z.get("secsInZone", 0)
                    pct = round((secs / total_secs * 100) if total_secs > 0 else 0, 1)
                    hr_zones.append(HeartRateZone(
                        zone=i,
                        low_bpm=z.get("zoneLowBoundary"),
                        high_bpm=z.get("zoneHighBoundary"),
                        duration_seconds=secs,
                        percentage=pct,
                    ))
                activity.hr_zones = hr_zones
        except Exception:
            pass
        return activity

    def get_recent_runs(self, count: int = 10) -> list[RunActivity]:
        raw_activities = self.api.get_activities(0, count * 3)  # fetch extra, filter to runs
        runs = []
        for raw in raw_activities:
            type_id = (raw.get("activityType") or {}).get("typeId")
            if type_id == self.RUNNING_TYPE_ID:
                runs.append(self._parse_activity(raw))
            if len(runs) >= count:
                break
        return runs

    def get_runs_in_range(self, start_date: date, end_date: date) -> list[RunActivity]:
        raw_activities = self.api.get_activities_by_date(
            start_date.isoformat(), end_date.isoformat(), "running"
        )
        return [self._parse_activity(r) for r in raw_activities]

    def get_activity_details(self, activity_id: int) -> RunActivity:
        raw = self.api.get_activity(activity_id)
        activity = self._parse_activity(raw)
        self._enrich_with_splits(activity)
        self._enrich_with_hr_zones(activity)
        return activity

    def get_weekly_summaries(self, weeks: int = 4) -> list[WeeklySummary]:
        end = date.today()
        start = end - timedelta(weeks=weeks)
        runs = self.get_runs_in_range(start, end)

        # Group by ISO week
        weeks_map: dict[tuple[int, int], list[RunActivity]] = {}
        for run in runs:
            if run.activity_date:
                iso = run.activity_date.isocalendar()
                key = (iso[0], iso[1])
                weeks_map.setdefault(key, []).append(run)

        summaries = []
        for (yr, wk), week_runs in sorted(weeks_map.items()):
            # Monday of that ISO week
            week_start = date.fromisocalendar(yr, wk, 1)
            week_end = week_start + timedelta(days=6)
            total_dist = sum(r.distance_km for r in week_runs)
            total_dur = sum(r.duration_seconds for r in week_runs)
            hrs = [r.avg_heart_rate for r in week_runs if r.avg_heart_rate]
            avg_pace = round((total_dur / 60) / total_dist, 2) if total_dist > 0 else None
            summaries.append(WeeklySummary(
                week_start=week_start,
                week_end=week_end,
                total_runs=len(week_runs),
                total_distance_km=round(total_dist, 2),
                total_duration_seconds=round(total_dur, 1),
                avg_pace_min_per_km=avg_pace,
                avg_heart_rate=round(sum(hrs) / len(hrs), 1) if hrs else None,
                total_elevation_gain_m=sum(r.elevation_gain_m or 0 for r in week_runs),
                total_calories=sum(r.calories or 0 for r in week_runs),
            ))
        return summaries


def _double_cadence(val: float | int | None) -> int | None:
    """Garmin sometimes reports cadence as steps per minute for one foot. Double it."""
    if val is None:
        return None
    # If cadence seems too low (under 100), it's likely single-foot
    v = int(val)
    return v * 2 if v < 100 else v
