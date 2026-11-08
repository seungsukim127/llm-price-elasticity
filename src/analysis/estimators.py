"""Estimators for the four analysis steps.

All regressions absorb fixed effects by alternating projections (repeated
group-demeaning; Frisch-Waugh-Lovell) instead of building thousands of dummy
columns, then run OLS with standard errors clustered by model.

Step 1  cross_model()        log Q_it = eps*log P_it + date FE
Step 2  within_model()       log Q_it = eps*log P_it + model FE + date FE
Step 3  price_event_study()  stacked event study / DiD around price cuts
Step 4  free_event_study()   stacked DiD around :free variant added / removed
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import statsmodels.api as sm


# --------------------------------------------------------------------------- #
# Core machinery
# --------------------------------------------------------------------------- #
def absorb(df: pd.DataFrame, cols: list[str], fes: list[str],
           tol: float = 1e-10, maxiter: int = 1000) -> pd.DataFrame:
    """Residualise `cols` on the fixed effects `fes` (alternating projections)."""
    out = df[cols].astype(float).copy()
    if not fes:
        return out
    if len(fes) == 1:
        return out - out.groupby(df[fes[0]]).transform("mean")
    for _ in range(maxiter):
        prev = out.copy()
        for fe in fes:
            out = out - out.groupby(df[fe]).transform("mean")
        if np.max(np.abs(out.values - prev.values)) < tol:
            break
    return out


@dataclass
class Fit:
    name: str
    coef: pd.Series
    se: pd.Series
    n_obs: int
    n_clusters: int
    cov: pd.DataFrame = field(repr=False)
    extra: dict = field(default_factory=dict)

    def ci(self, z: float = 1.96) -> pd.DataFrame:
        return pd.DataFrame({"coef": self.coef, "se": self.se,
                             "lo": self.coef - z * self.se, "hi": self.coef + z * self.se})

    def wald(self, names: list[str]) -> tuple[float, float]:
        """Joint test that coefficients `names` are all zero (cluster-robust)."""
        from scipy import stats
        b = self.coef[names].values
        V = self.cov.loc[names, names].values
        stat = float(b @ np.linalg.pinv(V) @ b)
        q = len(names)
        G = self.n_clusters
        F = stat / q
        return F, float(stats.f.sf(F, q, max(G - 1, 1)))


def ols_fe(df: pd.DataFrame, y: str, xs: list[str], fes: list[str],
           cluster: str, name: str = "") -> Fit:
    d = df.dropna(subset=[y, *xs]).copy()
    r = absorb(d, [y, *xs], fes)
    groups = pd.factorize(d[cluster])[0]
    res = sm.OLS(r[y].values, r[xs].values).fit(cov_type="cluster",
                                                 cov_kwds={"groups": groups})
    coef = pd.Series(res.params, index=xs)
    cov = pd.DataFrame(res.cov_params(), index=xs, columns=xs)
    return Fit(name, coef, pd.Series(res.bse, index=xs), len(d),
               int(groups.max() + 1), cov)


def _paid_sample(panel: pd.DataFrame, allow_assumed_prices: bool) -> pd.DataFrame:
    p = panel[(~panel.is_free) & panel.log_p.notna() & panel.log_q.notna()]
    if not allow_assumed_prices and "price_source" in p:
        p = p[p.price_source != "assumed"]
    return p


# --------------------------------------------------------------------------- #
# Steps 1-2: panel regressions
# --------------------------------------------------------------------------- #
def cross_model(panel: pd.DataFrame, allow_assumed_prices: bool = False) -> Fit:
    d = _paid_sample(panel, allow_assumed_prices)
    return ols_fe(d, "log_q", ["log_p"], ["date"], "permaslug", "cross-model (date FE)")


def within_model(panel: pd.DataFrame, allow_assumed_prices: bool = False) -> Fit:
    d = _paid_sample(panel, allow_assumed_prices)
    return ols_fe(d, "log_q", ["log_p"], ["permaslug", "date"], "permaslug",
                  "within-model (model + date FE)")


# --------------------------------------------------------------------------- #
# Steps 3-4: stacked event studies
# --------------------------------------------------------------------------- #
def _coverage(panel: pd.DataFrame, slug: str, t0: pd.Timestamp, W: int) -> float:
    days = panel.loc[(panel.permaslug == slug)
                     & panel.date.between(t0 - pd.Timedelta(days=W), t0 + pd.Timedelta(days=W)), "date"]
    return days.nunique() / (2 * W + 1)


def build_stack(outcome: pd.DataFrame, events: pd.DataFrame, candidates: pd.DataFrame,
                W: int, min_coverage: float) -> pd.DataFrame:
    """Generic stacker.

    outcome     columns [unit, date, y]
    events      columns [event_id, unit, date, dose]   (dose e.g. dlog p)
    candidates  columns [event_id, unit]  -> clean controls per event
    """
    rows = []
    for ev in events.itertuples(index=False):
        lo, hi = ev.date - pd.Timedelta(days=W), ev.date + pd.Timedelta(days=W)
        units = [ev.unit] + candidates.loc[candidates.event_id == ev.event_id, "unit"].tolist()
        o = outcome[outcome.unit.isin(units) & outcome.date.between(lo, hi)].copy()
        cov = o.groupby("unit").date.nunique() / (2 * W + 1)
        keep = cov[cov >= min_coverage].index
        if ev.unit not in keep:
            continue
        o = o[o.unit.isin(keep)]
        if o.unit.nunique() < 2:
            continue
        o["event_id"] = ev.event_id
        o["rel"] = (o.date - ev.date).dt.days
        o["treated"] = (o.unit == ev.unit).astype(int)
        o["post"] = (o.rel >= 0).astype(int)
        o["dose"] = ev.dose
        rows.append(o)
    if not rows:
        return pd.DataFrame()
    s = pd.concat(rows, ignore_index=True)
    s["ev_unit"] = s.event_id.astype(str) + "|" + s.unit
    s["ev_rel"] = s.event_id.astype(str) + "|" + s.rel.astype(str)
    s["tp"] = s.treated * s.post
    s["tp_dose"] = s.tp * s.dose
    return s


def fit_stack(stack: pd.DataFrame, W: int, name: str) -> dict:
    fes = ["ev_unit", "ev_rel"]
    did = ols_fe(stack, "y", ["tp"], fes, "unit", f"{name}: DiD")
    dose = ols_fe(stack, "y", ["tp_dose"], fes, "unit", f"{name}: elasticity")
    # event-study coefficients, reference day = -1
    rels = [k for k in range(-W, W + 1) if k != -1]
    es = stack.copy()
    names = []
    for k in rels:
        c = f"d{k}"
        es[c] = ((es.rel == k) & (es.treated == 1)).astype(float)
        names.append(c)
    esfit = ols_fe(es, "y", names, fes, "unit", f"{name}: event study")
    # Pre-trend check: ONE coefficient = differential daily trend of the treated
    # unit before the event. (A joint Wald test on all ~27 lead dummies
    # over-rejects badly when only a handful of events are treated.)
    pre = stack[stack.rel < 0].copy()
    pre["trend"] = pre.treated * pre.rel
    pt = ols_fe(pre, "y", ["trend"], fes, "unit", f"{name}: pre-trend")
    from scipy import stats
    t = pt.coef["trend"] / pt.se["trend"]
    F, p = float(pt.coef["trend"]), float(2 * stats.t.sf(abs(t), max(pt.n_clusters - 1, 1)))
    path = esfit.ci()
    path.index = rels
    path.loc[-1] = [0.0, 0.0, 0.0, 0.0]
    return {"did": did, "dose": dose, "path": path.sort_index(),
            "pretrend_slope": F, "pretrend_p": p,
            "n_events": stack.event_id.nunique(),
            "n_controls_avg": stack[stack.treated == 0].groupby("event_id").unit.nunique().mean()}


def cluster_bootstrap(stack: pd.DataFrame, coef: str, n: int = 300, seed: int = 0) -> tuple[float, float]:
    """Resample whole events with replacement; 95% percentile interval for `coef`."""
    rng = np.random.default_rng(seed)
    ids = stack.event_id.unique()
    draws = []
    for b in range(n):
        pick = rng.choice(ids, size=len(ids), replace=True)
        parts = []
        for j, e in enumerate(pick):
            s = stack[stack.event_id == e].copy()
            s["ev_unit"] = f"{j}|" + s.unit
            s["ev_rel"] = f"{j}|" + s.rel.astype(str)
            parts.append(s)
        bs = pd.concat(parts, ignore_index=True)
        try:
            draws.append(ols_fe(bs, "y", [coef], ["ev_unit", "ev_rel"], "unit").coef[coef])
        except Exception:
            continue
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def _event_windows_overlap(dates: pd.Series, t0: pd.Timestamp, W: int) -> bool:
    return bool(((dates - t0).abs() <= pd.Timedelta(days=2 * W)).any())


def price_event_study(panel: pd.DataFrame, price_events: pd.DataFrame, W: int = 14,
                      min_cut: float = 0.10, min_coverage: float = 0.8,
                      min_age_days: int = 30, tier_ratio: float | None = 4.0) -> dict:
    """Treated: a paid model whose blended price falls by >= min_cut (in log points).
    Controls: paid models with no price change within +-2W days, not a variant
    of the treated model, released >= min_age_days before the window, and (if
    tier_ratio) priced within a factor tier_ratio of the treated model."""
    paid = panel[(~panel.is_free) & panel.log_q.notna()]
    outcome = paid.rename(columns={"permaslug": "unit", "log_q": "y"})[["unit", "date", "y"]]
    ev = price_events.copy()
    ev["date"] = pd.to_datetime(ev.date)
    cuts = ev[ev.dlogp <= -min_cut].reset_index(drop=True)
    created = panel.groupby("permaslug").created.min()
    base = panel.groupby("permaslug").base_slug.first()
    pre_price = (panel.dropna(subset=["log_p"]).set_index(["permaslug", "date"]).log_p)

    keep_ev, cand = [], []
    for i, e in cuts.iterrows():
        own = ev[(ev.permaslug == e.permaslug) & (ev.date != e.date)].date
        if _event_windows_overlap(own, e.date, W):
            continue                              # contaminated by another own change
        c = created.get(e.permaslug, pd.NaT)
        if pd.notna(c) and (e.date - pd.Timedelta(days=W) - c).days < min_age_days:
            continue                              # launch period
        keep_ev.append({"event_id": i, "unit": e.permaslug, "date": e.date, "dose": e.dlogp})
        lp_t = np.log(e.p_before)
        for slug in paid.permaslug.unique():
            if slug == e.permaslug or base.get(slug) == base.get(e.permaslug):
                continue
            if _event_windows_overlap(ev.loc[ev.permaslug == slug, "date"], e.date, W):
                continue
            c = created.get(slug, pd.NaT)
            if pd.notna(c) and (e.date - pd.Timedelta(days=W) - c).days < min_age_days:
                continue
            if tier_ratio:
                try:
                    lp_c = pre_price.loc[slug].loc[:e.date - pd.Timedelta(days=1)].iloc[-1]
                except (KeyError, IndexError):
                    continue
                if abs(lp_c - lp_t) > np.log(tier_ratio):
                    continue
            cand.append({"event_id": i, "unit": slug})
    events = pd.DataFrame(keep_ev, columns=["event_id", "unit", "date", "dose"])
    stack = build_stack(outcome, events, pd.DataFrame(cand, columns=["event_id", "unit"]),
                        W, min_coverage)
    if stack.empty:
        return {"stack": stack, "n_events": 0}
    out = fit_stack(stack, W, "price cut")
    out["stack"] = stack
    out["mean_dlogp"] = stack.loc[stack.treated == 1].groupby("event_id").dose.first().mean()
    return out


def free_event_study(panel: pd.DataFrame, free_events: pd.DataFrame, price_events: pd.DataFrame,
                     W: int = 14, min_coverage: float = 0.8) -> dict:
    """Unit = base model. Two outcomes:
         paid   log tokens of the paid variant
         total  log(paid + free) tokens   (free counted as 0 when not in the top 50,
                so 'total' is a lower bound while the free variant exists)
    Run separately for free_added and free_removed."""
    p = panel.copy()
    paid = (p[~p.is_free].groupby(["base_slug", "date"]).tokens.sum().rename("paid"))
    free = (p[p.is_free].groupby(["base_slug", "date"]).tokens.sum().rename("free"))
    agg = pd.concat([paid, free], axis=1).reset_index()
    agg = agg[agg.paid.notna()]
    agg["free"] = agg.free.fillna(0.0)
    agg["log_paid"] = np.log(agg.paid)
    agg["log_total"] = np.log(agg.paid + agg.free)

    fe = free_events.copy()
    fe["date"] = pd.to_datetime(fe.date)
    pe = price_events.copy()
    pe["date"] = pd.to_datetime(pe.date)
    pe_base = pe.groupby("base_slug").date.apply(list).to_dict()
    fe_base = fe.groupby("base_slug").date.apply(list).to_dict()

    results = {}
    for kind in ["free_added", "free_removed"]:
        ks = fe[fe.kind == kind].reset_index(drop=True)
        events, cand = [], []
        for i, e in ks.iterrows():
            if _event_windows_overlap(pd.Series(pe_base.get(e.base_slug, []), dtype="datetime64[ns]"), e.date, W):
                continue
            events.append({"event_id": i, "unit": e.base_slug, "date": e.date, "dose": 1.0})
            for b in agg.base_slug.unique():
                if b == e.base_slug:
                    continue
                if _event_windows_overlap(pd.Series(fe_base.get(b, []) + pe_base.get(b, []),
                                                    dtype="datetime64[ns]"), e.date, W):
                    continue
                cand.append({"event_id": i, "unit": b})
        events = pd.DataFrame(events, columns=["event_id", "unit", "date", "dose"])
        cand = pd.DataFrame(cand, columns=["event_id", "unit"])
        res = {}
        for outcome_name, col in [("paid", "log_paid"), ("total", "log_total")]:
            o = agg.rename(columns={"base_slug": "unit", col: "y"})[["unit", "date", "y"]]
            stack = build_stack(o, events, cand, W, min_coverage)
            if stack.empty:
                res[outcome_name] = {"n_events": 0}
                continue
            r = fit_stack(stack, W, f"{kind}/{outcome_name}")
            r["stack"] = stack
            res[outcome_name] = r
        results[kind] = res
    return results
