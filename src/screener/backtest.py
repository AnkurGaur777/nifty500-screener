"""Look-ahead-aware backtesting engine, point-in-time scoring, and placebo evaluation."""

import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.screener.config import (
    CONFIG_DIR,
    INTERIM_DIR,
    RAW_DIR,
    configure_logging,
    get_database_url,
)
from src.screener.scoring import (
    ALL_SCORING_METRICS,
    FACTOR_DEFINITIONS,
    INVERTED_METRICS,
    assign_peer_groups,
    load_scoring_weights,
    winsorize_series,
)

logger = logging.getLogger(__name__)

COHORT_RETURNS_FILE = INTERIM_DIR / "backtest_cohort_returns.csv"
BACKTEST_SUMMARY_FILE = INTERIM_DIR / "backtest_summary.csv"


def get_available_date(fiscal_year: int) -> date:
    """Return conservative availability date (June 30) for Indian fiscal year."""
    return date(fiscal_year, 6, 30)


def is_data_available(fiscal_year: int, formation_date: date) -> bool:
    """Check whether fiscal year data is available at the formation date."""
    return formation_date >= get_available_date(fiscal_year)


def load_benchmark_prices(benchmark_file: Path | None = None) -> pd.DataFrame:
    """Load monthly adjusted close benchmark prices from parquet file."""
    if benchmark_file is not None and benchmark_file.exists():
        target = benchmark_file
    else:
        nifty500_file = RAW_DIR / "benchmark" / "nifty500_monthly.parquet"
        nifty50_file = RAW_DIR / "benchmark" / "nifty50_monthly.parquet"
        target = nifty500_file if nifty500_file.exists() else nifty50_file

    if not target.exists():
        logger.warning("Benchmark price file not found at %s", target)
        return pd.DataFrame(columns=["date", "adj_close"])

    try:
        df = pd.read_parquet(target)
    except Exception as exc:
        logger.warning("Failed loading benchmark file %s: %s", target, exc)
        return pd.DataFrame(columns=["date", "adj_close"])

    if df.empty:
        return pd.DataFrame(columns=["date", "adj_close"])

    date_col = "date" if "date" in df.columns else "Date"
    if date_col not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            date_col = df.columns[0]
        else:
            return pd.DataFrame(columns=["date", "adj_close"])

    close_col = "adj_close" if "adj_close" in df.columns else "close"
    if close_col not in df.columns:
        matches = [c for c in df.columns if c.lower() in ("close", "adj_close", "adj close")]
        if matches:
            close_col = matches[0]
        else:
            return pd.DataFrame(columns=["date", "adj_close"])

    res = df[[date_col, close_col]].dropna().copy()
    res["date"] = pd.to_datetime(res[date_col]).dt.date
    res["adj_close"] = res[close_col].astype(float)
    return res[["date", "adj_close"]].sort_values("date").reset_index(drop=True)


def load_backtest_dataset(engine: Engine) -> dict[str, pd.DataFrame]:
    """Retrieve non-financial companies, annual metrics, financials, prices, and market data."""
    companies_query = text(
        """
        select company_id, symbol, name, sector, industry
        from dim_company
        where is_financial = false
        order by company_id;
        """
    )
    metrics_query = text(
        """
        select
            company_id, fiscal_year, roe, roce, operating_margin,
            net_margin, debt_to_equity, interest_coverage, net_debt_to_ebitda,
            cfo_to_net_profit, fcf_cr, fcf_margin, receivable_days,
            inventory_days, asset_turnover, eps_growth_yoy, piotroski_f_score
        from metrics_annual
        order by company_id, fiscal_year;
        """
    )
    financials_query = text(
        """
        select
            company_id, fiscal_year, revenue_cr, cost_of_revenue_cr,
            gross_profit_cr, ebit_cr, ebitda_cr, interest_expense_cr,
            net_income_cr, total_assets_cr, total_debt_cr,
            cash_and_equivalents_cr, shareholders_equity_cr,
            operating_cash_flow_cr, capital_expenditure_cr
        from v_financials_wide
        order by company_id, fiscal_year;
        """
    )
    prices_query = text(
        """
        select company_id, month_end, adj_close
        from fact_prices_monthly
        order by company_id, month_end;
        """
    )
    market_query = text(
        """
        select company_id, snapshot_date, market_cap, shares_outstanding
        from fact_market
        order by company_id;
        """
    )

    with engine.connect() as conn:
        return {
            "companies": pd.read_sql_query(companies_query, conn),
            "metrics_annual": pd.read_sql_query(metrics_query, conn),
            "financials_wide": pd.read_sql_query(financials_query, conn),
            "prices": pd.read_sql_query(prices_query, conn),
            "market": pd.read_sql_query(market_query, conn),
        }


