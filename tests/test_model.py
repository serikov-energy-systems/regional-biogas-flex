from dataclasses import replace

import numpy as np
import pytest

from biogasflex import model


def _inputs(W=2):
    rng = np.random.default_rng(1)
    price = 50 + 40 * np.sin(np.arange(168) / 168 * 14 * np.pi)[None, :].repeat(W, 0) + rng.normal(0, 5, (W, 168))
    gas = np.full((W, 168), 40.0)
    temp = np.full((W, 168), 0.0)
    return price, gas, temp


def test_gas_balance_and_weekly_total():
    price, gas, temp = _inputs()
    pl = replace(model.Plant(), store_mwh=50.0, feed_flex=0.25)
    m = model.build(pl, model.Market(), price, gas, temp, 26.0)
    r = model.solve(m)
    W = price.shape[0]
    g = r["g"].reshape(W, 168)
    use = (r["u"] + r["c"] + r["b"] + r["f"]).reshape(W, 168)
    assert np.allclose(g.sum(1), use.sum(1), atol=1e-4)          # storage is cyclic within the week
    assert np.allclose(g.sum(1), pl.gas_mwh_year / 8760 * 168, atol=1e-4)


def test_flexible_feeding_never_lowers_gross_profit():
    price, gas, temp = _inputs()
    base = model.solve(model.build(model.Plant(), model.Market(), price, gas, temp, 26.0))["profit"]
    flex = model.solve(model.build(replace(model.Plant(), store_mwh=50.0, feed_flex=0.25), model.Market(), price, gas, temp, 26.0))["profit"]
    assert flex >= base - 1e-6


def test_heat_sold_not_above_demand_and_support_raises_profit():
    price, gas, temp = _inputs()
    r = model.solve(model.build(model.Plant(), model.Market(), price, gas, temp, 26.0))
    assert np.all(r["hs"] <= r["D"] + 1e-6)
    r2 = model.solve(model.build(model.Plant(), model.Market(support_biomethane=25.0), price, gas, temp, 26.0))
    assert r2["profit"] > r["profit"]


def test_outage_unserved_when_boilers_too_small():
    price, gas, temp = _inputs(1)
    avail = np.ones((1, 168))
    m = model.build(replace(model.Plant(), boiler_mw=2.0, chp_mw=1.0), model.Market(), price, gas, temp - 10, 52.0, heat_available=avail)
    r = model.solve(m)
    assert r["un"].sum() > 0


def test_heat_demand_bounds():
    mk = model.Market()
    d = model.heat_demand(np.array([-20.0, 0.0, 20.0]), mk)
    assert d[0] == pytest.approx(mk.heat_peak_mw) and d[2] == pytest.approx(mk.heat_base_mw) and d[0] > d[1] > d[2]


def test_dynamic_digester_damps_the_gas_response_and_keeps_the_mean():
    price, gas, temp = _inputs()
    pl = replace(model.Plant(), store_mwh=50.0, feed_flex=0.25, digester_dynamic=True)
    r = model.solve(model.build(pl, model.Market(), price, gas, temp, 26.0))
    g = r["g"].reshape(price.shape[0], 168)
    mean_g = pl.gas_mwh_year / 8760.0
    assert np.allclose(g.mean(1), mean_g, rtol=0.02)
    assert (g.max(1) - g.min(1)).max() < 0.25 * 2 * mean_g * 0.5      # far less swing than the +-25% an instant response allows


def test_digester_eta_formula():
    pl = model.Plant()
    assert abs(pl.digester_eta - 0.75) < 1e-9
