"""Load raw snapshots into a DuckDB database with SQL.

The raw .json.gz files stay the source of truth; this database is rebuilt
from them and is not committed (data/processed/ is git-ignored).

Tables:
    usage_raw  every (date, model) row from every rankings file, duplicates included
    usage      one row per (date, model): the newest version (latest as_of)

    python -m src.build_db
    python -m src.build_db --raw data_demo/raw --out data_demo/processed/llm.duckdb
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]


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

    n_raw = con.sql("SELECT count(*) FROM usage_raw").fetchall()[0][0]
    n = con.sql("SELECT count(*) FROM usage").fetchall()[0][0]
    print(f"[db] usage: {n:,} rows ({n_raw - n:,} restated duplicates dropped) -> {out}")
    return con


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "processed" / "llm.duckdb")
    a = ap.parse_args(argv)
    build_db(a.raw, a.out).close()


if __name__ == "__main__":
    main()
