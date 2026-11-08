"""Collect raw OpenRouter snapshots.

Two sources, both saved *unmodified* (gzipped JSON) so that parsing bugs or
API schema changes never destroy information:

* Models API  (GET /api/v1/models)                 -> current prices only.
  Price history exists only because we snapshot it every day.
* Rankings API (GET /api/v1/datasets/rankings-daily) -> daily token totals for
  the top-50 models (+ one aggregated "other" row). History back to 2025-01-01.

Usage
-----
    python -m src.collect daily              # models snapshot + last 7 days of rankings
    python -m src.collect backfill           # rankings from 2025-01-01 to yesterday
    python -m src.collect backfill --start 2026-01-01

Environment
-----------
    OPENROUTER_API_KEY   required for the rankings endpoint (any valid key).

Data attribution: "Source: OpenRouter (openrouter.ai/rankings), as of {as_of}."
Licensed under CC BY 4.0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://openrouter.ai/api/v1"
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DATASET_FLOOR = dt.date(2025, 1, 1)  # rankings dataset starts here
MAX_SPAN_DAYS = 366                  # API limit on one request window
HEADERS = {
    # OpenRouter's optional attribution headers; identify the project, not a person.
    "HTTP-Referer": "https://github.com/seungsukim127/llm-price-elasticity",
    "X-Title": "llm-price-elasticity research collector",
}


def _utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _write_gz_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, sort_keys=True)


def _get(url: str, params: dict | None = None, auth: bool = False,
         retries: int = 4, timeout: int = 60) -> dict:
    headers = dict(HEADERS)
    if auth:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set (needed for rankings).")
        headers["Authorization"] = f"Bearer {key}"
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:  # ValueError = bad JSON
            last_err = e
            time.sleep(2 ** attempt * 5)
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_err}")


# --------------------------------------------------------------------------- #
# Models (prices)
# --------------------------------------------------------------------------- #
def snapshot_models(today: dt.date | None = None) -> Path:
    today = today or _utc_today()
    payload = _get(f"{BASE_URL}/models")
    if not isinstance(payload.get("data"), list) or not payload["data"]:
        raise RuntimeError("Models API returned no data; schema may have changed.")
    payload["_collected_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    out = RAW / "models" / f"{today.isoformat()}.json.gz"
    _write_gz_json(out, payload)
    print(f"[models] {len(payload['data'])} models -> {out.relative_to(ROOT)}")
    return out


# --------------------------------------------------------------------------- #
# Rankings (usage)
# --------------------------------------------------------------------------- #
def fetch_rankings(start: dt.date, end: dt.date) -> Path:
    """Fetch one window (<= 366 days). File is named by collection date and
    window so later restatements are kept alongside earlier versions."""
    params = {"start_date": start.isoformat(), "end_date": end.isoformat(), "period": "day"}
    payload = _get(f"{BASE_URL}/datasets/rankings-daily", params=params, auth=True)
    if "data" not in payload or "meta" not in payload:
        raise RuntimeError("Rankings API response missing data/meta; schema may have changed.")
    payload["_collected_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    stamp = _utc_today().isoformat()
    out = RAW / "rankings" / f"{stamp}_{start.isoformat()}_{end.isoformat()}.json.gz"
    _write_gz_json(out, payload)
    print(f"[rankings] {len(payload['data'])} rows {start}..{end} -> {out.relative_to(ROOT)}")
    return out


def backfill(start: dt.date = DATASET_FLOOR, end: dt.date | None = None) -> list[Path]:
    end = end or (_utc_today() - dt.timedelta(days=1))
    start = max(start, DATASET_FLOOR)
    outs = []
    cur = start
    while cur <= end:
        win_end = min(cur + dt.timedelta(days=MAX_SPAN_DAYS - 1), end)
        outs.append(fetch_rankings(cur, win_end))
        cur = win_end + dt.timedelta(days=1)
        time.sleep(3)  # stay far below 30 req/min
    return outs


def daily() -> None:
    """Run once per day (GitHub Actions). Models snapshot + 7-day rankings
    window; the overlap captures late restatements of recent days."""
    errors = []
    try:
        snapshot_models()
    except Exception as e:  # keep going so one failure doesn't lose the other
        errors.append(f"models: {e}")
    try:
        end = _utc_today() - dt.timedelta(days=1)
        fetch_rankings(end - dt.timedelta(days=6), end)
    except Exception as e:
        errors.append(f"rankings: {e}")
    if errors:
        # Non-zero exit makes the workflow fail -> GitHub emails the repo owner.
        print("COLLECTION ERRORS:\n  " + "\n  ".join(errors), file=sys.stderr)
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daily")
    sub.add_parser("models")
    b = sub.add_parser("backfill")
    b.add_argument("--start", type=dt.date.fromisoformat, default=DATASET_FLOOR)
    b.add_argument("--end", type=dt.date.fromisoformat, default=None)
    a = p.parse_args(argv)
    if a.cmd == "daily":
        daily()
    elif a.cmd == "models":
        snapshot_models()
    else:
        backfill(a.start, a.end)


if __name__ == "__main__":
    main()
