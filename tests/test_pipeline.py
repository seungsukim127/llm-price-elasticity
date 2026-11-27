"""End-to-end check: estimators must recover parameters planted in synthetic data."""
import gzip
import json
import math

import pandas as pd
import pytest

from src import build_panel, collect, simulate
from src.analysis import estimators as est


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("sim")
    truth = simulate.simulate(root / "raw", seed=7)
    tables = build_panel.build_panel(root / "raw", root / "processed", root / "manual")
    return truth, tables


# Within-model must land near the truth; cross-model must miss it by a lot,
# which also confirms that the simulation's confound is strong enough to matter.
def test_within_beats_cross(built):
    truth, t = built
    cross = est.cross_model(t["panel"]).coef["log_p"]
    within = est.within_model(t["panel"]).coef["log_p"]
    assert abs(within - truth["eps_true"]) < 0.2
    assert abs(cross - truth["eps_true"]) > 0.5      # omitted quality biases it toward zero


def test_event_study_recovers_elasticity(built):
    truth, t = built
    r = est.price_event_study(t["panel"], t["price_events"])
    assert r["n_events"] >= 5
    assert abs(r["dose"].coef["tp_dose"] - truth["eps_true"]) < 0.3
    assert r["pretrend_p"] > 0.01


def test_free_variant_substitution(built):
    truth, t = built
    r = est.free_event_study(t["panel"], t["free_events"], t["price_events"])
    added = r["free_added"]["paid"]["did"].coef["tp"]
    removed = r["free_removed"]["paid"]["did"].coef["tp"]
    assert added < 0 < removed
    assert abs(added - truth["free_subst"]) < 0.15


def test_prices_before_snapshots_use_manual_file(built):
    _, t = built
    p = t["prices"]
    assert {"snapshot", "manual"} <= set(p.price_source.unique())


def test_negative_router_prices_are_dropped(built):
    _, t = built
    auto = t["prices"][t["prices"].permaslug == "openrouter/auto"]
    assert auto.price_blend.isna().all()


def test_restatement_keeps_latest(tmp_path):
    (tmp_path / "rankings").mkdir()
    for as_of, tok in [("2026-11-02T02:00:00Z", "100"), ("2026-11-03T02:00:00Z", "150")]:
        payload = {"data": [{"date": "2026-11-01", "model_permaslug": "a/b", "total_tokens": tok}],
                   "meta": {"as_of": as_of, "version": "v1", "start_date": "2026-11-01", "end_date": "2026-11-01"}}
        with gzip.open(tmp_path / "rankings" / f"{as_of[:10]}.json.gz", "wt") as f:
            json.dump(payload, f)
    u = build_panel.parse_rankings(tmp_path)
    assert u.tokens.tolist() == [150.0]


def test_collect_writes_raw_response(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "RAW", tmp_path)
    monkeypatch.setattr(collect, "ROOT", tmp_path)
    fake = {"data": [{"id": "a/b", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]}
    monkeypatch.setattr(collect, "_get", lambda *a, **k: dict(fake))
    out = collect.snapshot_models(pd.Timestamp("2026-11-10").date())
    with gzip.open(out, "rt") as f:
        saved = json.load(f)
    assert saved["data"] == fake["data"] and "_collected_at" in saved


def test_site_builds_from_synthetic_data(tmp_path):
    from src import build_site
    root = tmp_path / "sim"
    simulate.simulate(root / "raw", seed=3)
    data = build_site.build_data(root / "raw", root / "manual", root / "processed")
    page = build_site.render(data, tmp_path / "_site")
    text = page.read_text(encoding="utf-8")
    assert "{{" not in text                               # every placeholder filled
    assert len(data["top"]) == 20
    assert abs(sum(s["values"][-1] for s in data["share"]["series"]) - 100) < 0.5
    assert (tmp_path / "_site" / "chart.umd.min.js").exists()


# Issue #2: a short price spell that comes back is noise; only a move from one
# stable price (held 7+ days) to another counts as a price change.
def test_price_events_skip_short_spells():
    dates = pd.date_range("2026-11-01", periods=30, freq="D")
    price = [1.0] * 10 + [0.5] * 2 + [1.0] * 8 + [0.6] * 10
    prices = pd.DataFrame({"permaslug": "a/b", "base_slug": "a/b", "date": dates,
                           "price_blend": price, "price_source": "snapshot", "is_free": False})
    ev = build_panel.detect_price_events(prices, pd.DataFrame())
    assert len(ev) == 1
    assert ev.date.iloc[0] == pd.Timestamp("2026-11-21")
    assert abs(ev.dlogp.iloc[0] - math.log(0.6)) < 1e-9


# Issue #21: per-provider prices are saved unmodified for the latest top models
# and for models whose listed price changed; one failing model does not stop the rest.
def test_collect_endpoints_saves_raw_and_skips_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "RAW", tmp_path)
    monkeypatch.setattr(collect, "ROOT", tmp_path)
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)

    def write(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt") as f:
            json.dump(payload, f)

    def model(mid, price):
        return {"id": mid, "canonical_slug": f"{mid}-2026", "pricing": {"prompt": price, "completion": price}}

    write(tmp_path / "models" / "2026-11-09.json.gz",
          {"data": [model("a/top", "1"), model("b/moved", "1"), model("c/same", "1")]})
    write(tmp_path / "models" / "2026-11-10.json.gz",
          {"data": [model("a/top", "1"), model("b/moved", "2"), model("c/same", "1")]})
    write(tmp_path / "rankings" / "2026-11-10_2026-11-09_2026-11-09.json.gz",
          {"data": [{"date": "2026-11-09", "model_permaslug": "a/top-2026", "total_tokens": "100"},
                    {"date": "2026-11-09", "model_permaslug": "other", "total_tokens": "999"}],
           "meta": {}})
    fake = {"data": {"id": "a/top", "endpoints": [{"provider_name": "X", "pricing": {"prompt": "1"}}]}}

    def fake_get(url, *a, **k):
        if "b/moved" in url:
            raise RuntimeError("boom")
        return dict(fake)

    monkeypatch.setattr(collect, "_get", fake_get)
    out = collect.snapshot_endpoints(pd.Timestamp("2026-11-10").date())
    with gzip.open(out, "rt") as f:
        saved = json.load(f)
    assert saved["models"] == {"a/top": fake}           # top model, saved unmodified
    assert list(saved["errors"]) == ["b/moved"]         # price changed, request failed
    assert "c/same" not in saved["models"]              # neither top nor changed
    assert "_collected_at" in saved
