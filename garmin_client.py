from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from garminconnect import Garmin

from models import (
    AthleteProfile,
    BodyBattery,
    CrossTrainingActivity,
    DailyStats,
    EnduranceScore,
    GearItem,
    HeartRateZone,
    HillScore,
    HRVStatus,
    PersonalRecord,
    RacePredictions,
    RunActivity,
    RunningToleranceWeek,
    SleepSummary,
    SportLoadWeek,
    Split,
    TrainingReadiness,
    TrainingStatus,
    WeeklySummary,
)


class GarminClient:
    """Wrapper around garminconnect for fetching run activities."""

    RUNNING_TYPE_ID = 1  # Garmin activity type ID for running

    TOKEN_STORE_DIR = "~/.garmin_tokens"

    def __init__(self, email: str, password: str) -> None:
        from pathlib import Path

        self._token_dir = Path(self.TOKEN_STORE_DIR).expanduser().resolve()
        self._token_dir.mkdir(parents=True, exist_ok=True)

        self.api = Garmin(
            email,
            password,
            prompt_mfa=self._prompt_mfa,
            retry_attempts=1,
        )

        # The forked garminconnect (0.3.6) tries a 5-strategy login cascade.
        # The two mobile strategies hit sso.garmin.com/mobile/api/login, which
        # persistently returns HTTP 429 for our IP/client id. Those failed
        # attempts run *before* the working web (widget) flow and interfere
        # with Garmin's MFA-email trigger, so the code never gets sent.
        # Skipping them makes login start at the web SSO flow — the same one a
        # browser uses — which reliably sends the MFA email.
        if hasattr(self.api, "client"):
            self.api.client.skip_strategies = {"mobile+cffi", "mobile+requests"}

    @staticmethod
    def _prompt_mfa() -> str:
        print("A verification code was requested. Check your email —")
        print("it may take up to a minute to arrive. Wait for it before typing.")
        return input("Enter Garmin MFA code: ").strip()

    def login(self) -> None:
        self.api.login(tokenstore=str(self._token_dir))

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

    # ------------------------------------------------------------------
    # Physiology / readiness / fitness — the data a coach needs beyond
    # the raw workouts themselves.
    # ------------------------------------------------------------------

    def get_training_readiness(self, target: date | None = None) -> TrainingReadiness:
        target = target or date.today()
        data = self.api.get_training_readiness(target.isoformat())
        d = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
        rt = d.get("recoveryTime")
        return TrainingReadiness(
            calendar_date=_parse_date(d.get("calendarDate")),
            score=d.get("score"),
            level=d.get("level"),
            feedback=d.get("feedbackLong") or d.get("feedbackShort"),
            sleep_score=d.get("sleepScore"),
            sleep_history_factor_pct=d.get("sleepHistoryFactorPercent"),
            recovery_time_hours=round(rt / 60, 1) if rt else None,
            acute_load=d.get("acuteLoad"),
            acwr_factor_pct=d.get("acwrFactorPercent"),
            hrv_factor_pct=d.get("hrvFactorPercent"),
            hrv_status=d.get("hrvFactorFeedback"),
            stress_history_factor_pct=d.get("stressHistoryFactorPercent"),
        )

    def get_hrv_status(self, target: date | None = None) -> HRVStatus:
        target = target or date.today()
        data = self.api.get_hrv_data(target.isoformat()) or {}
        s = (data or {}).get("hrvSummary") or {}
        base = s.get("baseline") or {}
        return HRVStatus(
            calendar_date=_parse_date(s.get("calendarDate")),
            last_night_avg_ms=s.get("lastNightAvg"),
            weekly_avg_ms=s.get("weeklyAvg"),
            baseline_low_ms=base.get("lowUpper"),
            baseline_balanced_low_ms=base.get("balancedLow"),
            baseline_balanced_upper_ms=base.get("balancedUpper"),
            status=s.get("status"),
            feedback=s.get("feedbackPhrase"),
        )

    def get_sleep_summary(self, target: date | None = None) -> SleepSummary:
        target = target or date.today()
        data = self.api.get_sleep_data(target.isoformat()) or {}
        d = data.get("dailySleepDTO") or {}
        rhr = None
        try:
            rhr_data = self.api.get_rhr_day(target.isoformat())
            metrics = (rhr_data or {}).get("allMetrics", {}).get("metricsMap", {})
            rhr_list = metrics.get("WELLNESS_RESTING_HEART_RATE") or []
            if rhr_list:
                rhr = rhr_list[0].get("value")
        except Exception:
            pass
        hrv = None
        raw_hrv = data.get("hrvData") or []
        if raw_hrv:
            vals = [r.get("value") for r in raw_hrv if r.get("value")]
            hrv = round(sum(vals) / len(vals), 1) if vals else None
        return SleepSummary(
            calendar_date=_parse_date(d.get("calendarDate")),
            total_sleep_hours=_secs_to_hours(d.get("sleepTimeSeconds")),
            deep_sleep_hours=_secs_to_hours(d.get("deepSleepSeconds")),
            light_sleep_hours=_secs_to_hours(d.get("lightSleepSeconds")),
            rem_sleep_hours=_secs_to_hours(d.get("remSleepSeconds")),
            awake_hours=_secs_to_hours(d.get("awakeSleepSeconds")),
            sleep_score=(d.get("sleepScores") or {}).get("overall", {}).get("value")
            if isinstance(d.get("sleepScores"), dict) else None,
            sleep_feedback=d.get("sleepScoreFeedback"),
            avg_overnight_hr=d.get("avgHeartRate"),
            avg_overnight_hrv_ms=hrv,
            avg_sleep_stress=d.get("avgSleepStress"),
            avg_respiration=d.get("averageRespirationValue"),
            resting_heart_rate=rhr,
        )

    def get_training_status(self, target: date | None = None) -> TrainingStatus:
        target = target or date.today()
        data = self.api.get_training_status(target.isoformat()) or {}

        vo2 = ((data.get("mostRecentVO2Max") or {}).get("generic") or {})
        heat = ((data.get("mostRecentVO2Max") or {}).get("heatAltitudeAcclimation") or {})

        ts_map = ((data.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {})
        ts = next(iter(ts_map.values()), {}) if ts_map else {}
        acute = ts.get("acuteTrainingLoadDTO") or {}

        lb_map = ((data.get("mostRecentTrainingLoadBalance") or {})
                  .get("metricsTrainingLoadBalanceDTOMap") or {})
        lb = next(iter(lb_map.values()), {}) if lb_map else {}

        return TrainingStatus(
            calendar_date=_parse_date(ts.get("calendarDate") or vo2.get("calendarDate")),
            status=_TRAINING_STATUS_MAP.get(ts.get("trainingStatus"), str(ts.get("trainingStatus")) if ts.get("trainingStatus") is not None else None),
            fitness_trend=ts.get("trainingStatusFeedbackPhrase"),
            vo2_max_running=vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue"),
            acute_load=acute.get("dailyTrainingLoadAcute"),
            acwr_percent=acute.get("acwrPercent"),
            acwr_status=acute.get("acwrStatus"),
            load_focus_feedback=lb.get("trainingBalanceFeedbackPhrase"),
            monthly_load_aerobic_low=_r(lb.get("monthlyLoadAerobicLow")),
            monthly_load_aerobic_low_target_min=lb.get("monthlyLoadAerobicLowTargetMin"),
            monthly_load_aerobic_low_target_max=lb.get("monthlyLoadAerobicLowTargetMax"),
            monthly_load_aerobic_high=_r(lb.get("monthlyLoadAerobicHigh")),
            monthly_load_aerobic_high_target_min=lb.get("monthlyLoadAerobicHighTargetMin"),
            monthly_load_aerobic_high_target_max=lb.get("monthlyLoadAerobicHighTargetMax"),
            monthly_load_anaerobic=_r(lb.get("monthlyLoadAnaerobic")),
            monthly_load_anaerobic_target_min=lb.get("monthlyLoadAnaerobicTargetMin"),
            monthly_load_anaerobic_target_max=lb.get("monthlyLoadAnaerobicTargetMax"),
            heat_acclimation_pct=heat.get("heatAcclimationPercentage"),
            altitude_acclimation_pct=heat.get("altitudeAcclimation"),
        )

    def get_race_predictions(self) -> RacePredictions:
        d = self.api.get_race_predictions() or {}
        return RacePredictions(
            calendar_date=_parse_date(d.get("calendarDate")),
            time_5k=_secs_to_clock(d.get("time5K")),
            time_10k=_secs_to_clock(d.get("time10K")),
            time_half_marathon=_secs_to_clock(d.get("timeHalfMarathon")),
            time_marathon=_secs_to_clock(d.get("timeMarathon")),
            pace_5k=_race_pace(d.get("time5K"), 5.0),
            pace_10k=_race_pace(d.get("time10K"), 10.0),
            pace_half_marathon=_race_pace(d.get("timeHalfMarathon"), 21.0975),
            pace_marathon=_race_pace(d.get("timeMarathon"), 42.195),
        )

    def get_personal_records(self) -> list[PersonalRecord]:
        raw = self.api.get_personal_record() or []
        records = []
        for r in raw:
            if r.get("activityType") != "running":
                continue
            type_id = r.get("typeId")
            label = _PR_TYPE_MAP.get(type_id)
            if not label:
                continue
            value = r.get("value")
            if type_id in _PR_DISTANCE_TYPES:  # value is a time in seconds
                value_str = _secs_to_clock(value)
            else:  # value is a distance in metres (longest run)
                value_str = f"{round((value or 0) / 1000, 2)} km"
            records.append(PersonalRecord(
                label=label,
                value_str=value_str,
                activity_name=r.get("activityName"),
                achieved_on=_parse_date(r.get("activityStartDateTimeLocalFormatted")),
            ))
        return records

    def get_athlete_profile(self) -> AthleteProfile:
        p = self.api.get_user_profile() or {}
        ud = p.get("userData") or {}
        birth = _parse_date(ud.get("birthDate"))
        age = None
        if birth:
            today = date.today()
            age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        lt_speed = ud.get("lactateThresholdSpeed")  # metres per second
        # Garmin stores LT speed in m/s; pace(min/km) = (1000 / (m/s)) / 60.
        lt_pace = None
        if lt_speed and lt_speed > 0:
            pace = (1000 / lt_speed) / 60
            # Sanity guard: only surface physiologically plausible run paces.
            if 2.5 <= pace <= 9.0:
                lt_pace = round(pace, 2)
        return AthleteProfile(
            gender=ud.get("gender"),
            age=age,
            weight_kg=round(ud.get("weight") / 1000, 1) if ud.get("weight") else None,
            height_cm=round(ud.get("height"), 1) if ud.get("height") else None,
            vo2_max_running=ud.get("vo2MaxRunning"),
            lactate_threshold_hr=ud.get("lactateThresholdHeartRate"),
            lactate_threshold_pace_min_per_km=lt_pace,
            available_training_days=ud.get("availableTrainingDays") or [],
            preferred_long_run_days=ud.get("preferredLongTrainingDays") or [],
        )

    def get_running_tolerance(self, weeks: int = 6) -> list[RunningToleranceWeek]:
        end = date.today()
        start = end - timedelta(weeks=weeks)
        raw = self.api.get_running_tolerance(start.isoformat(), end.isoformat(), "weekly") or []
        out = []
        for r in raw:
            tol = r.get("tolerance")
            load = r.get("totalImpactLoad")
            out.append(RunningToleranceWeek(
                week_start=_parse_date(r.get("startOfWeek")),
                week_end=_parse_date(r.get("endOfWeek")),
                total_distance_km=round((r.get("totalDistance") or 0) / 1000, 2),
                impact_load=load,
                tolerance=tol,
                load_vs_tolerance_pct=round(load / tol * 100, 1) if tol else None,
            ))
        out.sort(key=lambda w: w.week_start or date.min)
        return out

    def get_endurance_score(self, target: date | None = None) -> EnduranceScore:
        target = target or date.today()
        d = self.api.get_endurance_score(target.isoformat()) or {}
        score = d.get("overallScore")
        # Derive classification and next threshold directly from the gauge limits
        # Garmin returns — more reliable than the opaque numeric classification code.
        limit_map = {
            "classificationLowerLimitIntermediate": "INTERMEDIATE",
            "classificationLowerLimitTrained": "TRAINED",
            "classificationLowerLimitWellTrained": "WELL_TRAINED",
            "classificationLowerLimitExpert": "EXPERT",
            "classificationLowerLimitSuperior": "SUPERIOR",
            "classificationLowerLimitElite": "ELITE",
        }
        tiers = sorted(
            ((v, limit_map[k]) for k, v in d.items() if k in limit_map),
            key=lambda t: t[0],
        )
        classification = _ENDURANCE_CLASS_MAP.get(d.get("classification"))
        next_thr = None
        if score is not None and tiers:
            below = [name for limit, name in tiers if limit <= score]
            if below:
                classification = below[-1]
            above = [int(limit) for limit, _ in tiers if limit > score]
            next_thr = above[0] if above else None
        return EnduranceScore(
            calendar_date=_parse_date(d.get("calendarDate")),
            overall_score=score,
            classification=classification,
            next_threshold=next_thr,
        )

    def get_hill_score(self, target: date | None = None) -> HillScore:
        target = target or date.today()
        d = self.api.get_hill_score(target.isoformat()) or {}
        return HillScore(
            calendar_date=_parse_date(d.get("calendarDate")),
            overall_score=d.get("overallScore"),
            strength_score=d.get("strengthScore"),
            endurance_score=d.get("enduranceScore"),
            classification=_HILL_CLASS_MAP.get(d.get("hillScoreClassificationId")),
        )

    def get_body_battery(self, target: date | None = None) -> BodyBattery:
        target = target or date.today()
        data = self.api.get_body_battery(target.isoformat()) or []
        d = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
        levels = [
            pt[1] for pt in (d.get("bodyBatteryValuesArray") or [])
            if isinstance(pt, list) and len(pt) > 1 and pt[1] is not None
        ]
        return BodyBattery(
            calendar_date=_parse_date(d.get("date")),
            current_level=levels[-1] if levels else None,
            highest_level=max(levels) if levels else None,
            lowest_level=min(levels) if levels else None,
            charged=d.get("charged"),
            drained=d.get("drained"),
        )

    def get_gear(self, running_only: bool = True) -> list[GearItem]:
        prof = self.api.get_user_profile() or {}
        uid = prof.get("id")
        if uid is None:
            return []
        raw = self.api.get_gear(uid) or []
        items = []
        for gr in raw:
            gtype = gr.get("gearTypeName")
            if running_only and gtype != "Shoes":
                continue
            name = (gr.get("displayName") or gr.get("customMakeModel")
                    or gr.get("gearModelName") or "Unknown")
            total_km = None
            total_acts = None
            try:
                stats = self.api.get_gear_stats(gr.get("uuid")) or {}
                total_km = round((stats.get("totalDistance") or 0) / 1000, 1)
                total_acts = stats.get("totalActivities")
            except Exception:
                pass
            max_m = gr.get("maximumMeters") or 0
            max_km = round(max_m / 1000, 1) if max_m else None
            pct = round(total_km / max_km * 100, 1) if (max_km and total_km is not None) else None
            items.append(GearItem(
                name=name,
                gear_type=gtype,
                status=gr.get("gearStatusName"),
                total_distance_km=total_km,
                total_activities=total_acts,
                max_distance_km=max_km,
                pct_of_max=pct,
            ))
        return items

    # ------------------------------------------------------------------
    # Cross-sport load — the athlete cross-trains, so total fatigue is not
    # explained by runs alone. Garmin's ACWR/training status already blend
    # all sports; these methods expose the underlying non-running load.
    # ------------------------------------------------------------------

    def get_recent_activities(self, count: int = 30) -> list[CrossTrainingActivity]:
        """All activities (every sport), most recent first."""
        raw = self.api.get_activities(0, count) or []
        return [self._parse_cross_activity(a) for a in raw]

    def _parse_cross_activity(self, a: dict) -> CrossTrainingActivity:
        atype = a.get("activityType") or {}
        start = a.get("startTimeLocal") or a.get("startTimeGMT")
        act_date = None
        if start:
            try:
                act_date = datetime.fromisoformat(str(start)).date()
            except (ValueError, TypeError):
                act_date = _parse_date(start)
        return CrossTrainingActivity(
            activity_id=a["activityId"],
            sport=atype.get("typeKey") or "unknown",
            activity_name=a.get("activityName") or "",
            activity_date=act_date,
            distance_km=round((a.get("distance") or 0) / 1000, 2),
            duration_minutes=round((a.get("duration") or 0) / 60, 1),
            training_load=_r(a.get("activityTrainingLoad")),
            aerobic_te=_r(a.get("aerobicTrainingEffect")),
            anaerobic_te=_r(a.get("anaerobicTrainingEffect")),
        )

    def get_sport_load_by_week(self, weeks: int = 4) -> list[SportLoadWeek]:
        """Total training load per ISO week, broken down by sport bucket."""
        # Fetch generously to cover the window across all sports.
        raw = self.api.get_activities(0, max(weeks * 12, 40)) or []
        acts = [self._parse_cross_activity(a) for a in raw]
        cutoff = date.today() - timedelta(weeks=weeks)
        acts = [a for a in acts if a.activity_date and a.activity_date >= cutoff]

        weeks_map: dict[tuple[int, int], list[CrossTrainingActivity]] = {}
        for a in acts:
            iso = a.activity_date.isocalendar()
            weeks_map.setdefault((iso[0], iso[1]), []).append(a)

        out = []
        for (yr, wk), wa in sorted(weeks_map.items()):
            week_start = date.fromisocalendar(yr, wk, 1)
            buckets = {"running": 0.0, "cycling": 0.0, "strength": 0.0, "swimming": 0.0, "other": 0.0}
            counts: dict[str, int] = {}
            total_dur = 0.0
            for a in wa:
                load = a.training_load or 0
                total_dur += a.duration_minutes or 0
                counts[a.sport] = counts.get(a.sport, 0) + 1
                buckets[_sport_bucket(a.sport)] += load
            total = sum(buckets.values())
            out.append(SportLoadWeek(
                week_start=week_start,
                week_end=week_start + timedelta(days=6),
                total_load=round(total, 1),
                total_duration_minutes=round(total_dur, 1),
                running_load=round(buckets["running"], 1),
                cycling_load=round(buckets["cycling"], 1),
                strength_load=round(buckets["strength"], 1),
                swimming_load=round(buckets["swimming"], 1),
                other_load=round(buckets["other"], 1),
                running_load_pct=round(buckets["running"] / total * 100, 1) if total else None,
                sessions_by_sport=counts,
            ))
        return out

    # ------------------------------------------------------------------
    # Daily wellness stats (steps, calories, stress, intensity minutes)
    # ------------------------------------------------------------------

    def get_daily_stats(self, target: date | None = None) -> DailyStats:
        """Fetch daily wellness summary (steps, calories, floors, stress, etc.).

        Uses get_stats (usersummary/daily) with fallback field aliases, plus
        the dedicated get_daily_steps endpoint to backfill steps/goal when the
        summary returns them as null.
        """
        target = target or date.today()
        d = self.api.get_stats(target.isoformat()) or {}

        # Helper: try multiple key aliases, return first non-None
        def _first(keys: list[str]) -> Any:
            for k in keys:
                v = d.get(k)
                if v is not None:
                    return v
            return None

        total_steps = _first(["totalSteps", "wellnessTotalSteps"])
        step_goal = _first(["dailyStepGoal", "stepGoal", "wellnessTotalDailyStepGoal"])
        active_cal = _first(["activeKilocalories", "wellnessActiveKilocalories"])
        total_cal = _first(["totalKilocalories", "wellnessKilocalories"])
        mod_int = _first(["moderateIntensityMinutes", "wellnessModerateIntensityMinutes"])
        vig_int = _first(["vigorousIntensityMinutes", "wellnessVigorousIntensityMinutes"])
        int_goal = _first(["intensityMinutesGoal", "wellnessIntensityMinutesGoal"])
        floors = _first(["floorsAscended", "wellnessFloorsAscended"])
        floors_goal = _first(["floorsAscendedGoal", "userFloorsAscendedGoal", "wellnessFloorsAscendedGoal"])
        rhr = _first(["restingHeartRate", "wellnessRestingHeartRate"])
        min_hr = _first(["minHeartRate", "wellnessMinHeartRate"])
        max_hr = _first(["maxHeartRate", "wellnessMaxHeartRate"])
        avg_stress = _first(["averageStressLevel", "wellnessAverageStressLevel"])
        max_stress = _first(["maxStressLevel", "wellnessMaxStressLevel"])
        dist_m = _first(["totalDistanceMeters", "wellnessTotalDistanceMeters"]) or 0

        # Backfill steps + goal from dedicated steps endpoint if summary was null
        if total_steps is None or step_goal is None:
            try:
                steps_data = self.api.get_daily_steps(
                    target.isoformat(), target.isoformat()
                )
                if steps_data and isinstance(steps_data, list) and steps_data:
                    row = steps_data[0]
                    if total_steps is None:
                        total_steps = row.get("totalSteps")
                    if step_goal is None:
                        step_goal = row.get("stepGoal")
                    if dist_m == 0:
                        dist_m = row.get("totalDistance") or 0
            except Exception:
                pass

        return DailyStats(
            calendar_date=_parse_date(d.get("calendarDate")),
            total_steps=total_steps,
            step_goal=step_goal,
            total_distance_km=round(dist_m / 1000, 2) or None,
            floors_climbed=floors,
            floors_goal=floors_goal,
            active_calories=active_cal,
            total_calories=total_cal,
            moderate_intensity_minutes=mod_int,
            vigorous_intensity_minutes=vig_int,
            intensity_minutes_goal=int_goal,
            avg_stress_level=avg_stress,
            max_stress_level=max_stress,
            resting_heart_rate=rhr,
            min_heart_rate=min_hr,
            max_heart_rate=max_hr,
        )

    def get_steps_last_days(self, days: int = 7) -> list[dict]:
        """Return [{date, steps, goal}] for the last N days, oldest first."""
        end = date.today()
        start = end - timedelta(days=days - 1)
        try:
            raw = self.api.get_daily_steps(
                start.isoformat(), end.isoformat()
            ) or []
            results = []
            for row in raw:
                results.append({
                    "date": row.get("calendarDate", ""),
                    "steps": row.get("totalSteps") or 0,
                    "goal": row.get("stepGoal") or 0,
                })
            # Pad if fewer rows returned
            if len(results) < days:
                existing_dates = {r["date"] for r in results}
                for i in range(days - 1, -1, -1):
                    d = (end - timedelta(days=i)).isoformat()
                    if d not in existing_dates:
                        results.append({"date": d, "steps": 0, "goal": 0})
            results.sort(key=lambda r: r["date"])
            return results
        except Exception:
            # Fallback: individual calls
            results = []
            for i in range(days - 1, -1, -1):
                d = end - timedelta(days=i)
                try:
                    stats = self.api.get_stats(d.isoformat()) or {}
                    results.append({
                        "date": d.isoformat(),
                        "steps": stats.get("totalSteps") or stats.get("wellnessTotalSteps") or 0,
                        "goal": stats.get("dailyStepGoal") or stats.get("stepGoal") or 0,
                    })
                except Exception:
                    results.append({"date": d.isoformat(), "steps": 0, "goal": 0})
            return results


def _sport_bucket(sport: str) -> str:
    s = (sport or "").lower()
    if "run" in s:
        return "running"
    if "cycl" in s or "bik" in s:
        return "cycling"
    if "strength" in s or "cardio" in s or "training" in s:
        return "strength"
    if "swim" in s:
        return "swimming"
    return "other"


_ENDURANCE_CLASS_MAP = {
    1: "UNTRAINED", 2: "NOVICE", 3: "INTERMEDIATE", 4: "TRAINED",
    5: "WELL_TRAINED", 6: "EXPERT", 7: "SUPERIOR", 8: "ELITE",
}

_HILL_CLASS_MAP = {
    1: "UNTRAINED", 2: "NOVICE", 3: "COMPETENT", 4: "GOOD",
    5: "STRONG", 6: "ELITE",
}

_TRAINING_STATUS_MAP = {
    0: "NO_STATUS", 1: "DETRAINING", 2: "UNPRODUCTIVE", 3: "MAINTAINING",
    4: "PRODUCTIVE", 5: "PEAKING", 6: "OVERREACHING", 7: "RECOVERY",
    8: "UNPRODUCTIVE", 9: "STRAINED",
}

_PR_TYPE_MAP = {
    1: "1 km", 2: "1 mile", 3: "5 km", 4: "10 km",
    7: "Half Marathon", 8: "Marathon", 9: "Longest run",
    12: "Longest run",
}
_PR_DISTANCE_TYPES = {1, 2, 3, 4, 7, 8}


def _parse_date(val) -> date | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)).date()
    except (ValueError, TypeError):
        try:
            return date.fromisoformat(str(val)[:10])
        except (ValueError, TypeError):
            return None


def _secs_to_hours(val) -> float | None:
    return round(val / 3600, 2) if val else None


def _secs_to_clock(secs) -> str | None:
    if not secs:
        return None
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _race_pace(secs, dist_km: float) -> str | None:
    if not secs or dist_km <= 0:
        return None
    pace = (secs / 60) / dist_km
    mins = int(pace)
    s = int(round((pace - mins) * 60))
    if s == 60:
        mins, s = mins + 1, 0
    return f"{mins}:{s:02d} /km"


def _r(val) -> float | None:
    return round(val, 1) if val is not None else None


def _double_cadence(val: float | int | None) -> int | None:
    """Garmin sometimes reports cadence as steps per minute for one foot. Double it."""
    if val is None:
        return None
    # If cadence seems too low (under 100), it's likely single-foot
    v = int(val)
    return v * 2 if v < 100 else v
