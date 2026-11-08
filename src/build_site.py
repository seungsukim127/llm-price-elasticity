"""Build the public dashboard (a single static HTML page) from the raw snapshots.

    python -m src.build_site                 # -> _site/index.html
    python -m src.build_site --raw data_demo/raw --out _site_demo

The page is fully static: data is embedded as JSON, charts are drawn by a
vendored copy of Chart.js (site/assets/), so it works on GitHub Pages with no
server and no third-party CDN.

Reading order for the code: build_data() gathers numbers -> render() fills
site/template.html -> the browser draws the two charts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import html
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from src import build_panel as bp

ROOT = Path(__file__).resolve().parents[1]
SITE_SRC = ROOT / "site"
TOP_PROVIDERS = 7          # stacked chart: 7 named providers + "Everyone else"
TABLE_ROWS = 20
PRICE_EVENT_DAYS = 90


def _latest_models_snapshot(raw: Path) -> tuple[pd.Timestamp | None, list[dict]]:
    files = sorted((raw / "models").glob("*.json.gz"))
    if not files:
        return None, []
    with gzip.open(files[-1], "rt", encoding="utf-8") as f:
        payload = json.load(f)
    return pd.Timestamp(files[-1].name.split(".")[0]), payload.get("data", [])


def _snapshot_index(models: list[dict]) -> dict[str, dict]:
    """permaslug -> {name, price_in, price_out, expiration} for the latest snapshot."""
    out = {}
    for m in models:
        mid = m.get("id", "")
        _, var = bp._split_variant(mid)
        slug = m.get("canonical_slug") or mid
        if var and ":" not in slug:
            slug = f"{slug}:{var}"
        pr = m.get("pricing") or {}
        try:
            p_in, p_out = float(pr.get("prompt")) * 1e6, float(pr.get("completion")) * 1e6
        except (TypeError, ValueError):
            p_in = p_out = None
        if p_in is not None and (p_in < 0 or p_out < 0):
            p_in = p_out = None
        out[slug] = {"id": mid, "name": m.get("name") or mid, "price_in": p_in,
                     "price_out": p_out, "expiration": m.get("expiration_date")}
    return out


def _fmt_tokens(x: float) -> str:
    for unit, div in [("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)]:
        if abs(x) >= div:
            return f"{x / div:.1f}{unit}"
    return f"{x:.0f}"


def _fmt_price(p) -> str:
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return "&mdash;"
    p = round(float(p), 6)          # avoid 0.0999999 -> "$0.100"
    if p == 0:
        return "Free"
    return f"${p:,.2f}" if p >= 0.1 else f"${p:,.3f}"


def build_data(raw: Path, manual: Path, processed: Path) -> dict:
    tables = bp.build_panel(raw, processed, manual)
    usage = bp.parse_rankings(raw)
    snap_date, models = _latest_models_snapshot(raw)
    snap = _snapshot_index(models)
    snap_files = sorted((raw / "models").glob("*.json.gz"))

    usage = usage.copy()
    usage["provider"] = np.where(usage.permaslug == "other", "Outside top 50",
                                 usage.permaslug.str.lstrip("~").str.split("/").str[0])
    last_day = usage.date.max()

    # 1) market total, with a 7-day average
    market = usage.groupby("date").tokens.sum().sort_index()
    market_7d = market.rolling(7, min_periods=7).mean()

    # 2) weekly provider share (7-day rolling sums, sampled weekly), last 52 weeks
    recent = usage[usage.date > last_day - pd.Timedelta(days=364)]
    by_prov = recent.pivot_table(index="date", columns="provider", values="tokens", aggfunc="sum").fillna(0.0)
    named = (by_prov.drop(columns=["Outside top 50"], errors="ignore")
             .iloc[-28:].sum().sort_values(ascending=False).index[:TOP_PROVIDERS].tolist())
    weekly = by_prov.rolling(7, min_periods=7).sum().iloc[::-7].iloc[::-1].dropna()
    share = pd.DataFrame(index=weekly.index)
    for p in named:
        share[p] = weekly[p]
    share["Everyone else"] = weekly.drop(columns=named).sum(axis=1)
    share = share.div(share.sum(axis=1), axis=0) * 100

    # 3) top models yesterday
    day = usage[(usage.date == last_day) & (usage.permaslug != "other")].sort_values("tokens", ascending=False)
    total_day = usage.loc[usage.date == last_day, "tokens"].sum()
    week_ago = usage[usage.date == last_day - pd.Timedelta(days=7)].set_index("permaslug").tokens
    rows = []
    for rank, r in enumerate(day.head(TABLE_ROWS).itertuples(index=False), start=1):
        info = snap.get(r.permaslug, {})
        prev = week_ago.get(r.permaslug)
        change = None if prev is None or prev == 0 else (r.tokens / prev - 1) * 100
        rows.append({"rank": rank, "slug": r.permaslug, "name": info.get("name", r.permaslug),
                     "provider": r.provider, "tokens": r.tokens,
                     "share": r.tokens / total_day * 100, "change": change,
                     "price_in": info.get("price_in"), "price_out": info.get("price_out"),
                     "free": r.permaslug.endswith(":free")})

    # 4) price changes (detected from our snapshots + manual file)
    pev = tables["price_events"]
    if len(pev):
        pev = pev[pev.date > last_day - pd.Timedelta(days=PRICE_EVENT_DAYS)].sort_values("date", ascending=False)
    price_changes = [{"date": str(e.date.date()), "slug": e.permaslug,
                      "name": snap.get(e.permaslug, {}).get("name", e.permaslug),
                      "before": e.p_before, "after": e.p_after,
                      "pct": (np.exp(e.dlogp) - 1) * 100}
                     for e in pev.itertuples(index=False)] if len(pev) else []

    # 5) free variants & scheduled retirements from the latest snapshot
    today = snap_date.date() if snap_date is not None else dt.date.today()
    expiring = sorted(
        [{"date": v["expiration"], "name": v["name"], "id": v["id"], "free": k.endswith(":free")}
         for k, v in snap.items() if v["expiration"] and v["expiration"] >= today.isoformat()],
        key=lambda x: x["date"])
    n_free = sum(1 for k in snap if k.endswith(":free"))

    as_of = usage.as_of.max()
    return {
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "as_of": as_of.isoformat() if pd.notna(as_of) else "",
        "last_day": str(last_day.date()),
        "first_day": str(usage.date.min().date()),
        "tracking_since": snap_files[0].name.split(".")[0] if snap_files else "",
        "n_snapshots": len(snap_files),
        "n_models_listed": len(snap),
        "n_free": n_free,
        "n_models_seen": int(usage.loc[usage.permaslug != "other", "permaslug"].nunique()),
        "tokens_last_day": float(total_day),
        "market": {"dates": [str(d.date()) for d in market.index],
                   "total": [float(x) for x in market.values],
                   "avg7": [None if np.isnan(x) else float(x) for x in market_7d.values]},
        "share": {"dates": [str(d.date()) for d in share.index],
                  "series": [{"name": c, "values": [round(float(v), 2) for v in share[c].values]}
                             for c in share.columns]},
        "top": rows, "price_changes": price_changes, "expiring": expiring,
    }


# --------------------------------------------------------------------------- #
# HTML (server-rendered tables so the page reads fine without JavaScript)
# --------------------------------------------------------------------------- #
def _top_table(rows: list[dict]) -> str:
    out = []
    for r in rows:
        ch = r["change"]
        ch_txt = "&mdash;" if ch is None else f"{'+' if ch >= 0 else '−'}{abs(ch):.0f}%"
        ch_cls = "" if ch is None else ("up" if ch >= 0 else "down")
        badge = ' <span class="badge">free</span>' if r["free"] else ""
        out.append(
            f"<tr><td class='num muted'>{r['rank']}</td>"
            f"<td><div class='model'>{html.escape(r['name'])}{badge}</div>"
            f"<div class='slug'>{html.escape(r['slug'])}</div></td>"
            f"<td class='num'>{_fmt_tokens(r['tokens'])}</td>"
            f"<td class='num'>{r['share']:.1f}%</td>"
            f"<td class='num {ch_cls}'>{ch_txt}</td>"
            f"<td class='num'>{_fmt_price(r['price_in'])}</td>"
            f"<td class='num'>{_fmt_price(r['price_out'])}</td></tr>")
    return "\n".join(out)


def _price_list(items: list[dict], tracking_since: str) -> str:
    if not items:
        return (f"<p class='empty'>No price changes detected yet. Daily price snapshots "
                f"started on {html.escape(tracking_since)}; changes appear here as soon as "
                f"two consecutive snapshots differ.</p>")
    li = []
    for e in items[:15]:
        arrow = "down" if e["pct"] < 0 else "up"
        li.append(f"<li><span class='date'>{e['date']}</span>"
                  f"<span class='what'>{html.escape(e['name'])}</span>"
                  f"<span class='num {arrow}'>{_fmt_price(e['before'])} → {_fmt_price(e['after'])} "
                  f"({'+' if e['pct'] >= 0 else '−'}{abs(e['pct']):.0f}%)</span></li>")
    return "<ul class='events'>" + "\n".join(li) + "</ul>"


def _expiring_list(items: list[dict]) -> str:
    if not items:
        return "<p class='empty'>No scheduled retirements listed right now.</p>"
    li = []
    for e in items[:12]:
        badge = ' <span class="badge">free</span>' if e["free"] else ""
        li.append(f"<li><span class='date'>{html.escape(e['date'])}</span>"
                  f"<span class='what'>{html.escape(e['name'])}{badge}</span></li>")
    return "<ul class='events'>" + "\n".join(li) + "</ul>"


def render(data: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    tpl = (SITE_SRC / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    fills = {
        "{{DATA_JSON}}": payload,
        "{{UPDATED}}": html.escape(data["updated"]),
        "{{AS_OF}}": html.escape(data["as_of"][:10]),
        "{{LAST_DAY}}": data["last_day"],
        "{{FIRST_DAY}}": data["first_day"],
        "{{TOKENS_LAST_DAY}}": _fmt_tokens(data["tokens_last_day"]),
        "{{N_MODELS_LISTED}}": str(data["n_models_listed"]),
        "{{N_FREE}}": str(data["n_free"]),
        "{{N_MODELS_SEEN}}": str(data["n_models_seen"]),
        "{{N_SNAPSHOTS}}": str(data["n_snapshots"]),
        "{{TRACKING_SINCE}}": html.escape(data["tracking_since"]),
        "{{TOP_ROWS}}": _top_table(data["top"]),
        "{{PRICE_CHANGES}}": _price_list(data["price_changes"], data["tracking_since"]),
        "{{EXPIRING}}": _expiring_list(data["expiring"]),
    }
    for k, v in fills.items():
        tpl = tpl.replace(k, v)
    (out / "index.html").write_text(tpl, encoding="utf-8")
    shutil.copy(SITE_SRC / "assets" / "chart.umd.min.js", out / "chart.umd.min.js")
    print(f"[site] {out / 'index.html'} ({len(tpl) / 1024:.0f} KB), data through {data['last_day']}")
    return out / "index.html"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    ap.add_argument("--manual", type=Path, default=ROOT / "data" / "manual")
    ap.add_argument("--processed", type=Path, default=ROOT / "data" / "processed")
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    a = ap.parse_args(argv)
    render(build_data(a.raw, a.manual, a.processed), a.out)


if __name__ == "__main__":
    main()
