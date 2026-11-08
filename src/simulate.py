"""Synthetic OpenRouter-shaped raw data with KNOWN true parameters.

Purpose: prove that the pipeline (collect format -> build_panel -> estimators)
recovers planted effects before trusting it on real data, and let anyone run
the whole repo without an API key.

Data-generating process
-----------------------
    quality_i      ~ N(0, 1)                    (unobserved by the analyst)
    log P_i        = 0.9*quality_i + noise      (better models cost more)
    log Q_it       = a + 1.0*quality_i + EPS_TRUE*log P_it + market_t
                     + FREE_SUBST*1[free variant exists] + AR(1) noise
    -> cross-model regression is biased toward zero (quality is omitted);
       within-model estimates should recover EPS_TRUE.

    python -m src.simulate --out data_demo/raw
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

EPS_TRUE = -1.2      # within-model price elasticity
FREE_SUBST = -0.25   # log change in paid usage while a free variant exists
FREE_SIZE = 0.6      # free-variant tokens as a share of paid counterfactual
QUALITY_DEMAND = 1.0
QUALITY_PRICE = 0.9


def simulate(out: Path, n_models: int = 90, n_days: int = 240, snapshot_start: int = 60,
             seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    start = dt.date(2026, 3, 1)
    dates = pd.date_range(start, periods=n_days, freq="D")
    authors = ["acme", "zeta", "nova", "orbit", "lumen", "kite"]

    models = []
    for i in range(n_models):
        q = rng.normal()
        lp0 = np.log(2.0) + QUALITY_PRICE * q + rng.normal(0, 0.6)
        release = -400 if i < 75 else int(rng.integers(20, n_days - 40))  # 15 launch mid-sample
        a = authors[i % len(authors)]
        models.append({"i": i, "id": f"{a}/model-{i}", "canonical": f"{a}/model-{i}-2025",
                       "q": q, "lp0": lp0, "release": release})

    # price paths with cuts (and a few increases)
    lp = np.array([[m["lp0"]] * n_days for m in models])
    true_events = []
    for i in rng.choice(n_models, 30, replace=False):
        d0 = int(rng.integers(25, n_days - 25))
        if models[i]["release"] > d0 - 60:
            continue
        dl = rng.uniform(-0.7, -0.15) if rng.random() < 0.85 else rng.uniform(0.1, 0.3)
        lp[i, d0:] += dl
        true_events.append((models[i]["canonical"], dates[d0], dl))

    # free variants: 6 start with one and lose it, 6 gain one
    has_free = np.zeros((n_models, n_days), bool)
    free_events = []
    cand = [m["i"] for m in models if m["release"] < 0]
    pick = rng.choice(cand, 12, replace=False)
    for k, i in enumerate(pick):
        d0 = int(rng.integers(snapshot_start + 20, n_days - 20))
        if k < 6:
            has_free[i, :d0] = True
            free_events.append((models[i]["canonical"], dates[d0], "free_removed"))
        else:
            has_free[i, d0:] = True
            free_events.append((models[i]["canonical"], dates[d0], "free_added"))

    market = np.cumsum(rng.normal(0.004, 0.02, n_days))
    rows = []
    for m in models:
        i = m["i"]
        noise = np.zeros(n_days)
        e = rng.normal(0, 0.12, n_days)
        for t in range(1, n_days):
            noise[t] = 0.6 * noise[t - 1] + e[t]
        ramp = np.clip((np.arange(n_days) - m["release"]) / 20, 0.05, 1.0)
        logq = (21 + QUALITY_DEMAND * m["q"] + EPS_TRUE * lp[i] + market + noise
                + np.log(ramp) + FREE_SUBST * has_free[i])
        for t in range(n_days):
            if t < m["release"]:
                continue
            rows.append((dates[t], m["canonical"], float(np.exp(logq[t]))))
            if has_free[i, t]:
                cf = logq[t] - FREE_SUBST * has_free[i, t]
                rows.append((dates[t], m["canonical"] + ":free",
                             float(FREE_SIZE * np.exp(cf + rng.normal(0, 0.1)))))
    usage = pd.DataFrame(rows, columns=["date", "permaslug", "tokens"])

    # Top-50 truncation + "other" row, exactly like the real endpoint
    usage["rank"] = usage.groupby("date").tokens.rank(ascending=False, method="first")
    top = usage[usage["rank"] <= 50]
    other = usage[usage["rank"] > 50].groupby("date").tokens.sum().reset_index()
    other["permaslug"] = "other"
    rk = pd.concat([top, other]).sort_values(["date", "tokens"], ascending=[True, False])
    data = [{"date": d.strftime("%Y-%m-%d"), "model_permaslug": s, "total_tokens": str(int(x))}
            for d, s, x in rk[["date", "permaslug", "tokens"]].itertuples(index=False)]
    (out / "rankings").mkdir(parents=True, exist_ok=True)
    as_of = (dates[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%dT02:00:00Z")
    payload = {"data": data, "meta": {"as_of": as_of, "version": "v1",
                                      "start_date": str(dates[0].date()), "end_date": str(dates[-1].date())}}
    with gzip.open(out / "rankings" / f"sim_{dates[0].date()}_{dates[-1].date()}.json.gz", "wt") as f:
        json.dump(payload, f)

    # Daily model-list snapshots, starting late (like the real project)
    (out / "models").mkdir(parents=True, exist_ok=True)
    for t in range(snapshot_start, n_days):
        lst = [{"id": "openrouter/auto", "canonical_slug": "openrouter/auto", "created": 1700000000,
                "pricing": {"prompt": "-1", "completion": "-1"}}]
        for m in models:
            if t < m["release"]:
                continue
            p_in = float(np.exp(lp[m["i"], t])) / 1.75   # blend = 0.75*in + 0.25*(4*in) = 1.75*in
            created = int(pd.Timestamp(dates[0] + pd.Timedelta(days=m["release"])).timestamp())
            lst.append({"id": m["id"], "canonical_slug": m["canonical"], "created": created,
                        "pricing": {"prompt": f"{p_in / 1e6:.12f}", "completion": f"{4 * p_in / 1e6:.12f}"}})
            if has_free[m["i"], t]:
                lst.append({"id": m["id"] + ":free", "canonical_slug": m["canonical"], "created": created,
                            "pricing": {"prompt": "0", "completion": "0"}})
        with gzip.open(out / "models" / f"{dates[t].date()}.json.gz", "wt") as f:
            json.dump({"data": lst}, f)

    # Manual file for price changes that happened before snapshots began
    pre = [e for e in true_events if e[1] < dates[snapshot_start]]
    man = out.parent / "manual"
    man.mkdir(parents=True, exist_ok=True)
    lines = ["date,permaslug,price_in_before,price_out_before,price_in_after,price_out_after,source_url,note"]
    for slug, d, dl in pre:
        i = next(m["i"] for m in models if m["canonical"] == slug)
        t = dates.get_loc(d)
        b, a = np.exp(lp[i, t - 1]) / 1.75, np.exp(lp[i, t]) / 1.75
        lines.append(f"{d.date()},{slug},{b:.6f},{4*b:.6f},{a:.6f},{4*a:.6f},simulated,")
    (man / "price_events.csv").write_text("\n".join(lines) + "\n")

    truth = {"eps_true": EPS_TRUE, "free_subst": FREE_SUBST,
             "n_price_events": len(true_events), "n_free_events": len(free_events)}
    (out.parent / "truth.json").write_text(json.dumps(truth, indent=2))
    print(f"[simulate] {n_models} models x {n_days} days -> {out}; truth={truth}")
    return truth


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data_demo/raw"))
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    simulate(a.out, seed=a.seed)


if __name__ == "__main__":
    main()
