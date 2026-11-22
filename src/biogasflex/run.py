"""Plans x scenarios, value of flexibility, EVPI, self-sufficiency and the outage test.

    python -m biogasflex.run
"""
from __future__ import annotations

import itertools
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from . import data, model

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"

# assumed investment costs of the options (EUR); annualised at 6% over 20 years plus 3% operation
ANN = 0.06 / (1 - 1.06 ** -20) + 0.03
COST = {"upgr_mw": 400e3, "chp_mw": 700e3, "boiler_mw": 150e3, "store_mwh": 20e3}
FEED_FLEX_COST = 50e3        # EUR per year for feeding control (assumed)

POLICIES = {"no support": dict(support_biomethane=0.0, premium_el=0.0),
            "biomethane support 25": dict(support_biomethane=25.0, premium_el=0.0),
            "biomethane 25 + CHP premium 30": dict(support_biomethane=25.0, premium_el=30.0)}
BASE = model.Plant()


def capex_year(pl: model.Plant) -> float:
    c = sum(COST[k] * max(getattr(pl, k) - getattr(BASE, k), 0.0) for k in COST)
    return c * ANN + (FEED_FLEX_COST if pl.feed_flex > 0 else 0.0)


def one(plan_id, pl, year, pol):
    w = data.typical_weeks(year)
    m = model.build(pl, model.Market(**POLICIES[pol]), w["price"], w["gas"], w["temp"], w["weight"])
    r = model.solve(m)
    wt = w["weight"]
    return {"plan": plan_id, "year": year, "policy": pol, "gross_eur": r["profit"],
            "net_eur": r["profit"] - capex_year(pl),
            "biomethane_gwh": float(model.Plant.eta_up * r["u"].sum() * wt / 1e3),
            "el_gwh": float(pl.eta_el * r["c"].sum() * wt / 1e3), "heat_sold_gwh": float(r["hs"].sum() * wt / 1e3),
            "heat_demand_gwh": float(r["D"].sum() * wt / 1e3), "flared_gwh": float(r["f"].sum() * wt / 1e3)}


def plan_grid():
    plans = {}
    for ua, ca, s, ff in itertools.product((0, 8, 16), (0, 8, 16), (0, 40, 120), (0.0, 0.25)):
        plans[f"U+{ua} C+{ca} S{s} F{int(ff * 100)}"] = replace(
            BASE, upgr_mw=BASE.upgr_mw + ua, chp_mw=BASE.chp_mw + ca, store_mwh=float(s), feed_flex=ff)
    return plans


def outage(pl, month_idx=0, supply_cut=0.4):
    """External heat source fails for the cold January week and the feedstock supply falls by `supply_cut` in that week."""
    w = data.typical_weeks(2024)
    W, H = w["price"].shape
    avail = np.zeros((W, H)); avail[month_idx, :] = 1.0
    sf = np.ones((W, H)); sf[month_idx, :] = 1 - supply_cut
    m = model.build(pl, model.Market(), w["price"], w["gas"], w["temp"], w["weight"], supply_factor=sf, heat_available=avail)
    r = model.solve(m)
    un = r["un"].reshape(W, H)[month_idx].sum()
    d = r["D"].reshape(W, H)[month_idx].sum()
    return {"unserved_share": float(un / d), "unserved_mwh": float(un), "demand_mwh": float(d)}