def build_price_map(prices_df: pd.DataFrame) -> dict[tuple[int, int, int], float]:
    """Build fast lookup map of (company_id, year, month) -> adjusted close price."""
    price_map: dict[tuple[int, int, int], float] = {}
    if prices_df.empty:
        return price_map

    for _, row in prices_df.iterrows():
        try:
            cid = int(row["company_id"])
            dt = pd.to_datetime(row["month_end"]).date()
            p = float(row["adj_close"])
            if p > 0:
                price_map[(cid, dt.year, dt.month)] = p
        except (ValueError, TypeError):
            continue
    return price_map


def build_benchmark_map(benchmark_df: pd.DataFrame) -> dict[tuple[int, int], float]:
    """Build fast lookup map of (year, month) -> benchmark adjusted close price."""
    bm_map: dict[tuple[int, int], float] = {}
    if benchmark_df.empty:
        return bm_map

    for _, row in benchmark_df.iterrows():
        try:
            dt = pd.to_datetime(row["date"]).date()
            p = float(row["adj_close"])
            if p > 0:
                bm_map[(dt.year, dt.month)] = p
        except (ValueError, TypeError):
            continue
    return bm_map


def compute_pit_market_cap(
    company_id: int,
    formation_date: date,
    price_map: dict[tuple[int, int, int], float],
    market_df: pd.DataFrame,
) -> float | None:
    """Compute point-in-time market capitalization in INR Crore at formation date."""
    p_form = price_map.get((company_id, formation_date.year, formation_date.month))
    if p_form is None or p_form <= 0:
        return None

    m_sub = market_df[market_df["company_id"] == company_id]
    if m_sub.empty:
        return None

    row = m_sub.iloc[0]
    shares = row.get("shares_outstanding")
    mcap = row.get("market_cap")

    if pd.notna(shares) and float(shares) > 0:
        return float(shares) * p_form / 1e7

    if pd.notna(mcap) and float(mcap) > 0:
        return float(mcap) / 1e7

    return None


