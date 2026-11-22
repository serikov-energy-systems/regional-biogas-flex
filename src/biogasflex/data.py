"""Inputs: SE3 day-ahead prices (ENTSO-E), TTF gas (front-month futures) and Linkoping temperature, reduced to 12
typical weeks per price year."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
YEARS = (2023, 2024, 2025)


def se3_hourly() -> pd.Series:
    d = pd.read_parquet(RAW / "dayahead_SE3_2023-01-01_2025-12-01.parquet")
    s = d.set_index("ts_utc")["value"].sort_index()
    s.index = pd.to_datetime(s.index, utc=True)
    h = s.groupby(pd.Grouper(freq="h")).mean()
    h = h.reindex(pd.date_range(h.index.min(), h.index.max(), freq="h"))
    return h.interpolate(limit=6)


def ttf_daily() -> pd.Series:
    j = json.loads((RAW / "ttf_front_month.json").read_text())["chart"]["result"][0]
    idx = pd.to_datetime(j["timestamp"], unit="s", utc=True).normalize()
    s = pd.Series(j["indicators"]["quote"][0]["close"], index=idx, dtype=float).dropna()
    return s[~s.index.duplicated(keep="last")]


def temperature() -> pd.Series:
    j = json.loads((RAW / "temperature_linkoping_2023_2025.json").read_text())["hourly"]
    return pd.Series(j["temperature_2m"], index=pd.to_datetime(j["time"], utc=True), dtype=float)


def typical_weeks(year: int) -> dict:
    """One typical week (168 hours, Monday start) per month: the week whose mean SE3 price is the median of that month's
    weeks. Returns arrays: price (EUR/MWh), gas (EUR/MWh, TTF of the week's days), temp (deg C), month, weight (weeks per year)."""
    p, g, t = se3_hourly(), ttf_daily(), temperature()
    p = p[str(year)]
    wk = p.groupby(pd.Grouper(freq="W-SUN"))
    weeks = [(k, v) for k, v in wk if len(v) == 168]
    by_month = {}
    for k, v in weeks:
        by_month.setdefault(v.index[0].month, []).append((k, v))
    out = {"price": [], "gas": [], "temp": [], "month": [], "start": []}
    for m in range(1, 13):
        cand = sorted(by_month[m], key=lambda kv: kv[1].mean())
        k, v = cand[len(cand) // 2]
        out["price"].append(v.to_numpy())
        out["temp"].append(t.reindex(v.index).interpolate().to_numpy())
        d = g.reindex(pd.date_range(v.index[0].normalize(), periods=7, freq="D", tz="UTC")).ffill().bfill().to_numpy()
        out["gas"].append(np.repeat(d, 24))
        out["month"].append(m)
        out["start"].append(str(v.index[0]))
    out["price"], out["gas"], out["temp"] = (np.array(out[k]) for k in ("price", "gas", "temp"))
    out["weight"] = 52.0 / 12.0
    return out
