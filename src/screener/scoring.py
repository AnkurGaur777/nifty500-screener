"""Sector-relative scoring model, factor weighting, sensitivity analysis, and master view."""

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.screener.config import (
    CONFIG_DIR,
    INTERIM_DIR,
    PROJECT_ROOT,
    SNAPSHOT_DATE,
    configure_logging,
    get_database_url,
)

logger = logging.getLogger(__name__)

SCORE_VIEWS_FILE = PROJECT_ROOT / "sql" / "04_score_views.sql"

FACTOR_DEFINITIONS: dict[str, list[str]] = {
    "quality": [
        "avg_roce_3yr",
        "avg_roe_3yr",
        "operating_margin",
        "cfo_to_net_profit",
        "piotroski_f_score",
    ],
    "growth": [
        "revenue_cagr_3yr",
        "profit_cagr_3yr",
        "eps_growth_yoy",
    ],
    "value": [
        "earnings_yield",
        "pb_ratio",
        "ev_to_ebitda",
        "fcf_yield",
    ],
    "safety": [
        "debt_to_equity",
        "interest_coverage",
        "net_debt_to_ebitda",
        "operating_margin_std_3yr",
    ],
}

ALL_SCORING_METRICS: list[str] = [
    metric for metrics in FACTOR_DEFINITIONS.values() for metric in metrics
]

INVERTED_METRICS: set[str] = {
    "debt_to_equity",
    "net_debt_to_ebitda",
    "operating_margin_std_3yr",
    "pb_ratio",
    "ev_to_ebitda",
}

ALTERNATIVE_SCHEMES: dict[str, dict[str, float]] = {
    "Quality-Tilted": {"quality": 0.40, "growth": 0.20, "value": 0.20, "safety": 0.20},
    "Value-Tilted": {"quality": 0.20, "growth": 0.15, "value": 0.45, "safety": 0.20},
    "Growth-Tilted": {"quality": 0.20, "growth": 0.45, "value": 0.15, "safety": 0.20},
}


def load_scoring_weights(weights_file: Path) -> dict[str, float]:
    """Load factor weights from CSV configuration file with default equal weights fallback."""
    default_weights = {"quality": 0.25, "growth": 0.25, "value": 0.25, "safety": 0.25}
    if not weights_file.exists():
        return default_weights

    try:
        df = pd.read_csv(weights_file)
        weights = {}
        for _, row in df.iterrows():
            factor = str(row["factor"]).strip().lower()
            weights[factor] = float(row["weight"])
        total = sum(weights.values())
        if not np.isclose(total, 1.0, atol=1e-3):
            logger.warning("Scoring weights do not sum to 1.0 (sum=%.3f). Normalizing.", total)
            weights = {k: v / total for k, v in weights.items()}
        return weights
    except Exception as exc:
        logger.warning(
            "Failed reading weights from %s: %s. Using default equal weights.",
            weights_file,
            exc,
        )
        return default_weights


def winsorize_series(
    series: pd.Series,
    lower_q: float = 0.01,
    upper_q: float = 0.99,
) -> pd.Series:
    """Winsorize raw metric series across universe non-null values at 1st and 99th percentiles."""
    valid = series.dropna()
    if valid.empty:
        return series
    low = valid.quantile(lower_q)
    high = valid.quantile(upper_q)
    return series.clip(lower=low, upper=high)


def assign_peer_groups(df: pd.DataFrame, min_peers: int = 8) -> pd.DataFrame:
    """Assign peer groups, mapping sectors with fewer than min_peers to Other Small Sectors."""
    res = df.copy()
    counts = res["sector"].value_counts()
    small_sectors = set(counts[counts < min_peers].index)
    res["peer_group"] = res["sector"].apply(
        lambda s: "Other Small Sectors" if s in small_sectors else s
    )
    return res


