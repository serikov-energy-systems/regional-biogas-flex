"""Process knowledge for the biogas model: how fast a digester can change its gas output, and what leaves it as digestate.

Digester (first-order degradation in a continuously stirred tank): the stock X of digestible organic matter (energy units) obeys
    dX/dt = F(t) - k X - X / HRT,        gas g(t) = k X
with feeding F, degradation rate k and hydraulic retention time HRT. Steady state: g = F k HRT / (1 + k HRT). A change in feeding
reaches the gas output with the time constant tau = 1 / (k + 1 / HRT): for k = 0.10 per day and HRT = 30 days tau is 7.5 days, so a
daily or weekly swing of the feeding is damped by 1 / sqrt(1 + (omega tau)^2) in the gas.

Values anchored in the Energigas Sverige / Energimyndigheten statistics for 2024 (data/raw/biogas_report): mesophilic digestion at
about 37 C (page 11), raw biogas 60 to 70 percent methane and 9.97 kWh per Nm3 methane (page 30), substrate 22.6 million tonnes wet
weight and digestate 3.69 million tonnes wet weight in Sweden in 2024 with 92 percent used as fertiliser in agriculture (pages 24
and 29), biogas yields per tonne of substrate (Table 12, page 27). k = 0.10 per day and HRT = 30 days are typical orders of magnitude
for mesophilic digestion of mixed substrates and are ASSUMED here: the report gives neither.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

YIELD_NM3_PER_T = {"manure": 26, "sewage sludge": 11, "slaughterhouse waste": 120, "food waste": 148, "energy crops": 200,
                   "agricultural residues": 150, "grass and catch crops": 150, "food industry": 66}   # Table 12
NATIONAL = {"production_gwh": 2395.0, "substrate_kt_wet": 22600.0, "digestate_kt_wet": 3688.0, "digestate_used_kt": 3410.0}


def time_constant_days(k_per_day: float, hrt_days: float) -> float:
    return 1.0 / (k_per_day + 1.0 / hrt_days)


def gain(period_days, k_per_day: float = 0.10, hrt_days: float = 30.0):
    """Amplitude of the gas swing relative to the swing of the feeding, for a sinusoidal feeding of the given period (steady state)."""
    tau = time_constant_days(k_per_day, hrt_days)
    w = 2 * np.pi / np.asarray(period_days, float)
    return 1.0 / np.sqrt(1.0 + (w * tau) ** 2)


def response_table(k_per_day: float = 0.10, hrt_days: float = 30.0) -> pd.DataFrame:
    periods = np.array([1 / 24, 6 / 24, 12 / 24, 1, 3, 7, 14, 30])
    g = gain(periods, k_per_day, hrt_days)
    return pd.DataFrame({"period_days": periods, "gas_swing_over_feeding_swing": g,
                         "feeding_swing_needed_for_25pct_gas_swing": np.minimum(0.25 / g, 99.0)})


def digestate_estimate(production_gwh: float) -> dict:
    """Regional digestate at the national ratio of 2024 (the regional substrate mix is NOT known, so this is an order of magnitude)."""
    r = production_gwh / NATIONAL["production_gwh"]
    return {"digestate_kt_wet": NATIONAL["digestate_kt_wet"] * r, "used_as_fertiliser_kt": NATIONAL["digestate_used_kt"] * r,
            "substrate_kt_wet": NATIONAL["substrate_kt_wet"] * r}
