"""Turn raw snapshots into analysis tables.

Outputs (in data/processed/):
    prices_daily.parquet  one row per (date, permaslug): input/output/blended price + provenance
    panel.parquet         usage (top-50 rows) joined to prices, with logs
    market_daily.csv      total tokens per day (top-50 + "other")
    price_events.csv      detected price changes (snapshot diffs + manual file)
    free_events.csv       :free variant introductions / removals

Key conventions
---------------
* permaslug  = the model variant id used by the rankings dataset
  (e.g. "openai/gpt-4o-2024-05-13", "meta-llama/llama-3.3-70b-instruct:free").
* base_slug  = permaslug without the ":variant" suffix -> links free & paid versions.
* Blended price ($ per 1M tokens) = 0.75 * input + 0.25 * output, i.e. a 3:1
  input:output token mix. Change BLEND_INPUT_SHARE to test sensitivity.

    python -m src.build_panel                     # real data
    python -m src.build_panel --raw data_demo/raw --out data_demo/processed
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BLEND_INPUT_SHARE = 0.75
MIN_PRICE_CHANGE = 0.01      # |dlog p| below this is treated as rounding noise
MAX_FFILL_DAYS = 3           # fill missed snapshot days up to this many days


def _read_gz(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _split_variant(slug: str) -> tuple[str, str]:
    if ":" in slug:
        base, var = slug.split(":", 1)
        return base, var
    return slug, ""


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #
def parse_model_snapshots(raw: Path) -> pd.DataFrame:
    rows = []
    for f in sorted((raw / "models").glob("*.json.gz")):
        date = pd.Timestamp(f.name.split(".")[0])
        for m in _read_gz(f).get("data", []):
            mid = m.get("id", "")
            _, var = _split_variant(mid)
            slug = m.get("canonical_slug") or mid
            if var and ":" not in slug:
                slug = f"{slug}:{var}"
            pr = m.get("pricing") or {}
            try:
                p_in = float(pr.get("prompt", "nan")) * 1e6
                p_out = float(pr.get("completion", "nan")) * 1e6
            except (TypeError, ValueError):
                p_in = p_out = np.nan
            # Negative prices (e.g. "-1" for router/auto models) are not real prices.
            if p_in < 0 or p_out < 0:
                p_in = p_out = np.nan
            rows.append({
                "date": date, "model_id": mid, "permaslug": slug,
                "base_slug": _split_variant(slug)[0], "variant": var,
                "author": mid.split("/")[0] if "/" in mid else "",
                "created": pd.to_datetime(m.get("created"), unit="s", errors="coerce"),
                "price_in": p_in, "price_out": p_out,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates(["date", "permaslug"], keep="last")


def load_manual_price_events(manual: Path) -> pd.DataFrame:
    f = manual / "price_events.csv"
    if not f.exists():
        return pd.DataFrame()
    # Skip header comment lines only (URLs may legitimately contain '#').
    text = "".join(l for l in f.read_text(encoding="utf-8").splitlines(True) if not l.startswith("#"))
    ev = pd.read_csv(io.StringIO(text), parse_dates=["date"])
    return ev.dropna(subset=["permaslug", "date"])


def build_daily_prices(snaps: pd.DataFrame, manual_ev: pd.DataFrame,
                       first_day: pd.Timestamp | None = None) -> pd.DataFrame:
    """Daily price per permaslug with a `price_source` flag:
       snapshot  observed that day
       ffill     snapshot missing, carried forward <= MAX_FFILL_DAYS
       manual    before our first snapshot, reconstructed from manual events
       assumed   before our first snapshot, no known change -> assume constant
    Analyses can drop 'assumed' rows to be strict."""
    if snaps.empty:
        return pd.DataFrame()
    meta = (snaps.sort_values("date").groupby("permaslug")
            .agg(base_slug=("base_slug", "last"), variant=("variant", "last"),
                 author=("author", "last"), created=("created", "min"),
                 first_snap=("date", "min"), last_snap=("date", "max")))
    first_day = first_day or snaps["date"].min()
    last_day = snaps["date"].max()
    out = []
    for slug, g in snaps.groupby("permaslug"):
        g = g.set_index("date")[["price_in", "price_out"]].sort_index()
        m = meta.loc[slug]
        start = first_day if pd.isna(m.created) else max(first_day, m.created.normalize())
        idx = pd.date_range(min(start, g.index.min()), m.last_snap, freq="D")
        d = g.reindex(idx)
        d["price_source"] = np.where(d["price_in"].notna(), "snapshot", None)
        # forward-fill short gaps after the first snapshot
        after = d.index >= m.first_snap
        filled = d.loc[after, ["price_in", "price_out"]].ffill(limit=MAX_FFILL_DAYS)
        newly = d.loc[after, "price_in"].isna() & filled["price_in"].notna()
        d.loc[after, ["price_in", "price_out"]] = filled
        d.loc[newly[newly].index, "price_source"] = "ffill"
        # backward reconstruction before the first snapshot
        before = d.index < m.first_snap
        if before.any():
            first_in, first_out = g.iloc[0]["price_in"], g.iloc[0]["price_out"]
            d.loc[before, ["price_in", "price_out"]] = [first_in, first_out]
            d.loc[before, "price_source"] = "assumed"
            if not manual_ev.empty:
                evs = manual_ev[(manual_ev.permaslug == slug) & (manual_ev.date <= m.first_snap)]
                # Walk events newest -> oldest: a day t gets the "before" price of the
                # earliest event that happens after t; days after the last event keep
                # the first snapshot price.
                for _, e in evs.sort_values("date", ascending=False).iterrows():
                    mask = before & (d.index < e.date)
                    d.loc[mask, ["price_in", "price_out"]] = [e.price_in_before, e.price_out_before]
                if len(evs):
                    d.loc[before, "price_source"] = "manual"
        d["permaslug"] = slug
        out.append(d.rename_axis("date").reset_index())
    prices = pd.concat(out, ignore_index=True)
    prices = prices.merge(meta[["base_slug", "variant", "author", "created"]],
                          left_on="permaslug", right_index=True, how="left")
    prices["price_blend"] = (BLEND_INPUT_SHARE * prices.price_in
                             + (1 - BLEND_INPUT_SHARE) * prices.price_out)
    prices["is_free"] = (prices.variant == "free") | (prices.price_blend == 0)
    return prices[prices.date <= last_day]


def detect_price_events(prices: pd.DataFrame, manual_ev: pd.DataFrame) -> pd.DataFrame:
    p = prices[(~prices.is_free) & prices.price_source.isin(["snapshot", "ffill", "manual"])]
    p = p.sort_values(["permaslug", "date"])
    p["lp"] = np.log(p.price_blend)
    p["prev_lp"] = p.groupby("permaslug").lp.shift()
    p["prev_date"] = p.groupby("permaslug").date.shift()
    ev = p[(p.lp - p.prev_lp).abs() > MIN_PRICE_CHANGE].copy()
    ev = ev[(ev.date - ev.prev_date).dt.days <= MAX_FFILL_DAYS + 1]  # real neighbour, not a gap
    ev["dlogp"] = ev.lp - ev.prev_lp
    ev["p_before"] = np.exp(ev.prev_lp)
    ev["p_after"] = ev.price_blend
    ev["source"] = np.where(ev.price_source == "manual", "manual", "snapshot")
    cols = ["permaslug", "base_slug", "date", "p_before", "p_after", "dlogp", "source"]
    ev = ev[cols]
    # manual events that fall inside the snapshot period are already captured;
    # manual events before it are captured via the 'manual' price_source above.
    return ev.sort_values("date").reset_index(drop=True)


def detect_free_events(snaps: pd.DataFrame) -> pd.DataFrame:
    """A :free variant appearing in / disappearing from the model list.
    The first snapshot day is left-censored and never counts as an event."""
    if snaps.empty:
        return pd.DataFrame(columns=["base_slug", "date", "kind"])
    days = sorted(snaps.date.unique())
    free = snaps[snaps.variant == "free"]
    present = {d: set(free.loc[free.date == d, "base_slug"]) for d in days}
    rows = []
    for prev, cur in zip(days[:-1], days[1:]):
        for b in present[cur] - present[prev]:
            rows.append({"base_slug": b, "date": cur, "kind": "free_added"})
        for b in present[prev] - present[cur]:
            rows.append({"base_slug": b, "date": cur, "kind": "free_removed"})
    return pd.DataFrame(rows, columns=["base_slug", "date", "kind"])


# --------------------------------------------------------------------------- #
# Usage
# --------------------------------------------------------------------------- #
def parse_rankings(raw: Path) -> pd.DataFrame:
    frames = []
    for f in sorted((raw / "rankings").glob("*.json.gz")):
        payload = _read_gz(f)
        d = pd.DataFrame(payload.get("data", []))
        if d.empty:
            continue
        d["as_of"] = pd.Timestamp(payload["meta"]["as_of"])
        frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["date", "model_permaslug", "total_tokens"])
    u = pd.concat(frames, ignore_index=True)
    u["date"] = pd.to_datetime(u.date)
    u["tokens"] = pd.to_numeric(u.total_tokens, errors="coerce").astype(float)
    # Restatements: keep the most recent version of each (date, model) row.
    u = (u.sort_values("as_of").drop_duplicates(["date", "model_permaslug"], keep="last")
          .rename(columns={"model_permaslug": "permaslug"}))
    return u[["date", "permaslug", "tokens", "as_of"]]


def build_panel(raw: Path, out: Path, manual: Path) -> dict[str, pd.DataFrame]:
    out.mkdir(parents=True, exist_ok=True)
    snaps = parse_model_snapshots(raw)
    usage = parse_rankings(raw)
    manual_ev = load_manual_price_events(manual)

    market = (usage.groupby("date").tokens.sum().rename("market_tokens").reset_index())
    top = usage[usage.permaslug != "other"].copy()
    top["rank"] = top.groupby("date").tokens.rank(ascending=False, method="first")

    first_usage = top.date.min() if not top.empty else None
    prices = build_daily_prices(snaps, manual_ev, first_day=first_usage)
    price_ev = detect_price_events(prices, manual_ev) if not prices.empty else pd.DataFrame()
    free_ev = detect_free_events(snaps)

    panel = top.merge(prices, on=["date", "permaslug"], how="left")
    panel["base_slug"] = panel.base_slug.fillna(panel.permaslug.map(lambda s: _split_variant(s)[0]))
    panel["is_free"] = panel.is_free.fillna(panel.permaslug.str.endswith(":free")).astype(bool)
    panel = panel.merge(market, on="date", how="left")
    panel["share"] = panel.tokens / panel.market_tokens
    panel["log_q"] = np.log(panel.tokens)
    panel["log_p"] = np.log(panel.price_blend.where(panel.price_blend > 0))
    panel["days_since_release"] = (panel.date - panel.created.dt.normalize()).dt.days

    prices.to_parquet(out / "prices_daily.parquet", index=False)
    panel.to_parquet(out / "panel.parquet", index=False)
    market.to_csv(out / "market_daily.csv", index=False)
    price_ev.to_csv(out / "price_events.csv", index=False)
    free_ev.to_csv(out / "free_events.csv", index=False)

    matched = panel.price_blend.notna().mean() if len(panel) else 0
    print(f"[panel] {len(panel):,} model-days, {panel.permaslug.nunique()} models, "
          f"{panel.date.min().date() if len(panel) else '-'}..{panel.date.max().date() if len(panel) else '-'}; "
          f"price matched {matched:.0%}")
    print(f"[events] {len(price_ev)} price changes, {len(free_ev)} free-variant changes")
    return {"panel": panel, "prices": prices, "price_events": price_ev,
            "free_events": free_ev, "market": market}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "processed")
    ap.add_argument("--manual", type=Path, default=ROOT / "data" / "manual")
    a = ap.parse_args(argv)
    build_panel(a.raw, a.out, a.manual)


if __name__ == "__main__":
    main()
