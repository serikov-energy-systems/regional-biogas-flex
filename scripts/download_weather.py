"""Hourly 2 m temperature at Linkoping, 2023-2025, from the Open-Meteo archive (ERA5-based)."""
from pathlib import Path
import requests

out = Path(__file__).resolve().parents[1] / "data" / "raw" / "temperature_linkoping_2023_2025.json"
r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
    "latitude": 58.41, "longitude": 15.62, "start_date": "2023-01-01", "end_date": "2025-12-31",
    "hourly": "temperature_2m", "timezone": "UTC"}, timeout=120)
r.raise_for_status()
out.write_bytes(r.content)
print(out, len(r.content))