def build_pit_cohort_metrics(
    formation_fy: int,
    formation_date: date,
    exit_date: date,
    data: dict[str, pd.DataFrame],
    price_map: dict[tuple[int, int, int], float],
) -> pd.DataFrame:
    """Build point-in-time metrics and forward returns for companies available at formation date."""
    companies_df = data.get("companies", pd.DataFrame())
    metrics_annual_df = data.get("metrics_annual", pd.DataFrame())
    financials_wide_df = data.get("financials_wide", pd.DataFrame())
    market_df = data.get("market", pd.DataFrame())

    rows: list[dict[str, Any]] = []

    for _, c_row in companies_df.iterrows():
        cid = int(c_row["company_id"])
        symbol = str(c_row["symbol"])
        sector = str(c_row["sector"])
        name = str(c_row["name"])

        p_entry = price_map.get((cid, formation_date.year, formation_date.month))
        p_exit = price_map.get((cid, exit_date.year, exit_date.month))

        if p_entry and p_exit and p_entry > 0:
            fwd_ret = (p_exit / p_entry) - 1.0
        else:
            fwd_ret = None

        if metrics_annual_df.empty:
            continue

        c_metrics = metrics_annual_df[
            (metrics_annual_df["company_id"] == cid)
            & (metrics_annual_df["fiscal_year"] <= formation_fy)
        ].sort_values("fiscal_year", ascending=False)

        if c_metrics.empty:
            continue

        recent_m = c_metrics.head(4)
        latest_m = recent_m.iloc[0]
        latest_fy = int(latest_m["fiscal_year"])
        earliest_fy = int(recent_m["fiscal_year"].iloc[-1])
        years_avail = len(recent_m)

        roce_mean = recent_m["roce"].mean()
        roe_mean = recent_m["roe"].mean()
        avg_roce_3yr = float(roce_mean) if pd.notna(roce_mean) else None
        avg_roe_3yr = float(roe_mean) if pd.notna(roe_mean) else None

        if years_avail >= 2:
            op_std = recent_m["operating_margin"].std(ddof=1)
            op_std_3yr = float(op_std) if pd.notna(op_std) else None
        else:
            op_std_3yr = None

        op_margin = (
            float(latest_m["operating_margin"])
            if pd.notna(latest_m["operating_margin"])
            else None
        )
        cfo_np = (
            float(latest_m["cfo_to_net_profit"])
            if pd.notna(latest_m["cfo_to_net_profit"])
            else None
        )
        piot_f = (
            float(latest_m["piotroski_f_score"])
            if pd.notna(latest_m["piotroski_f_score"])
            else None
        )
        eps_g = (
            float(latest_m["eps_growth_yoy"])
            if pd.notna(latest_m["eps_growth_yoy"])
            else None
        )
        de = (
            float(latest_m["debt_to_equity"])
            if pd.notna(latest_m["debt_to_equity"])
            else None
        )
        ic = (
            float(latest_m["interest_coverage"])
            if pd.notna(latest_m["interest_coverage"])
            else None
        )
        nd_eb = (
            float(latest_m["net_debt_to_ebitda"])
            if pd.notna(latest_m["net_debt_to_ebitda"])
            else None
        )
        fcf_m = latest_m.get("fcf_cr")
        fcf_latest = float(fcf_m) if pd.notna(fcf_m) else None

        c_fin = financials_wide_df[
            (financials_wide_df["company_id"] == cid)
            & (financials_wide_df["fiscal_year"] <= formation_fy)
        ].sort_values("fiscal_year", ascending=False)

        rev_cagr = None
        prof_cagr = None
        ni_latest = None
        eq_latest = None
        eb_latest = None
        debt_latest = 0.0
        cash_latest = 0.0
        fcf_latest = None

        if not c_fin.empty:
            recent_fin = c_fin.head(4)
            latest_f = recent_fin.iloc[0]
            earliest_f = recent_fin.iloc[-1]
            fy_span = latest_fy - earliest_fy

            rev_l = latest_f.get("revenue_cr")
            rev_e = earliest_f.get("revenue_cr")
            if (
                fy_span >= 2
                and pd.notna(rev_l)
                and pd.notna(rev_e)
                and rev_l > 0
                and rev_e > 0
            ):
                rev_cagr = (float(rev_l) / float(rev_e)) ** (1.0 / fy_span) - 1.0

            ni_l = latest_f.get("net_income_cr")
            ni_e = earliest_f.get("net_income_cr")
            if (
                fy_span >= 2
                and pd.notna(ni_l)
                and pd.notna(ni_e)
                and ni_l > 0
                and ni_e > 0
            ):
                prof_cagr = (float(ni_l) / float(ni_e)) ** (1.0 / fy_span) - 1.0

            ni_latest = float(ni_l) if pd.notna(ni_l) else None
            eq_val = latest_f.get("shareholders_equity_cr")
            eq_latest = float(eq_val) if pd.notna(eq_val) else None
            eb_val = latest_f.get("ebitda_cr")
            eb_latest = float(eb_val) if pd.notna(eb_val) else None
            d_val = latest_f.get("total_debt_cr")
            debt_latest = float(d_val) if pd.notna(d_val) else 0.0
            c_val = latest_f.get("cash_and_equivalents_cr")
            cash_latest = float(c_val) if pd.notna(c_val) else 0.0
            if fcf_latest is None:
                cfo_v = latest_f.get("operating_cash_flow_cr")
                capex_v = latest_f.get("capital_expenditure_cr")
                if pd.notna(cfo_v):
                    fcf_latest = float(cfo_v) - abs(float(capex_v or 0.0))

        mcap_pit = compute_pit_market_cap(cid, formation_date, price_map, market_df)
        if mcap_pit and mcap_pit > 0:
            ey = (ni_latest / mcap_pit * 100.0) if ni_latest is not None else None
            fcf_y = (fcf_latest / mcap_pit * 100.0) if fcf_latest is not None else None
            pb = (mcap_pit / eq_latest) if eq_latest is not None and eq_latest > 0 else None
            ev = mcap_pit + debt_latest - cash_latest
            ev_eb = (
                (ev / eb_latest)
                if eb_latest is not None and eb_latest > 0 and ev > 0
                else None
            )
        else:
            ey = None
            fcf_y = None
            pb = None
            ev_eb = None

        rows.append(
            {
                "company_id": cid,
                "symbol": symbol,
                "name": name,
                "sector": sector,
                "formation_fy": formation_fy,
                "formation_date": formation_date,
                "exit_date": exit_date,
                "entry_price": p_entry,
                "exit_price": p_exit,
                "fwd_return": fwd_ret,
                "years_available": years_avail,
                "avg_roce_3yr": avg_roce_3yr,
                "avg_roe_3yr": avg_roe_3yr,
                "operating_margin": op_margin,
                "cfo_to_net_profit": cfo_np,
                "piotroski_f_score": piot_f,
                "revenue_cagr_3yr": rev_cagr,
                "profit_cagr_3yr": prof_cagr,
                "eps_growth_yoy": eps_g,
                "earnings_yield": ey,
                "pb_ratio": pb,
                "ev_to_ebitda": ev_eb,
                "fcf_yield": fcf_y,
                "debt_to_equity": de,
                "interest_coverage": ic,
                "net_debt_to_ebitda": nd_eb,
                "operating_margin_std_3yr": op_std_3yr,
            }
        )

    return pd.DataFrame(rows)


