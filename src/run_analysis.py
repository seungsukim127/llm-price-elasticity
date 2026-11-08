"""Run all four analysis steps on a processed panel and write figures + a
results summary.

    python -m src.run_analysis                                  # real data
    python -m src.run_analysis --processed data_demo/processed --figures data_demo/figures
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.analysis import estimators as est
from src.analysis.estimators import absorb

ROOT = Path(__file__).resolve().parents[1]
INK, MUTED, A, B = "#1f2937", "#6b7280", "#2563eb", "#d97706"


def _style(ax, title, xl, yl):
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    ax.set_xlabel(xl, color=MUTED); ax.set_ylabel(yl, color=MUTED)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    ax.grid(alpha=0.25, lw=0.6)


def fig_cross_vs_within(panel, f_cross, f_within, path):
    d = panel[(~panel.is_free) & panel.log_p.notna()]
    if "price_source" in d:
        d = d[d.price_source != "assumed"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    # left: model averages (between variation)
    m = d.assign(lq=d.log_q - d.groupby("date").log_q.transform("mean")).groupby("permaslug")[["log_p", "lq"]].mean()
    axes[0].scatter(m.log_p, m.lq, s=14, color=A, alpha=0.6)
    xs = np.linspace(m.log_p.min(), m.log_p.max(), 50)
    axes[0].plot(xs, m.lq.mean() + f_cross.coef["log_p"] * (xs - m.log_p.mean()), color=INK)
    _style(axes[0], f"Across models: ε = {f_cross.coef['log_p']:.2f}", "log price (model avg)", "log usage (date-demeaned)")
    # right: within-model binscatter
    r = absorb(d, ["log_q", "log_p"], ["permaslug", "date"])
    r = r[r.log_p.abs() > 1e-6]
    if len(r) > 20:
        bins = pd.qcut(r.log_p, 20, duplicates="drop")
        b = r.groupby(bins, observed=True).mean()
        axes[1].scatter(b.log_p, b.log_q, s=22, color=B)
        xs = np.linspace(b.log_p.min(), b.log_p.max(), 50)
        axes[1].plot(xs, f_within.coef["log_p"] * xs, color=INK)
    _style(axes[1], f"Within model: ε = {f_within.coef['log_p']:.2f}", "log price (residualised)", "log usage (residualised)")
    fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)


def fig_event(path_df, title, path, ylabel="log usage vs. day −1"):
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.axhline(0, color=MUTED, lw=0.8); ax.axvline(-0.5, color=MUTED, lw=0.8, ls="--")
    ax.fill_between(path_df.index, path_df.lo, path_df.hi, color=A, alpha=0.15, lw=0)
    ax.plot(path_df.index, path_df.coef, color=A, marker="o", ms=3)
    _style(ax, title, "days relative to event", ylabel)
    fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)


def _row(fit, coef):
    c = fit.ci().loc[coef]
    return {"estimate": round(float(c.coef), 3), "ci95": [round(float(c.lo), 3), round(float(c.hi), 3)],
            "n_obs": fit.n_obs, "n_clusters": fit.n_clusters}


def run(processed: Path, figures: Path, W: int = 14, n_boot: int = 200) -> dict:
    figures.mkdir(parents=True, exist_ok=True)
    panel = pd.read_parquet(processed / "panel.parquet")
    pev = pd.read_csv(processed / "price_events.csv", parse_dates=["date"])
    fev = pd.read_csv(processed / "free_events.csv", parse_dates=["date"])
    out: dict = {}

    f1 = est.cross_model(panel); f2 = est.within_model(panel)
    out["step1_cross_model"] = _row(f1, "log_p")
    out["step2_within_model"] = _row(f2, "log_p")
    fig_cross_vs_within(panel, f1, f2, figures / "fig1_cross_vs_within.png")

    ps = est.price_event_study(panel, pev, W=W)
    if ps.get("n_events", 0):
        out["step3_price_cuts"] = {
            "did_log_change": _row(ps["did"], "tp"),
            "elasticity": _row(ps["dose"], "tp_dose"),
            "elasticity_bootstrap_ci95": [round(x, 3) for x in est.cluster_bootstrap(ps["stack"], "tp_dose", n=n_boot)],
            "mean_dlogp": round(float(ps["mean_dlogp"]), 3),
            "n_events": int(ps["n_events"]), "avg_controls_per_event": round(float(ps["n_controls_avg"]), 1),
            "pretrend_slope_per_day": round(float(ps["pretrend_slope"]), 4), "pretrend_p": round(float(ps["pretrend_p"]), 3)}
        fig_event(ps["path"], f"Price cuts: usage path (N={ps['n_events']} events)", figures / "fig2_price_event_study.png")
    else:
        out["step3_price_cuts"] = "no usable events yet"

    fs = est.free_event_study(panel, fev, pev, W=W)
    out["step4_free_variant"] = {}
    for kind, res in fs.items():
        out["step4_free_variant"][kind] = {}
        for oc, r in res.items():
            if not r.get("n_events"):
                out["step4_free_variant"][kind][oc] = "no usable events"
                continue
            out["step4_free_variant"][kind][oc] = {**_row(r["did"], "tp"), "n_events": int(r["n_events"]),
                                                   "pretrend_p": round(float(r["pretrend_p"]), 3)}
            fig_event(r["path"], f"{kind.replace('_', ' ')} → {oc} usage (N={r['n_events']})",
                      figures / f"fig3_{kind}_{oc}.png")

    (figures / "results.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    ap.add_argument("--figures", type=Path, default=ROOT / "figures")
    ap.add_argument("--window", type=int, default=14)
    ap.add_argument("--n-boot", type=int, default=200)
    a = ap.parse_args(argv)
    run(a.processed, a.figures, a.window, a.n_boot)


if __name__ == "__main__":
    main()
