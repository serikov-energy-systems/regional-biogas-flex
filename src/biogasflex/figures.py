from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
v = pd.read_csv(ROOT / "results" / "volatility_sensitivity.csv")
fig, ax = plt.subplots(figsize=(6.4, 3.6))
for name, g in v[v.plan != "reference"].groupby("plan"):
    ax.plot(g.stretch, g.gain_over_reference_eur / 1e6, marker="o", label=name)
ax.axhline(0, color="k", lw=0.6)
ax.set_xlabel("Stretch of the SE3 price around its mean (1 = 2024 typical weeks)")
ax.set_ylabel("Net gain over the reference, million EUR per year")
ax.legend(fontsize=7)
fig.tight_layout(); fig.savefig(ROOT / "figures" / "flexibility_vs_volatility.png", dpi=150)