def assign_quintiles(df: pd.DataFrame) -> pd.DataFrame:
    """Assign eligible companies into 5 equal quintiles (Q1 top 20% to Q5 bottom 20%)."""
    res = df.copy()
    eligible_mask = res["is_eligible"] == True  # noqa: E712
    res["quintile"] = None

    if not eligible_mask.any():
        return res

    eligible_df = res[eligible_mask].copy()
    pct = eligible_df["total_score"].rank(pct=True, ascending=False, method="first")

    conditions = [
        pct <= 0.20,
        (pct > 0.20) & (pct <= 0.40),
        (pct > 0.40) & (pct <= 0.60),
        (pct > 0.60) & (pct <= 0.80),
        pct > 0.80,
    ]
    choices = ["Q1", "Q2", "Q3", "Q4", "Q5"]
    res.loc[eligible_mask, "quintile"] = np.select(conditions, choices, default=None)
    return res


def score_backtest_cohort(
    cohort_df: pd.DataFrame,
    weights: dict[str, float],
) -> pd.DataFrame:
    """Score historical cohort with percentile ranking, factor scoring, and quintile allocation."""
    if cohort_df.empty:
        return cohort_df

    with_peers = assign_peer_groups(cohort_df, min_peers=8)

    for m in ALL_SCORING_METRICS:
        if m in with_peers.columns:
            with_peers[m] = winsorize_series(with_peers[m])

    for m in ALL_SCORING_METRICS:
        if m not in with_peers.columns:
            continue
        rank_col = f"{m}_rank"
        is_inv = m in INVERTED_METRICS

        def _rank(group: pd.Series, inverted: bool = is_inv) -> pd.Series:
            if group.count() == 0:
                return group
            pct = group.rank(pct=True, ascending=True) * 100.0
            return (100.0 - pct) if inverted else pct

        with_peers[rank_col] = with_peers.groupby("peer_group")[m].transform(_rank)

    for factor, metrics in FACTOR_DEFINITIONS.items():
        rank_cols = [f"{m}_rank" for m in metrics if f"{m}_rank" in with_peers.columns]
        sub = with_peers[rank_cols]
        valid_counts = sub.notna().sum(axis=1)
        mean_scores = sub.mean(axis=1, skipna=True)
        with_peers[f"{factor}_score"] = np.where(valid_counts >= 2, mean_scores, np.nan)

    factor_cols = ["quality_score", "growth_score", "value_score", "safety_score"]
    non_null_factors = with_peers[factor_cols].notna().sum(axis=1)
    has_returns = with_peers["fwd_return"].notna()
    with_peers["is_eligible"] = (non_null_factors >= 3) & has_returns

    eligible_mask = with_peers["is_eligible"] == True  # noqa: E712
    with_peers["total_score"] = np.nan

    if eligible_mask.any():
        for idx in with_peers[eligible_mask].index:
            active_factors = [f for f in factor_cols if pd.notna(with_peers.loc[idx, f])]
            w_sum = sum(weights[f.replace("_score", "")] for f in active_factors)
            if w_sum > 0:
                weighted_val = sum(
                    weights[f.replace("_score", "")] * with_peers.loc[idx, f]
                    for f in active_factors
                )
                with_peers.loc[idx, "total_score"] = round(weighted_val / w_sum, 2)

    return assign_quintiles(with_peers)


