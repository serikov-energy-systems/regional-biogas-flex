"""Pyomo / HiGHS model of one region's biogas system on 12 typical weeks (hourly).

All plant, cost, demand and policy values are illustrative assumptions (data/sources.csv), except the regional
production of 183 GWh raw biogas in 2024 (Energigas Sverige / Energimyndigheten statistics for Ostergotland) and the
national shares of use, which are quoted from the same report.

Raw gas g_t is produced by the digesters. With flexible feeding it may deviate by +-feed_flex from the weekly mean
(the weekly total stays fixed), storage s_t buffers it. Uses: upgrading u_t to biomethane (sold at TTF + premium +
support), CHP c_t (electricity at SE3 spot + premium, heat to the district heating network up to its demand),
boiler b_t (heat), flare f_t. Unserved heat demand (when the external heat source fails) costs a penalty.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pyomo.environ as pyo


@dataclass(frozen=True)
class Plant:
    gas_mwh_year: float = 183_000.0   # raw biogas, Ostergotland 2024 (Energigas Sverige report, table 13)
    upgr_mw: float = 16.0             # existing upgrading capacity, MW raw gas in (assumed; 3 units in the region)
    chp_mw: float = 4.0               # existing CHP, MW gas in (assumed)
    boiler_mw: float = 8.0            # MW gas in (assumed)
    store_mwh: float = 0.0            # raw gas storage (assumed; none existing)
    feed_flex: float = 0.0            # share of the mean by which the feeding may vary within a week
    eta_up: float = 0.97              # raw gas to biomethane (methane slip 3%, assumed)
    eta_el: float = 0.40
    eta_th: float = 0.45
    eta_boiler: float = 0.90


@dataclass(frozen=True)
class Market:
    premium_biomethane: float = 30.0     # EUR/MWh over TTF for a vehicle-fuel biomethane with guarantee of origin (assumed)
    support_biomethane: float = 0.0      # EUR/MWh biomethane, policy scenario
    premium_el: float = 0.0              # EUR/MWh_e, policy scenario
    heat_price: float = 45.0             # EUR/MWh district heat sold (assumed)
    upgrade_opex: float = 12.0           # EUR/MWh raw gas upgraded (assumed)
    chp_opex: float = 6.0                # EUR/MWh_e (assumed)
    unserved_heat: float = 500.0         # EUR/MWh
    heat_peak_mw: float = 25.0           # connected heat demand at -10 C and below (assumed)
    heat_base_mw: float = 3.0            # at 15 C and above (assumed)


def heat_demand(temp: np.ndarray, m: Market) -> np.ndarray:
    frac = np.clip((15.0 - temp) / 25.0, 0.0, 1.0)
    return m.heat_base_mw + (m.heat_peak_mw - m.heat_base_mw) * frac


def build(plant: Plant, mkt: Market, price: np.ndarray, gas: np.ndarray, temp: np.ndarray, weight: float,
          supply_factor: np.ndarray | None = None, heat_available: np.ndarray | None = None):
    """price, gas, temp: arrays (W, 168). supply_factor (W, 168): multiplies the feedstock supply (disruption test).
    heat_available (W, 168): share of the heat demand the biogas system must cover (1 = all; the external source covers
    the rest); the normal case uses the biogas system for the part it can serve and leaves the rest to the network."""
    W, H = price.shape
    mean_g = plant.gas_mwh_year / 8760.0
    sf = np.ones((W, H)) if supply_factor is None else supply_factor
    D = heat_demand(temp, mkt)
    m = pyo.ConcreteModel()
    m.W, m.H = pyo.RangeSet(0, W - 1), pyo.RangeSet(0, H - 1)
    idx = [(w, h) for w in range(W) for h in range(H)]
    m.I = pyo.Set(initialize=idx, dimen=2)
    m.g = pyo.Var(m.I, bounds=(0, None))
    m.s = pyo.Var(m.I, bounds=(0, max(plant.store_mwh, 0.0)))
    m.u = pyo.Var(m.I, bounds=(0, plant.upgr_mw))
    m.c = pyo.Var(m.I, bounds=(0, plant.chp_mw))
    m.b = pyo.Var(m.I, bounds=(0, plant.boiler_mw))
    m.f = pyo.Var(m.I, bounds=(0, None))
    m.hs = pyo.Var(m.I, bounds=(0, None))     # useful heat sold
    m.un = pyo.Var(m.I, bounds=(0, None))     # unserved heat demand

    def prod_bounds(mm, w, h):
        base = mean_g * sf[w, h]
        return pyo.inequality(base * (1 - plant.feed_flex), mm.g[w, h], base * (1 + plant.feed_flex))
    m.gb = pyo.Constraint(m.I, rule=prod_bounds)

    def week_total(mm, w):
        return sum(mm.g[w, h] for h in range(H)) == sum(mean_g * sf[w, h] for h in range(H))
    m.wt = pyo.Constraint(m.W, rule=week_total)

    def gas_balance(mm, w, h):
        prev = mm.s[w, h - 1] if h > 0 else mm.s[w, H - 1]      # storage cyclic within the week
        return mm.g[w, h] + prev - mm.s[w, h] == mm.u[w, h] + mm.c[w, h] + mm.b[w, h] + mm.f[w, h]
    m.gbal = pyo.Constraint(m.I, rule=gas_balance)

    def heat_rule(mm, w, h):   # heat from CHP and boiler is sold up to the demand; the rest of the demand is unserved
        return mm.hs[w, h] <= plant.eta_th * mm.c[w, h] + plant.eta_boiler * mm.b[w, h]
    m.hr = pyo.Constraint(m.I, rule=heat_rule)

    def cap_rule(mm, w, h):
        return mm.hs[w, h] <= D[w, h]
    m.dc = pyo.Constraint(m.I, rule=cap_rule)
    if heat_available is not None:
        def need_rule(mm, w, h):
            return mm.hs[w, h] + mm.un[w, h] >= D[w, h] * heat_available[w, h]
        m.nr = pyo.Constraint(m.I, rule=need_rule)

    pb = gas + mkt.premium_biomethane + mkt.support_biomethane
    obj = 0
    for (w, h) in idx:
        rev = (plant.eta_up * m.u[w, h] * pb[w, h]
               + plant.eta_el * m.c[w, h] * (price[w, h] + mkt.premium_el)
               + m.hs[w, h] * mkt.heat_price)
        cost = (mkt.upgrade_opex * m.u[w, h] + mkt.chp_opex * plant.eta_el * m.c[w, h]
                + mkt.unserved_heat * m.un[w, h] + 0.01 * m.f[w, h])
        obj += weight * (rev - cost)
    m.obj = pyo.Objective(expr=obj, sense=pyo.maximize)
    m._D = D
    return m


def solve(m) -> dict:
    opt = pyo.SolverFactory("appsi_highs")
    res = opt.solve(m)
    ok = str(res.solver.termination_condition).lower().endswith("optimal")
    if not ok:
        raise RuntimeError(f"solver: {res.solver.termination_condition}")
    idx = list(m.I)
    get = lambda v: np.array([pyo.value(v[i]) for i in idx])
    out = {k: get(getattr(m, k)) for k in ("g", "u", "c", "b", "f", "hs", "un")}
    out["profit"] = pyo.value(m.obj)
    out["D"] = m._D.flatten()
    return out
