#!/usr/bin/env python3
"""
Fetches hourly temperature data for Gamil, Barcelos from Open-Meteo
and saves to temperature_data.json in the same folder.
"""
import urllib.request, json, sys
from pathlib import Path

LAT, LON = 41.533, -8.617   # Gamil, Barcelos
START, END = "2025-04-01", "2025-11-30"
TIMEZONE = "Europe/Lisbon"

url = (
    f"https://archive-api.open-meteo.com/v1/archive"
    f"?latitude={LAT}&longitude={LON}"
    f"&start_date={START}&end_date={END}"
    f"&hourly=temperature_2m"
    f"&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean"
    f"&timezone={TIMEZONE}"
)

print(f"Fetching temperature data for Gamil, Barcelos ({LAT}°N, {LON}°W)...")
print(f"Period: {START} → {END}")

try:
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read())
except Exception as e:
    print(f"\n❌ Error: {e}")
    sys.exit(1)

hourly_times = data["hourly"]["time"]          # ["2025-04-01T00:00", ...]
hourly_temps = data["hourly"]["temperature_2m"]  # [12.3, ...]
daily_dates  = data["daily"]["time"]
daily_max    = data["daily"]["temperature_2m_max"]
daily_min    = data["daily"]["temperature_2m_min"]
daily_mean   = data["daily"]["temperature_2m_mean"]

# Build structured dict: date -> list of 24 hourly temps
hourly_by_day = {}
for ts, temp in zip(hourly_times, hourly_temps):
    date = ts[:10]   # "2025-04-01"
    if date not in hourly_by_day:
        hourly_by_day[date] = []
    hourly_by_day[date].append(temp)

# Build daily summary dict
daily_summary = {}
for date, mx, mn, mean in zip(daily_dates, daily_max, daily_min, daily_mean):
    daily_summary[date] = {"max": mx, "min": mn, "mean": round(mean, 1) if mean else None}

output = {
    "meta": {"lat": LAT, "lon": LON, "location": "Gamil, Barcelos", "source": "Open-Meteo ERA5"},
    "hourly": hourly_by_day,   # {"2025-04-01": [t0, t1, ..., t23], ...}
    "daily":  daily_summary,   # {"2025-04-01": {"max":..., "min":..., "mean":...}, ...}
}

out_path = Path(__file__).parent / "temperature_data.json"
with open(out_path, "w") as f:
    json.dump(output, f, separators=(",", ":"))

n_days = len(hourly_by_day)
print(f"\n✅ Saved {n_days} days of hourly data → {out_path}")
print(f"   Sample (2025-04-01): {hourly_by_day.get('2025-04-01', [])[:6]} ... °C")
print(f"   Daily (2025-08-01): {daily_summary.get('2025-08-01', {})}")