def compute_cohort_returns(
    formation_fy: int,
    formation_date: date,
    exit_date: date,
    scored_cohort_df: pd.DataFrame,
    bm_return: float,
) -> pd.DataFrame:
    """Compute equal-weighted return and excess return for each quintile in the cohort."""
    rows: list[dict[str, Any]] = []
    eligible = scored_cohort_df[scored_cohort_df["is_eligible"] == True]  # noqa: E712

    for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        sub = eligible[eligible["quintile"] == q]
        if sub.empty:
            continue
        q_ret = float(sub["fwd_return"].mean())
        count = len(sub)
        excess = q_ret - bm_return
        rows.append(
            {
                "cohort_fy": formation_fy,
                "formation_date": formation_date,
                "exit_date": exit_date,
                "quintile": q,
                "company_count": count,
                "return_pct": round(q_ret * 100.0, 2),
                "benchmark_return_pct": round(bm_return * 100.0, 2),
                "excess_return_pct": round(excess * 100.0, 2),
            }
        )
    return pd.DataFrame(rows)


def run_placebo_test(
    cohort_companies: list[pd.DataFrame],
    n_trials: int = 500,
    random_seed: int = 42,
) -> dict[str, float]:
    """Execute deterministic Monte Carlo placebo test by shuffling scores within cohorts."""
    rng = np.random.default_rng(random_seed)
    trial_spreads: list[float] = []

    valid_cohort_returns: list[np.ndarray] = []
    for cdf in cohort_companies:
        eligible = cdf[cdf["is_eligible"] == True]  # noqa: E712
        if len(eligible) >= 5:
            valid_cohort_returns.append(eligible["fwd_return"].to_numpy())

    if not valid_cohort_returns:
        return {"placebo_p5": 0.0, "placebo_p50": 0.0, "placebo_p95": 0.0}

    for _ in range(n_trials):
        cohort_diffs: list[float] = []
        for returns in valid_cohort_returns:
            n = len(returns)
            perm = rng.permutation(returns)
            q_size = max(1, int(round(n * 0.20)))
            q1_mean = float(np.mean(perm[:q_size]))
            q5_mean = float(np.mean(perm[-q_size:]))
            cohort_diffs.append(q1_mean - q5_mean)

        if cohort_diffs:
            trial_spreads.append(float(np.mean(cohort_diffs)))

    if not trial_spreads:
        return {"placebo_p5": 0.0, "placebo_p50": 0.0, "placebo_p95": 0.0}

    p5 = float(np.percentile(trial_spreads, 5))
    p50 = float(np.percentile(trial_spreads, 50))
    p95 = float(np.percentile(trial_spreads, 95))

    return {
        "placebo_p5": p5,
        "placebo_p50": p50,
        "placebo_p95": p95,
    }