def compute_relative_percentiles(df: pd.DataFrame) -> pd.DataFrame:
    """Compute within-peer-group percentile ranks (0-100), inverting lower-is-better metrics."""
    res = df.copy()

    # Step 1: Winsorize universe-wide
    for metric in ALL_SCORING_METRICS:
        if metric in res.columns:
            res[metric] = winsorize_series(res[metric])

    # Step 2: Percentile rank within peer group
    for metric in ALL_SCORING_METRICS:
        if metric not in res.columns:
            continue
        rank_col = f"{metric}_rank"

        def rank_group(group: pd.Series, is_inverted: bool) -> pd.Series:
            valid_count = group.count()
            if valid_count == 0:
                return group
            pct = group.rank(pct=True, ascending=True) * 100.0
            return (100.0 - pct) if is_inverted else pct

        res[rank_col] = (
            res.groupby("peer_group")[metric]
            .transform(rank_group, is_inverted=(metric in INVERTED_METRICS))
        )

    return res


def compute_factor_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate the 4 factor scores, requiring at least 2 non-null metrics per factor."""
    res = df.copy()

    for factor, metrics in FACTOR_DEFINITIONS.items():
        score_col = f"{factor}_score"
        rank_cols = [f"{m}_rank" for m in metrics if f"{m}_rank" in res.columns]

        sub_ranks = res[rank_cols]
        valid_counts = sub_ranks.notna().sum(axis=1)
        mean_scores = sub_ranks.mean(axis=1, skipna=True)

        res[score_col] = np.where(valid_counts >= 2, mean_scores, np.nan)

    # Data completeness % out of 16 metrics
    available_metrics = [m for m in ALL_SCORING_METRICS if m in res.columns]
    res["data_completeness_pct"] = (
        (res[available_metrics].notna().sum(axis=1) / float(len(ALL_SCORING_METRICS))) * 100.0
    ).round(1)

    # Eligibility: years_available >= 3 and all 4 factor scores present
    all_factors_present = (
        res["quality_score"].notna()
        & res["growth_score"].notna()
        & res["value_score"].notna()
        & res["safety_score"].notna()
    )
    years_valid = res.get("years_available", pd.Series(0, index=res.index)).fillna(0) >= 3
    res["is_eligible"] = all_factors_present & years_valid

    return res


def compute_composite_scores(
    df: pd.DataFrame,
    weights: dict[str, float],
) -> pd.DataFrame:
    """Calculate weighted composite score and assign overall and sector ranks."""
    res = df.copy()

    res["total_score"] = np.nan
    res["overall_rank"] = np.nan
    res["sector_rank"] = np.nan

    eligible_mask = res["is_eligible"] == True  # noqa: E712
    if not eligible_mask.any():
        return res

    eligible_df = res[eligible_mask].copy()
    weighted_sum = (
        weights["quality"] * eligible_df["quality_score"]
        + weights["growth"] * eligible_df["growth_score"]
        + weights["value"] * eligible_df["value_score"]
        + weights["safety"] * eligible_df["safety_score"]
    )
    eligible_df["total_score"] = weighted_sum.round(2)

    # 1 = highest total score
    eligible_df["overall_rank"] = (
        eligible_df["total_score"].rank(ascending=False, method="min").astype(int)
    )
    eligible_df["sector_rank"] = (
        eligible_df.groupby("sector")["total_score"]
        .rank(ascending=False, method="min")
        .astype(int)
    )

    res.loc[eligible_mask, "total_score"] = eligible_df["total_score"]
    res.loc[eligible_mask, "overall_rank"] = eligible_df["overall_rank"]
    res.loc[eligible_mask, "sector_rank"] = eligible_df["sector_rank"]

    return res


def compute_sensitivity_overlap(
    eligible_df: pd.DataFrame,
    baseline_weights: dict[str, float],
    schemes: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """Compute Top-20 symbol overlap between baseline and alternative weighting schemes."""
    if eligible_df.empty:
        return pd.DataFrame(columns=["scheme_name", "overlap_count", "overlap_pct"])

    def get_top_20(w: dict[str, float]) -> list[str]:
        scores = (
            w["quality"] * eligible_df["quality_score"]
            + w["growth"] * eligible_df["growth_score"]
            + w["value"] * eligible_df["value_score"]
            + w["safety"] * eligible_df["safety_score"]
        )
        sub = eligible_df[["symbol"]].copy()
        sub["score"] = scores
        top_sub = sub.sort_values("score", ascending=False).head(20)
        return top_sub["symbol"].tolist()

    baseline_top = set(get_top_20(baseline_weights))
    rows: list[dict[str, object]] = []

    for name, w in schemes.items():
        alt_top = set(get_top_20(w))
        overlap = len(baseline_top.intersection(alt_top))
        overlap_pct = round((overlap / 20.0) * 100.0, 1)
        rows.append(
            {
                "scheme_name": name,
                "overlap_count": overlap,
                "overlap_pct": overlap_pct,
            }
        )

    return pd.DataFrame(rows)


def ensure_scores_table(engine: Engine) -> None:
    """Ensure the scores table exists in the PostgreSQL database."""
    ddl = text(
        """
        create table if not exists scores (
            company_id integer primary key references dim_company(company_id),
            scoring_date date not null,
            peer_group text not null,
            quality_score numeric,
            growth_score numeric,
            value_score numeric,
            safety_score numeric,
            total_score numeric,
            overall_rank integer,
            sector_rank integer,
            data_completeness_pct numeric not null,
            is_eligible boolean not null,
            red_flag_count integer,
            red_flag_list text
        );
        """
    )
    with engine.begin() as conn:
        conn.execute(ddl)


def upsert_scores(
    engine: Engine,
    scores_df: pd.DataFrame,
    scoring_date: str = SNAPSHOT_DATE,
) -> int:
    """Upsert company scoring and ranking records into the scores table."""
    if scores_df.empty:
        return 0

    ensure_scores_table(engine)
    metadata = MetaData()
    table = Table("scores", metadata, autoload_with=engine)

    records: list[dict[str, Any]] = []
    for _, row in scores_df.iterrows():
        q_s = float(row["quality_score"]) if pd.notna(row["quality_score"]) else None
        g_s = float(row["growth_score"]) if pd.notna(row["growth_score"]) else None
        v_s = float(row["value_score"]) if pd.notna(row["value_score"]) else None
        s_s = float(row["safety_score"]) if pd.notna(row["safety_score"]) else None
        t_s = float(row["total_score"]) if pd.notna(row["total_score"]) else None
        o_r = int(row["overall_rank"]) if pd.notna(row["overall_rank"]) else None
        s_r = int(row["sector_rank"]) if pd.notna(row["sector_rank"]) else None
        rf_c = int(row["red_flag_count"]) if pd.notna(row.get("red_flag_count")) else None
        rf_l = str(row["red_flag_list"]) if pd.notna(row.get("red_flag_list")) else None

        records.append(
            {
                "company_id": int(row["company_id"]),
                "scoring_date": scoring_date,
                "peer_group": row["peer_group"],
                "quality_score": q_s,
                "growth_score": g_s,
                "value_score": v_s,
                "safety_score": s_s,
                "total_score": t_s,
                "overall_rank": o_r,
                "sector_rank": s_r,
                "data_completeness_pct": float(row["data_completeness_pct"]),
                "is_eligible": bool(row["is_eligible"]),
                "red_flag_count": rf_c,
                "red_flag_list": rf_l,
            }
        )

    stmt = insert(table).values(records)
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=["company_id"],
        set_={
            "scoring_date": stmt.excluded.scoring_date,
            "peer_group": stmt.excluded.peer_group,
            "quality_score": stmt.excluded.quality_score,
            "growth_score": stmt.excluded.growth_score,
            "value_score": stmt.excluded.value_score,
            "safety_score": stmt.excluded.safety_score,
            "total_score": stmt.excluded.total_score,
            "overall_rank": stmt.excluded.overall_rank,
            "sector_rank": stmt.excluded.sector_rank,
            "data_completeness_pct": stmt.excluded.data_completeness_pct,
            "is_eligible": stmt.excluded.is_eligible,
            "red_flag_count": stmt.excluded.red_flag_count,
            "red_flag_list": stmt.excluded.red_flag_list,
        },
    )

    with engine.begin() as conn:
        result = conn.execute(upsert_stmt)
        return result.rowcount


def apply_master_view(engine: Engine, sql_file: Path = SCORE_VIEWS_FILE) -> None:
    """Execute SQL DDL file to deploy screener_master database view."""
    with sql_file.open("r", encoding="utf-8") as f:
        sql_content = f.read()

    with engine.begin() as conn:
        conn.execute(text(sql_content))


def fetch_scoring_dataset(engine: Engine) -> pd.DataFrame:
    """Retrieve non-financial companies with latest metrics and red-flag details."""
    query = text(
        """
        select
            c.company_id,
            c.symbol,
            c.name,
            c.sector,
            ml.years_available,
            ml.market_cap_cr,
            ml.roe,
            ml.roce,
            ml.operating_margin,
            ml.cfo_to_net_profit,
            ml.piotroski_f_score,
            ml.revenue_cagr_3yr,
            ml.profit_cagr_3yr,
            ml.eps_growth_yoy,
            ml.earnings_yield,
            ml.pb_ratio,
            ml.ev_to_ebitda,
            ml.fcf_yield,
            ml.debt_to_equity,
            ml.interest_coverage,
            ml.net_debt_to_ebitda,
            ml.operating_margin_std_3yr,
            rf.red_flag_count,
            rf.red_flag_list
        from dim_company c
        left join metrics_latest ml on c.company_id = ml.company_id
        left join red_flags_detail rf on c.company_id = rf.company_id
        where c.is_financial = false
        order by c.symbol
        """
    )
    with engine.connect() as conn:
        return pd.read_sql_query(query, conn)


def run_scoring_pipeline() -> None:
    """Execute end-to-end sector-relative scoring, persistence, and sensitivity analysis."""
    start_time = time.perf_counter()
    configure_logging()
    logger.info("Starting sector-relative scoring model pipeline...")

    db_url = get_database_url()
    try:
        engine = create_engine(db_url)
        dataset = fetch_scoring_dataset(engine)
        logger.info("Loaded %d non-financial companies for scoring.", len(dataset))

        weights = load_scoring_weights(CONFIG_DIR / "scoring_weights.csv")
        logger.info("Scoring weights loaded: %s", weights)

        with_peers = assign_peer_groups(dataset, min_peers=8)
        with_ranks = compute_relative_percentiles(with_peers)
        with_factors = compute_factor_scores(with_ranks)
        scored_df = compute_composite_scores(with_factors, weights)

        eligible_count = int(scored_df["is_eligible"].sum())
        logger.info(
            "Eligibility audit: %d eligible, %d ineligible.",
            eligible_count,
            len(scored_df) - eligible_count,
        )

        upserted = upsert_scores(engine, scored_df, scoring_date=SNAPSHOT_DATE)
        logger.info("Upserted %d records into scores table.", upserted)

        logger.info("Deploying master view from %s...", SCORE_VIEWS_FILE.name)
        apply_master_view(engine, SCORE_VIEWS_FILE)
        logger.info("View screener_master deployed successfully.")

        # Sensitivity Analysis
        eligible_sub = scored_df[scored_df["is_eligible"] == True].copy()  # noqa: E712
        sensitivity_df = compute_sensitivity_overlap(eligible_sub, weights, ALTERNATIVE_SCHEMES)
        sens_path = INTERIM_DIR / "score_sensitivity.csv"
        sensitivity_df.to_csv(sens_path, index=False)
        logger.info(
            "Weight Sensitivity Analysis (Top-20 overlap vs baseline):\n%s",
            sensitivity_df.to_string(index=False),
        )

        # Top-15 Leaders Table
        leaders = (
            scored_df[scored_df["is_eligible"] == True]  # noqa: E712
            .sort_values("overall_rank")
            .head(15)[
                [
                    "overall_rank",
                    "symbol",
                    "name",
                    "sector",
                    "total_score",
                    "quality_score",
                    "growth_score",
                    "value_score",
                    "safety_score",
                    "red_flag_count",
                ]
            ]
        )
        logger.info("Top-15 Ranked Companies:\n%s", leaders.to_string(index=False))
    except SQLAlchemyError as exc:
        logger.warning("Database unavailable or scoring execution failed: %s", exc)

    elapsed = time.perf_counter() - start_time
    logger.info("Scoring pipeline completed in %.2f seconds.", elapsed)


if __name__ == "__main__":
    run_scoring_pipeline()
