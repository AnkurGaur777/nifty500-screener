"""Ratio engine execution and coverage summary telemetry."""

import logging
from pathlib import Path
import time

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.screener.config import (
    PROJECT_ROOT,
    configure_logging,
    get_database_url,
)

logger = logging.getLogger(__name__)

SQL_VIEWS_FILE = PROJECT_ROOT / "sql" / "02_ratio_views.sql"

RATIO_COLUMNS = [
    "roe",
    "roce",
    "operating_margin",
    "net_margin",
    "debt_to_equity",
    "interest_coverage",
    "net_debt_to_ebitda",
    "cfo_to_net_profit",
    "fcf_cr",
    "fcf_margin",
    "receivable_days",
    "inventory_days",
    "asset_turnover",
    "eps_growth_yoy",
    "piotroski_f_score",
    "revenue_cagr_3yr",
    "profit_cagr_3yr",
    "avg_roce_3yr",
    "avg_roe_3yr",
    "operating_margin_std_3yr",
    "pe_ratio",
    "pb_ratio",
    "ev_to_ebitda",
    "earnings_yield",
    "fcf_yield",
]


def apply_ratio_views(engine: Engine, sql_file: Path = SQL_VIEWS_FILE) -> None:
    """Execute SQL DDL file to create or replace ratio views."""
    with sql_file.open("r", encoding="utf-8") as f:
        sql_content = f.read()

    with engine.begin() as conn:
        conn.execute(text(sql_content))


def fetch_latest_metrics(engine: Engine) -> pd.DataFrame:
    """Query company metrics from the metrics_latest view."""
    cols_str = ", ".join(["company_id", "symbol", "name", "sector"] + RATIO_COLUMNS)
    query = text(f"select {cols_str} from metrics_latest order by symbol")
    with engine.connect() as conn:
        return pd.read_sql_query(query, conn)


def compute_ratio_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Compute non-null count, coverage percentage, and percentiles for all ratios."""
    total_companies = len(df)
    summary_rows: list[dict[str, object]] = []

    for metric in RATIO_COLUMNS:
        if metric not in df.columns:
            continue
        series = pd.to_numeric(df[metric], errors="coerce").dropna()
        count_val = int(series.count())
        cov_pct = round((count_val / total_companies) * 100, 1) if total_companies else 0.0

        if count_val > 0:
            p10 = round(float(series.quantile(0.10)), 4)
            med = round(float(series.median()), 4)
            p90 = round(float(series.quantile(0.90)), 4)
        else:
            p10, med, p90 = None, None, None

        summary_rows.append(
            {
                "metric": metric,
                "count": count_val,
                "coverage_pct": cov_pct,
                "p10": p10,
                "median": med,
                "p90": p90,
            }
        )

    return pd.DataFrame(summary_rows)


def run_metrics_pipeline() -> None:
    """Deploy SQL ratio views and log distribution telemetry for all ratios."""
    start_time = time.perf_counter()
    configure_logging()
    logger.info("Starting ratio engine deployment pipeline...")

    db_url = get_database_url()
    try:
        engine = create_engine(db_url)
        logger.info("Deploying SQL views from %s...", SQL_VIEWS_FILE.name)
        apply_ratio_views(engine)
        logger.info("Views v_financials_wide, metrics_annual, metrics_latest created.")

        df = fetch_latest_metrics(engine)
        logger.info("Retrieved %d non-financial companies from metrics_latest.", len(df))

        summary_df = compute_ratio_summary(df)
        table_str = summary_df.to_string(index=False)
        logger.info("Latest Metrics Coverage and Percentiles Summary:\n%s", table_str)
    except SQLAlchemyError as exc:
        logger.warning("Database unavailable or view execution failed: %s", exc)

    elapsed = time.perf_counter() - start_time
    logger.info("Ratio engine completed in %.2f seconds.", elapsed)


if __name__ == "__main__":
    run_metrics_pipeline()