def compute_backtest_summary(
    cohort_returns_df: pd.DataFrame,
    placebo_results: dict[str, float],
) -> pd.DataFrame:
    """Compute summary performance metrics across all evaluated backtest cohorts."""
    if cohort_returns_df.empty:
        return pd.DataFrame(
            columns=[
                "quintile",
                "mean_annual_return",
                "cumulative_return",
                "worst_period_return",
                "hit_rate_vs_benchmark",
            ]
        )

    rows: list[dict[str, Any]] = []

    for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        sub = cohort_returns_df[cohort_returns_df["quintile"] == q]
        if sub.empty:
            continue
        mean_ret = float(sub["return_pct"].mean())
        factors = 1.0 + (sub["return_pct"] / 100.0)
        cum_ret = float((np.prod(factors) - 1.0) * 100.0)
        worst_ret = float(sub["return_pct"].min())
        hit_rate = float((sub["return_pct"] > sub["benchmark_return_pct"]).mean() * 100.0)
        rows.append(
            {
                "quintile": q,
                "mean_annual_return": round(mean_ret, 2),
                "cumulative_return": round(cum_ret, 2),
                "worst_period_return": round(worst_ret, 2),
                "hit_rate_vs_benchmark": round(hit_rate, 1),
            }
        )

    bm_cohorts = cohort_returns_df.drop_duplicates(subset=["cohort_fy"])
    if not bm_cohorts.empty:
        bm_mean = float(bm_cohorts["benchmark_return_pct"].mean())
        bm_factors = 1.0 + (bm_cohorts["benchmark_return_pct"] / 100.0)
        bm_cum = float((np.prod(bm_factors) - 1.0) * 100.0)
        bm_worst = float(bm_cohorts["benchmark_return_pct"].min())
        rows.append(
            {
                "quintile": "Benchmark",
                "mean_annual_return": round(bm_mean, 2),
                "cumulative_return": round(bm_cum, 2),
                "worst_period_return": round(bm_worst, 2),
                "hit_rate_vs_benchmark": None,
            }
        )

    q1_sub = cohort_returns_df[cohort_returns_df["quintile"] == "Q1"].set_index("cohort_fy")
    q5_sub = cohort_returns_df[cohort_returns_df["quintile"] == "Q5"].set_index("cohort_fy")
    common_fys = q1_sub.index.intersection(q5_sub.index)

    if not common_fys.empty:
        spread = q1_sub.loc[common_fys, "return_pct"] - q5_sub.loc[common_fys, "return_pct"]
        mean_sp = float(spread.mean())
        sp_factors = 1.0 + (spread / 100.0)
        cum_sp = float((np.prod(sp_factors) - 1.0) * 100.0)
        worst_sp = float(spread.min())
        hit_sp = float((spread > 0).mean() * 100.0)
        rows.append(
            {
                "quintile": "Q1_minus_Q5_Spread",
                "mean_annual_return": round(mean_sp, 2),
                "cumulative_return": round(cum_sp, 2),
                "worst_period_return": round(worst_sp, 2),
                "hit_rate_vs_benchmark": round(hit_sp, 1),
            }
        )

    rows.append(
        {
            "quintile": "Placebo_Spread_p50_p95",
            "mean_annual_return": round(placebo_results["placebo_p50"] * 100.0, 2),
            "cumulative_return": round(placebo_results["placebo_p95"] * 100.0, 2),
            "worst_period_return": round(placebo_results["placebo_p5"] * 100.0, 2),
            "hit_rate_vs_benchmark": None,
        }
    )

    return pd.DataFrame(rows)