def main():
    RES.mkdir(exist_ok=True)
    plans = plan_grid()
    jobs = [(pid, pl, y, pol) for pid, pl in plans.items() for y in data.YEARS for pol in POLICIES]
    rows = Parallel(n_jobs=4)(delayed(one)(*j) for j in jobs)
    df = pd.DataFrame(rows)
    df["capex_year_eur"] = df.plan.map({k: capex_year(v) for k, v in plans.items()})
    df.to_csv(RES / "plans_scenarios.csv", index=False)

    piv = df.pivot_table(index="plan", columns=["year", "policy"], values="net_eur")
    ref = "U+0 C+0 S0 F0"
    best_each = piv.max()
    best_plan_each = piv.idxmax()
    exp_net = piv.mean(axis=1)
    best_single = exp_net.idxmax()
    evpi = best_each.mean() - exp_net.max()
    regret = (best_each - piv)
    out = {
        "reference_plan": ref,
        "reference_net_meur": float(piv.loc[ref].mean() / 1e6),
        "best_single_plan": best_single,
        "best_single_expected_net_meur": float(exp_net.max() / 1e6),
        "best_single_gain_over_reference_meur": float((exp_net.max() - piv.loc[ref].mean()) / 1e6),
        "evpi_meur": float(evpi / 1e6),
        "max_regret_of_best_single_meur": float(regret.loc[best_single].max() / 1e6),
        "best_plan_by_scenario": {f"{y} / {p}": best_plan_each[(y, p)] for (y, p) in piv.columns},
    }
    # value of flexibility alone (storage 120 MWh + feeding +-25%, no new conversion capacity) against the reference
    flex_only = "U+0 C+0 S120 F25"
    out["flex_only_gain_meur_by_scenario"] = {f"{y} / {p}": float((piv.loc[flex_only, (y, p)] - piv.loc[ref, (y, p)]) / 1e6)
                                              for (y, p) in piv.columns}
    sub = df[df.plan == best_single]
    ref_rows = df[df.plan == ref]
    out["self_sufficiency"] = {
        "heat_share_covered_ref": float(ref_rows.heat_sold_gwh.sum() / ref_rows.heat_demand_gwh.sum()),
        "heat_share_covered_best": float(sub.heat_sold_gwh.sum() / sub.heat_demand_gwh.sum()),
        "biomethane_gwh_ref_mean": float(ref_rows.biomethane_gwh.mean()),
        "biomethane_gwh_best_mean": float(sub.biomethane_gwh.mean()),
    }
    # outage test on a separate small grid
    res_rows = []
    for s, bo, c, ff in itertools.product((0, 40, 120), (8, 18, 28), (4, 12, 20), (0.0, 0.25)):
        pl = replace(BASE, store_mwh=float(s), boiler_mw=float(bo), chp_mw=float(c), feed_flex=ff)
        res_rows.append((f"S{s} B{bo} C{c} F{int(ff * 100)}", pl))
    rr = Parallel(n_jobs=4)(delayed(outage)(pl) for _, pl in res_rows)
    od = pd.DataFrame(rr); od.insert(0, "plan", [n for n, _ in res_rows])
    od.to_csv(RES / "outage_test.csv", index=False)
    out["outage_unserved_share"] = {"reference": float(od[od.plan == "S0 B8 C4 F0"].unserved_share.iloc[0]),
                                    "min": float(od.unserved_share.min()), "max": float(od.unserved_share.max())}
    (RES / "summary.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    piv.round(0).to_csv(RES / "net_by_plan_scenario.csv")
    regret.round(0).to_csv(RES / "regret_by_plan_scenario.csv")


if __name__ == "__main__":
    main()


def volatility_sensitivity():
    """Does flexibility pay when the SE3 price is more volatile? Prices are stretched around their mean by a factor k."""
    rows = []
    plans = {"reference": BASE,
             "storage 120 MWh + feeding +-25%": replace(BASE, store_mwh=120.0, feed_flex=0.25),
             "CHP +8 MW, storage 120, feeding": replace(BASE, chp_mw=BASE.chp_mw + 8, store_mwh=120.0, feed_flex=0.25),
             "CHP +16 MW, storage 120, feeding": replace(BASE, chp_mw=BASE.chp_mw + 16, store_mwh=120.0, feed_flex=0.25)}
    w = data.typical_weeks(2024)
    for k in (1.0, 2.0, 3.0, 4.0):
        price = w["price"].mean() + k * (w["price"] - w["price"].mean())
        for name, pl in plans.items():
            m = model.build(pl, model.Market(), price, w["gas"], w["temp"], w["weight"])
            r = model.solve(m)
            rows.append({"stretch": k, "plan": name, "gross_eur": r["profit"], "net_eur": r["profit"] - capex_year(pl),
                         "el_gwh": float(pl.eta_el * r["c"].sum() * w["weight"] / 1e3),
                         "max_price": float(price.max()), "hours_above_120": float((price > 120).mean() * 8760)})
    df = pd.DataFrame(rows)
    ref = df[df.plan == "reference"].set_index("stretch").net_eur
    df["gain_over_reference_eur"] = df.net_eur - df.stretch.map(ref)
    df.to_csv(RES / "volatility_sensitivity.csv", index=False)
    print(df.round(0).to_string())
