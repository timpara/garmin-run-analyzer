"""KPI dashboard — runs alongside the Discord bot in the same process."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from garmin_client import GarminClient


# ---------------------------------------------------------------------------
# Workout cache — written by the Discord bot's daily-workout task,
# read by the dashboard (no extra LLM calls).
# ---------------------------------------------------------------------------

@dataclass
class WorkoutCache:
    """Thread-safe cache for the daily AI workout suggestion."""
    text: str = ""
    generated_date: date | None = None

    def update(self, text: str) -> None:
        self.text = text
        self.generated_date = date.today()

    def get(self) -> dict:
        if self.generated_date == date.today() and self.text:
            return {"text": self.text, "available": True}
        return {"text": "", "available": False}


# ---------------------------------------------------------------------------
# API data cache — avoid hammering Garmin on every page refresh
# ---------------------------------------------------------------------------

@dataclass
class _DataCache:
    data: dict | None = None
    timestamp: float = 0
    ttl: float = 300  # 5 minutes


_cache = _DataCache()


# ---------------------------------------------------------------------------
# Data collection — all blocking Garmin calls wrapped in to_thread
# ---------------------------------------------------------------------------

async def _collect(garmin: GarminClient) -> dict[str, Any]:
    """Fetch all KPIs from Garmin. Returns a JSON-serialisable dict."""

    def _fetch() -> dict[str, Any]:
        out: dict[str, Any] = {}

        # Today's wellness
        try:
            stats = garmin.get_daily_stats()
            out["daily_stats"] = stats.model_dump()
        except Exception as e:
            out["daily_stats"] = {"error": str(e)}

        # Body battery
        try:
            bb = garmin.get_body_battery()
            out["body_battery"] = bb.model_dump()
        except Exception as e:
            out["body_battery"] = {"error": str(e)}

        # Training readiness
        try:
            tr = garmin.get_training_readiness()
            out["readiness"] = tr.model_dump()
        except Exception as e:
            out["readiness"] = {"error": str(e)}

        # HRV
        try:
            hrv = garmin.get_hrv_status()
            out["hrv"] = hrv.model_dump()
        except Exception as e:
            out["hrv"] = {"error": str(e)}

        # Sleep
        try:
            sleep = garmin.get_sleep_summary()
            out["sleep"] = sleep.model_dump()
        except Exception as e:
            out["sleep"] = {"error": str(e)}

        # Training status
        try:
            ts = garmin.get_training_status()
            out["training_status"] = ts.model_dump()
        except Exception as e:
            out["training_status"] = {"error": str(e)}

        # Race predictions
        try:
            rp = garmin.get_race_predictions()
            out["race_predictions"] = rp.model_dump()
        except Exception as e:
            out["race_predictions"] = {"error": str(e)}

        # Recent activities (all sports, last 14)
        try:
            acts = garmin.get_recent_activities(14)
            out["recent_activities"] = [a.model_dump() for a in acts]
        except Exception as e:
            out["recent_activities"] = {"error": str(e)}

        # Sport load by week (4 weeks)
        try:
            sl = garmin.get_sport_load_by_week(4)
            out["sport_load_weeks"] = [w.model_dump() for w in sl]
        except Exception as e:
            out["sport_load_weeks"] = {"error": str(e)}

        # Steps last 7 days
        try:
            out["steps_7d"] = garmin.get_steps_last_days(7)
        except Exception as e:
            out["steps_7d"] = {"error": str(e)}

        # Gear (shoes)
        try:
            gear = garmin.get_gear(running_only=False)
            out["gear"] = [g.model_dump() for g in gear]
        except Exception as e:
            out["gear"] = {"error": str(e)}

        return out

    return await asyncio.to_thread(_fetch)


# ---------------------------------------------------------------------------
# HTML template — self-contained, Chart.js from CDN
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Training Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e4e4e7; --muted: #9ca3af; --accent: #6366f1;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444;
    --blue: #3b82f6; --orange: #f97316; --purple: #a855f7;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--text); line-height: 1.5;
         padding: 1rem; max-width: 1400px; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin-bottom: .25rem; }
  .subtitle { color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; }
  .grid { display: grid; gap: 1rem; }
  .grid-4 { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
  .grid-2 { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: .75rem; padding: 1rem; }
  .card h3 { font-size: .75rem; text-transform: uppercase; color: var(--muted);
             letter-spacing: .05em; margin-bottom: .5rem; }
  .stat { font-size: 1.75rem; font-weight: 700; }
  .stat-sm { font-size: .85rem; color: var(--muted); }
  .stat-unit { font-size: .9rem; font-weight: 400; color: var(--muted); }
  .badge { display: inline-block; padding: .15rem .5rem; border-radius: .25rem;
           font-size: .75rem; font-weight: 600; }
  .badge-green { background: #22c55e22; color: var(--green); }
  .badge-yellow { background: #eab30822; color: var(--yellow); }
  .badge-red { background: #ef444422; color: var(--red); }
  .badge-blue { background: #3b82f622; color: var(--blue); }
  section { margin-bottom: 1.5rem; }
  section > h2 { font-size: 1.1rem; margin-bottom: .75rem; border-bottom: 1px solid var(--border);
                 padding-bottom: .5rem; }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  th { text-align: left; color: var(--muted); font-weight: 500; padding: .5rem; border-bottom: 1px solid var(--border); }
  td { padding: .5rem; border-bottom: 1px solid var(--border); }
  .sport-icon { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: .4rem; }
  .sport-running { background: var(--green); }
  .sport-cycling { background: var(--blue); }
  .sport-strength { background: var(--orange); }
  .sport-swimming { background: var(--purple); }
  .sport-other { background: var(--muted); }
  .workout-box { background: var(--surface); border: 1px solid var(--accent);
                 border-radius: .75rem; padding: 1rem; white-space: pre-wrap;
                 font-size: .9rem; line-height: 1.6; }
  .workout-box .empty { color: var(--muted); font-style: italic; }
  canvas { max-height: 250px; }
  .progress-bar { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; margin-top: .25rem; }
  .progress-fill { height: 100%; border-radius: 3px; }
  .loading { text-align: center; padding: 3rem; color: var(--muted); }
  @media (max-width: 600px) { .stat { font-size: 1.3rem; } body { padding: .5rem; } }
</style>
</head>
<body>
<h1>Training Dashboard</h1>
<p class="subtitle" id="timestamp">Loading...</p>
<div id="content"><p class="loading">Fetching data from Garmin...</p></div>
<script>
const SPORT_COLORS = {running:'#22c55e',cycling:'#3b82f6',strength:'#f97316',swimming:'#a855f7',other:'#9ca3af'};

function sportBucket(s) {
  s = (s||'').toLowerCase();
  if (s.includes('run')) return 'running';
  if (s.includes('cycl') || s.includes('bik')) return 'cycling';
  if (s.includes('strength') || s.includes('cardio') || s.includes('training')) return 'strength';
  if (s.includes('swim')) return 'swimming';
  return 'other';
}

function sportLabel(s) {
  const map = {running:'Run',cycling:'Ride',strength:'Strength',swimming:'Swim',other:'Other'};
  return map[sportBucket(s)] || s;
}

function v(val, fallback='-') { return val != null ? val : fallback; }
function pct(val, goal) { return (val && goal) ? Math.min(Math.round(val/goal*100),999) : 0; }

function readinessColor(score) {
  if (!score) return 'yellow';
  if (score >= 60) return 'green';
  if (score >= 40) return 'yellow';
  return 'red';
}

function badge(text, color) { return `<span class="badge badge-${color}">${text}</span>`; }

function progressBar(val, goal, color='var(--accent)') {
  const p = Math.min(pct(val,goal), 100);
  return `<div class="progress-bar"><div class="progress-fill" style="width:${p}%;background:${color}"></div></div>`;
}

function fmtDuration(mins) {
  if (!mins) return '-';
  const h = Math.floor(mins/60), m = Math.round(mins%60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function fmtDist(km) { return km != null ? km.toFixed(1) : '-'; }

function renderDashboard(d) {
  const ds = d.daily_stats || {};
  const bb = d.body_battery || {};
  const rd = d.readiness || {};
  const hrv = d.hrv || {};
  const sl = d.sleep || {};
  const ts = d.training_status || {};
  const rp = d.race_predictions || {};
  const acts = Array.isArray(d.recent_activities) ? d.recent_activities : [];
  const slw = Array.isArray(d.sport_load_weeks) ? d.sport_load_weeks : [];
  const steps7 = Array.isArray(d.steps_7d) ? d.steps_7d : [];
  const gear = Array.isArray(d.gear) ? d.gear : [];
  const wo = d.workout || {};

  // Filter activities to last 7 days
  const now = new Date();
  const weekAgo = new Date(now); weekAgo.setDate(weekAgo.getDate() - 7);
  const weekActs = acts.filter(a => a.activity_date && new Date(a.activity_date) >= weekAgo);

  let html = '';

  // --- Today's Stats ---
  html += `<section><h2>Today</h2><div class="grid grid-4">`;

  // Steps
  const stepPct = pct(ds.total_steps, ds.step_goal);
  html += `<div class="card"><h3>Steps</h3>
    <div class="stat">${v(ds.total_steps,'0').toLocaleString?.() || v(ds.total_steps,'0')}</div>
    <div class="stat-sm">Goal: ${v(ds.step_goal,'-').toLocaleString?.() || v(ds.step_goal,'-')} (${stepPct}%)</div>
    ${progressBar(ds.total_steps, ds.step_goal, 'var(--green)')}</div>`;

  // Body Battery
  html += `<div class="card"><h3>Body Battery</h3>
    <div class="stat">${v(bb.current_level,'-')}<span class="stat-unit">/100</span></div>
    <div class="stat-sm">High ${v(bb.highest_level,'-')} &middot; Low ${v(bb.lowest_level,'-')}</div></div>`;

  // Training Readiness
  const rc = readinessColor(rd.score);
  html += `<div class="card"><h3>Readiness</h3>
    <div class="stat">${v(rd.score,'-')}<span class="stat-unit">/100</span></div>
    <div class="stat-sm">${badge(v(rd.level,'?'), rc)}</div></div>`;

  // Sleep
  html += `<div class="card"><h3>Sleep</h3>
    <div class="stat">${sl.total_sleep_hours != null ? sl.total_sleep_hours.toFixed(1) : '-'}<span class="stat-unit">hrs</span></div>
    <div class="stat-sm">Score: ${v(sl.sleep_score,'-')}/100</div></div>`;

  // HRV
  const hrvColor = (hrv.status||'').toLowerCase().includes('balanced') ? 'green' : 'yellow';
  html += `<div class="card"><h3>HRV</h3>
    <div class="stat">${v(hrv.last_night_avg_ms,'-')}<span class="stat-unit">ms</span></div>
    <div class="stat-sm">${badge(v(hrv.status,'?'), hrvColor)} &middot; Avg ${v(hrv.weekly_avg_ms,'-')}ms</div></div>`;

  // Resting HR
  html += `<div class="card"><h3>Resting HR</h3>
    <div class="stat">${v(sl.resting_heart_rate || ds.resting_heart_rate,'-')}<span class="stat-unit">bpm</span></div>
    <div class="stat-sm">Overnight avg ${v(sl.avg_overnight_hr,'-')} bpm</div></div>`;

  // Intensity Minutes
  const modMin = ds.moderate_intensity_minutes || 0;
  const vigMin = ds.vigorous_intensity_minutes || 0;
  const totalInt = modMin + vigMin * 2;  // vigorous counts double toward weekly goal
  html += `<div class="card"><h3>Intensity Min</h3>
    <div class="stat">${modMin + vigMin}<span class="stat-unit">min</span></div>
    <div class="stat-sm">Mod ${modMin} &middot; Vig ${vigMin}</div></div>`;

  // Active Calories
  html += `<div class="card"><h3>Active Calories</h3>
    <div class="stat">${v(ds.active_calories,'-')}<span class="stat-unit">kcal</span></div>
    <div class="stat-sm">Total: ${v(ds.total_calories,'-')} kcal</div></div>`;

  html += `</div></section>`;

  // --- Training Status Strip ---
  html += `<section><h2>Training Status</h2><div class="grid grid-4">`;

  const tsColor = {'PRODUCTIVE':'green','MAINTAINING':'yellow','PEAKING':'green',
                   'RECOVERY':'blue','DETRAINING':'red','OVERREACHING':'red',
                   'UNPRODUCTIVE':'red','STRAINED':'red'}[ts.status] || 'yellow';
  html += `<div class="card"><h3>Status</h3>
    <div>${badge(v(ts.status,'?'), tsColor)}</div>
    <div class="stat-sm" style="margin-top:.5rem">${v(ts.fitness_trend,'')}</div></div>`;

  html += `<div class="card"><h3>VO2 Max</h3>
    <div class="stat">${v(ts.vo2_max_running,'-')}</div></div>`;

  const acwrColor = (ts.acwr_percent && ts.acwr_percent > 150) ? 'red' :
                    (ts.acwr_percent && ts.acwr_percent >= 80 && ts.acwr_percent <= 130) ? 'green' : 'yellow';
  html += `<div class="card"><h3>ACWR</h3>
    <div class="stat">${v(ts.acwr_percent,'-')}<span class="stat-unit">%</span></div>
    <div class="stat-sm">${badge(v(ts.acwr_status,'?'), acwrColor)}</div></div>`;

  // Race predictions
  html += `<div class="card"><h3>Race Predictions</h3>
    <div style="font-size:.85rem">
    <div>5K: <strong>${v(rp.time_5k,'-')}</strong> <span class="stat-sm">${v(rp.pace_5k,'')}</span></div>
    <div>10K: <strong>${v(rp.time_10k,'-')}</strong> <span class="stat-sm">${v(rp.pace_10k,'')}</span></div>
    <div>HM: <strong>${v(rp.time_half_marathon,'-')}</strong> <span class="stat-sm">${v(rp.pace_half_marathon,'')}</span></div>
    <div>M: <strong>${v(rp.time_marathon,'-')}</strong> <span class="stat-sm">${v(rp.pace_marathon,'')}</span></div>
    </div></div>`;

  html += `</div></section>`;

  // --- Suggested Workout ---
  html += `<section><h2>Today's Suggested Workout</h2>`;
  if (wo.available) {
    html += `<div class="workout-box">${wo.text}</div>`;
  } else {
    html += `<div class="workout-box"><span class="empty">Not generated yet. The AI workout posts to Discord at 07:00, 08:00, and 09:00 &mdash; check back after the first post.</span></div>`;
  }
  html += `</section>`;

  // --- Last 7 Days Activities ---
  html += `<section><h2>Last 7 Days &mdash; Activities</h2>`;
  if (weekActs.length === 0) {
    html += `<p class="stat-sm">No activities in the last 7 days.</p>`;
  } else {
    html += `<div class="card" style="overflow-x:auto"><table>
      <tr><th>Date</th><th>Sport</th><th>Name</th><th>Distance</th><th>Duration</th><th>Load</th><th>Aer TE</th></tr>`;
    for (const a of weekActs) {
      const sb = sportBucket(a.sport);
      html += `<tr>
        <td>${a.activity_date || '-'}</td>
        <td><span class="sport-icon sport-${sb}"></span>${sportLabel(a.sport)}</td>
        <td>${a.activity_name || '-'}</td>
        <td>${a.distance_km ? fmtDist(a.distance_km)+' km' : '-'}</td>
        <td>${fmtDuration(a.duration_minutes)}</td>
        <td>${v(a.training_load,'-')}</td>
        <td>${v(a.aerobic_te,'-')}</td>
      </tr>`;
    }
    html += `</table></div>`;
  }
  html += `</section>`;

  // --- Charts ---
  html += `<section><h2>Weekly Overview</h2><div class="grid grid-2">`;

  // Steps chart
  html += `<div class="card"><h3>Steps (Last 7 Days)</h3><canvas id="stepsChart"></canvas></div>`;

  // Training load by sport chart
  html += `<div class="card"><h3>Training Load by Sport (4 Weeks)</h3><canvas id="loadChart"></canvas></div>`;

  html += `</div></section>`;

  // --- Gear ---
  const activeGear = gear.filter(g => (g.status||'').toLowerCase() === 'active');
  if (activeGear.length > 0) {
    html += `<section><h2>Gear</h2><div class="grid grid-4">`;
    for (const g of activeGear) {
      const wornColor = (g.pct_of_max && g.pct_of_max > 80) ? 'var(--red)' :
                        (g.pct_of_max && g.pct_of_max > 60) ? 'var(--yellow)' : 'var(--green)';
      html += `<div class="card"><h3>${g.gear_type || 'Gear'}</h3>
        <div style="font-size:.9rem;font-weight:600">${g.name}</div>
        <div class="stat-sm">${v(g.total_distance_km,0)} km / ${v(g.max_distance_km,'?')} km</div>
        ${g.pct_of_max != null ? progressBar(g.pct_of_max, 100, wornColor) : ''}
        ${g.pct_of_max != null ? `<div class="stat-sm">${g.pct_of_max}% worn</div>` : ''}
      </div>`;
    }
    html += `</div></section>`;
  }

  document.getElementById('content').innerHTML = html;

  // --- Render charts ---
  // Steps
  if (steps7.length > 0) {
    new Chart(document.getElementById('stepsChart'), {
      type: 'bar',
      data: {
        labels: steps7.map(s => {const d=new Date(s.date); return d.toLocaleDateString('en',{weekday:'short'})}),
        datasets: [{
          label: 'Steps',
          data: steps7.map(s => s.steps),
          backgroundColor: '#6366f1aa',
          borderRadius: 4,
        },{
          label: 'Goal',
          data: steps7.map(s => s.goal),
          type: 'line',
          borderColor: '#9ca3af',
          borderDash: [4,4],
          pointRadius: 0,
          borderWidth: 1.5,
          fill: false,
        }]
      },
      options: { responsive:true, plugins:{legend:{display:false}},
        scales: { y:{beginAtZero:true,ticks:{color:'#9ca3af'},grid:{color:'#2a2d3a'}},
                  x:{ticks:{color:'#9ca3af'},grid:{display:false}} } }
    });
  }

  // Load by sport
  if (slw.length > 0) {
    new Chart(document.getElementById('loadChart'), {
      type: 'bar',
      data: {
        labels: slw.map(w => {const d=new Date(w.week_start); return d.toLocaleDateString('en',{month:'short',day:'numeric'})}),
        datasets: [
          {label:'Running', data:slw.map(w=>w.running_load), backgroundColor:SPORT_COLORS.running},
          {label:'Cycling', data:slw.map(w=>w.cycling_load), backgroundColor:SPORT_COLORS.cycling},
          {label:'Strength', data:slw.map(w=>w.strength_load), backgroundColor:SPORT_COLORS.strength},
          {label:'Swimming', data:slw.map(w=>w.swimming_load), backgroundColor:SPORT_COLORS.swimming},
          {label:'Other', data:slw.map(w=>w.other_load), backgroundColor:SPORT_COLORS.other},
        ]
      },
      options: { responsive:true, plugins:{legend:{position:'bottom',labels:{color:'#e4e4e7',boxWidth:12}}},
        scales: { x:{stacked:true,ticks:{color:'#9ca3af'},grid:{display:false}},
                  y:{stacked:true,beginAtZero:true,ticks:{color:'#9ca3af'},grid:{color:'#2a2d3a'}} } }
    });
  }
}

// Fetch and render
fetch('/api/summary')
  .then(r => r.json())
  .then(d => {
    document.getElementById('timestamp').textContent =
      'Last updated: ' + new Date().toLocaleString();
    renderDashboard(d);
  })
  .catch(e => {
    document.getElementById('content').innerHTML =
      `<p style="color:var(--red)">Failed to load data: ${e.message}</p>`;
  });

// Auto-refresh every 5 minutes
setInterval(() => {
  fetch('/api/summary')
    .then(r => r.json())
    .then(d => {
      document.getElementById('timestamp').textContent =
        'Last updated: ' + new Date().toLocaleString();
      renderDashboard(d);
    })
    .catch(() => {});
}, 300000);
</script>
</body>
</html>
"""


def create_dashboard_app(
    garmin: GarminClient,
    workout_cache: WorkoutCache,
) -> FastAPI:
    """Create and return the FastAPI dashboard application."""

    app = FastAPI(title="Training Dashboard", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTML_TEMPLATE

    @app.get("/api/summary", response_class=JSONResponse)
    async def summary():
        now = time.monotonic()
        if _cache.data is not None and (now - _cache.timestamp) < _cache.ttl:
            data = _cache.data
        else:
            data = await _collect(garmin)
            _cache.data = data
            _cache.timestamp = now

        # Inject cached workout (not from Garmin — from the LLM agent)
        data["workout"] = workout_cache.get()

        # Convert date objects to strings for JSON serialisation
        return _jsonify(data)

    return app


def _jsonify(obj: Any) -> Any:
    """Recursively convert date objects to ISO strings."""
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonify(i) for i in obj]
    return obj