def run_backtest_pipeline() -> None:
    """Execute end-to-end look-ahead-aware backtest pipeline and placebo evaluation."""
    start_time = time.perf_counter()
    configure_logging()
    logger.info("Starting look-ahead-aware backtest pipeline...")

    db_url = get_database_url()
    try:
        engine = create_engine(db_url)
        data = load_backtest_dataset(engine)
        logger.info(
            "Loaded data: %d companies, %d annual metrics, %d price rows.",
            len(data["companies"]),
            len(data["metrics_annual"]),
            len(data["prices"]),
        )
    except SQLAlchemyError as exc:
        logger.warning("Database unavailable for backtest execution: %s", exc)
        return

    benchmark_df = load_benchmark_prices()
    bm_map = build_benchmark_map(benchmark_df)
    price_map = build_price_map(data["prices"])

    weights = load_scoring_weights(CONFIG_DIR / "scoring_weights.csv")
    metrics_annual_df = data["metrics_annual"]

    if metrics_annual_df.empty:
        logger.warning("No annual metrics found. Exiting backtest pipeline.")
        return

    all_fys = sorted(metrics_annual_df["fiscal_year"].dropna().unique().astype(int))
    cohort_results: list[pd.DataFrame] = []
    cohort_companies: list[pd.DataFrame] = []

    for fy in all_fys:
        formation_d = get_available_date(fy)
        exit_d = date(fy + 1, 6, 30)

        bm_entry = bm_map.get((formation_d.year, formation_d.month))
        bm_exit = bm_map.get((exit_d.year, exit_d.month))

        if bm_entry is None or bm_exit is None or bm_entry <= 0:
            logger.info(
                "Skipping FY%d cohort: missing benchmark prices for %s-%s.",
                fy,
                formation_d,
                exit_d,
            )
            continue

        bm_ret = (bm_exit / bm_entry) - 1.0

        pit_metrics = build_pit_cohort_metrics(fy, formation_d, exit_d, data, price_map)
        if pit_metrics.empty:
            continue

        scored = score_backtest_cohort(pit_metrics, weights)
        eligible_count = int(scored["is_eligible"].sum())
        logger.info(
            "FY%d cohort (%s to %s): %d eligible companies, BM return = %.2f%%.",
            fy,
            formation_d,
            exit_d,
            eligible_count,
            bm_ret * 100.0,
        )

        if eligible_count < 5:
            logger.warning(
                "FY%d cohort has fewer than 5 eligible companies; skipping quintile allocation.",
                fy,
            )
            continue

        cohort_q_ret = compute_cohort_returns(fy, formation_d, exit_d, scored, bm_ret)
        cohort_results.append(cohort_q_ret)
        cohort_companies.append(scored)

    if not cohort_results:
        logger.warning("No valid cohorts completed full 12-month forward holding windows.")
        return

    cohort_returns_df = pd.concat(cohort_results, ignore_index=True)
    cohort_returns_df.to_csv(COHORT_RETURNS_FILE, index=False)
    logger.info("Saved cohort returns to %s", COHORT_RETURNS_FILE)

    logger.info("Running deterministic Monte Carlo placebo test (500 trials)...")
    placebo_res = run_placebo_test(cohort_companies, n_trials=500, random_seed=42)
    logger.info(
        "Placebo spread distribution: p5=%.2f%%, p50=%.2f%%, p95=%.2f%%",
        placebo_res["placebo_p5"] * 100.0,
        placebo_res["placebo_p50"] * 100.0,
        placebo_res["placebo_p95"] * 100.0,
    )

    summary_df = compute_backtest_summary(cohort_returns_df, placebo_res)
    summary_df.to_csv(BACKTEST_SUMMARY_FILE, index=False)
    logger.info("Saved backtest summary to %s", BACKTEST_SUMMARY_FILE)

    logger.info("Cohort-by-Cohort Performance:\n%s", cohort_returns_df.to_string(index=False))
    logger.info("Backtest Summary Results:\n%s", summary_df.to_string(index=False))

    elapsed = time.perf_counter() - start_time
    logger.info("Backtest pipeline completed in %.2f seconds.", elapsed)


if __name__ == "__main__":
    run_backtest_pipeline()
