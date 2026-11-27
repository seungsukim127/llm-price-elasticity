"""Load raw snapshots into a DuckDB database with SQL.

The raw .json.gz files stay the source of truth; this database is rebuilt
from them and is not committed (data/processed/ is git-ignored).

Tables:
    usage_raw  every (date, model) row from every rankings file, duplicates included
    usage      one row per (date, model): the newest version (latest as_of)
    prices     one row per (snapshot date, model): input/output/blended price
    market     total tokens per day (top 50 + "other")
    panel      usage joined to prices and market, with logs (snapshot days only;
               build_panel.py still fills the days before and between snapshots)
    price_changes  paid models whose blended price moved by more than 1% between
               neighbouring snapshots (same rule as build_panel.detect_price_events)

    python -m src.build_db
    python -m src.build_db --raw data_demo/raw --out data_demo/processed/llm.duckdb
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
BLEND_INPUT_SHARE = 0.75     # same 3:1 input:output mix as build_panel.py
MIN_PRICE_CHANGE = 0.01      # |dlog p| below this is rounding noise (as in build_panel.py)
MAX_GAP_DAYS = 4             # snapshots further apart are not neighbours (MAX_FFILL_DAYS + 1)


def build_db(raw: Path, out: Path) -> duckdb.DuckDBPyConnection:
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(out))

    # Every row of every rankings file. The same day appears in several files
    # because each daily run re-fetches the last 7 days.
    con.sql(f"""
        CREATE OR REPLACE TABLE usage_raw AS
        SELECT
            r.meta.as_of                   AS as_of,
            CAST(d.date AS DATE)           AS date,
            d.model_permaslug              AS permaslug,
            CAST(d.total_tokens AS DOUBLE) AS tokens
        FROM read_json('{raw}/rankings/*.json.gz', maximum_object_size = 200000000) AS r,
             unnest(r.data) AS t(d)
    """)

    # Restatements: keep the newest version of each (date, model),
    # the same rule as build_panel.parse_rankings.
    con.sql("""
        CREATE OR REPLACE TABLE usage AS
        SELECT date, permaslug, tokens, as_of
        FROM usage_raw
        QUALIFY ROW_NUMBER() OVER (PARTITION BY date, permaslug ORDER BY as_of DESC) = 1
    """)

    # Prices: one row per model per snapshot day. The date comes from the file
    # name; prices are $ per token, so x 1e6 gives $ per 1M tokens.
    # Negative prices (e.g. -1 for router models) are not real prices -> NULL.
    con.sql(f"""
        CREATE OR REPLACE TABLE prices AS
        WITH snap AS (
            SELECT
                CAST(regexp_extract(r.filename, '(\\d{{4}}-\\d{{2}}-\\d{{2}})\\.json\\.gz$', 1) AS DATE) AS date,
                m.id                                               AS model_id,
                COALESCE(m.canonical_slug, m.id)                   AS slug,
                CASE WHEN m.id LIKE '%:%' THEN split_part(m.id, ':', 2) ELSE '' END AS variant,
                TRY_CAST(m.pricing.prompt AS DOUBLE) * 1e6         AS p_in,
                TRY_CAST(m.pricing.completion AS DOUBLE) * 1e6     AS p_out,
                i                                                  AS pos
            FROM read_json('{raw}/models/*.json.gz', filename = true,
                           columns = {{'data': 'STRUCT(id VARCHAR, canonical_slug VARCHAR,
                                       pricing STRUCT(prompt VARCHAR, completion VARCHAR))[]'}}) AS r,
                 unnest(r.data) WITH ORDINALITY AS t(m, i)
        ),
        cleaned AS (
            SELECT
                date, model_id, variant,
                CASE WHEN variant <> '' AND slug NOT LIKE '%:%'
                     THEN slug || ':' || variant ELSE slug END   AS permaslug,
                CASE WHEN p_in < 0 OR p_out < 0 THEN NULL ELSE p_in  END AS price_in,
                CASE WHEN p_in < 0 OR p_out < 0 THEN NULL ELSE p_out END AS price_out,
                pos
            FROM snap
        )
        SELECT
            date, permaslug, model_id, variant, price_in, price_out,
            {BLEND_INPUT_SHARE} * price_in + {1 - BLEND_INPUT_SHARE} * price_out AS price_blend
        FROM cleaned
        QUALIFY ROW_NUMBER() OVER (PARTITION BY date, permaslug ORDER BY pos DESC) = 1
    """)

    # Market size: total tokens per day, including the aggregated "other" row.
    con.sql("""
        CREATE OR REPLACE TABLE market AS
        SELECT date, sum(tokens) AS market_tokens
        FROM usage
        GROUP BY date
    """)

    # Panel: top-50 usage, with that day's price (if a snapshot exists) and
    # the market total. LEFT JOIN keeps usage rows that have no price.
    con.sql("""
        CREATE OR REPLACE TABLE panel AS
        SELECT
            u.date, u.permaslug, u.tokens,
            p.price_in, p.price_out, p.price_blend,
            m.market_tokens,
            u.tokens / m.market_tokens                                AS share,
            ln(u.tokens)                                              AS log_q,
            CASE WHEN p.price_blend > 0 THEN ln(p.price_blend) END    AS log_p
        FROM usage AS u
        LEFT JOIN prices AS p ON p.date = u.date AND p.permaslug = u.permaslug
        LEFT JOIN market AS m ON m.date = u.date
        WHERE u.permaslug <> 'other'
    """)

    # Price changes: compare each snapshot with the same model's previous one.
    # LAG looks one row back within each model, ordered by date. Changes under
    # 1% are rounding noise; a gap of more than 4 days is not a real neighbour.
    con.sql(f"""
        CREATE OR REPLACE TABLE price_changes AS
        WITH paid AS (
            SELECT date, permaslug, price_blend
            FROM prices
            WHERE variant <> 'free' AND price_blend > 0
        ),
        with_prev AS (
            SELECT
                permaslug, date, price_blend,
                LAG(date)        OVER (PARTITION BY permaslug ORDER BY date) AS prev_date,
                LAG(price_blend) OVER (PARTITION BY permaslug ORDER BY date) AS prev_price
            FROM paid
        )
        SELECT
            permaslug, date, prev_date,
            prev_price                       AS p_before,
            price_blend                      AS p_after,
            ln(price_blend) - ln(prev_price) AS dlogp
        FROM with_prev
        WHERE prev_price IS NOT NULL
          AND abs(ln(price_blend) - ln(prev_price)) > {MIN_PRICE_CHANGE}
          AND date - prev_date <= {MAX_GAP_DAYS}
        ORDER BY date, permaslug
    """)

    n_raw = con.sql("SELECT count(*) FROM usage_raw").fetchall()[0][0]
    n = con.sql("SELECT count(*) FROM usage").fetchall()[0][0]
    print(f"[db] usage: {n:,} rows ({n_raw - n:,} restated duplicates dropped) -> {out}")
    for t in ["prices", "market", "panel", "price_changes"]:
        print(f"[db] {t}: {con.sql(f'SELECT count(*) FROM {t}').fetchall()[0][0]:,} rows")
    return con


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "processed" / "llm.duckdb")
    a = ap.parse_args(argv)
    build_db(a.raw, a.out).close()


if __name__ == "__main__":
    main()
