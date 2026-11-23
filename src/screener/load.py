"""Database loading pipeline for cleaned financials, prices, and market data."""

import argparse
import logging
import time
from collections.abc import Sequence

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.screener.clean import (
    clean_financials,
    clean_market,
    clean_prices,
    run_quality_checks,
)
from src.screener.config import (
    CONFIG_DIR,
    configure_logging,
    get_database_url,
)

logger = logging.getLogger(__name__)


def get_symbol_to_id_mapping(engine: Engine) -> dict[str, int]:
    """Fetch mapping from symbol to company_id from dim_company."""
    query = text("select symbol, company_id from dim_company")
    with engine.connect() as conn:
        result = conn.execute(query)
        return {row[0]: row[1] for row in result.fetchall()}


def load_target_symbols(pilot_mode: bool, engine: Engine | None) -> list[str]:
    """Load target symbol list from pilot configuration or database."""
    if pilot_mode or engine is None:
        pilot_file = CONFIG_DIR / "pilot_symbols.csv"
        if not pilot_file.exists():
            logger.warning("Pilot file %s not found.", pilot_file)
            return []
        df = pd.read_csv(pilot_file)
        return df["symbol"].dropna().astype(str).str.strip().tolist()

    query = text(
        "select symbol from dim_company where is_financial = false order by symbol"
    )
    try:
        with engine.connect() as conn:
            result = conn.execute(query)
            return [row[0] for row in result.fetchall()]
    except SQLAlchemyError as exc:
        logger.warning("Failed querying symbols from database: %s. Using pilot.", exc)
        return load_target_symbols(pilot_mode=True, engine=None)


def upsert_dim_period(engine: Engine, fiscal_years: Sequence[int]) -> int:
    """Upsert distinct fiscal years into dim_period table."""
    if not fiscal_years:
        return 0

    metadata = MetaData()
    table = Table("dim_period", metadata, autoload_with=engine)
    records = [
        {"fiscal_year": int(fy), "period_end_date": f"{fy}-03-31"}
        for fy in sorted(set(fiscal_years))
    ]

    stmt = insert(table).values(records)
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=["fiscal_year"],
        set_={"period_end_date": stmt.excluded.period_end_date},
    )
    with engine.begin() as conn:
        result = conn.execute(upsert_stmt)
        return result.rowcount


def upsert_fact_financials(
    engine: Engine,
    df: pd.DataFrame,
    symbol_map: dict[str, int],
    batch_size: int = 1000,
) -> int:
    """Upsert standardized financial records into fact_financials table."""
    if df.empty:
        return 0

    work_df = df.copy()
    work_df["company_id"] = work_df["symbol"].map(symbol_map)
    work_df = work_df.dropna(subset=["company_id"])
    work_df["company_id"] = work_df["company_id"].astype(int)

    if work_df.empty:
        return 0

    metadata = MetaData()
    table = Table("fact_financials", metadata, autoload_with=engine)
    total_upserted = 0

    records = work_df[
        ["company_id", "fiscal_year", "line_item", "value_inr", "source", "downloaded_at"]
    ].to_dict(orient="records")

    for i in range(0, len(records), batch_size):
        chunk = records[i : i + batch_size]
        stmt = insert(table).values(chunk)
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["company_id", "fiscal_year", "line_item"],
            set_={
                "value_inr": stmt.excluded.value_inr,
                "source": stmt.excluded.source,
                "downloaded_at": stmt.excluded.downloaded_at,
            },
        )
        with engine.begin() as conn:
            result = conn.execute(upsert_stmt)
            total_upserted += result.rowcount

    return total_upserted


def upsert_fact_prices_monthly(
    engine: Engine,
    df: pd.DataFrame,
    symbol_map: dict[str, int],
    batch_size: int = 2000,
) -> int:
    """Upsert monthly adjusted close prices into fact_prices_monthly table."""
    if df.empty:
        return 0

    work_df = df.copy()
    work_df["company_id"] = work_df["symbol"].map(symbol_map)
    work_df = work_df.dropna(subset=["company_id"])
    work_df["company_id"] = work_df["company_id"].astype(int)

    if work_df.empty:
        return 0

    metadata = MetaData()
    table = Table("fact_prices_monthly", metadata, autoload_with=engine)
    total_upserted = 0

    records = work_df[["company_id", "month_end", "adj_close"]].to_dict(orient="records")

    for i in range(0, len(records), batch_size):
        chunk = records[i : i + batch_size]
        stmt = insert(table).values(chunk)
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=["company_id", "month_end"],
            set_={"adj_close": stmt.excluded.adj_close},
        )
        with engine.begin() as conn:
            result = conn.execute(upsert_stmt)
            total_upserted += result.rowcount

    return total_upserted


def upsert_fact_market(
    engine: Engine,
    df: pd.DataFrame,
    symbol_map: dict[str, int],
) -> int:
    """Upsert market snapshot metrics into fact_market table."""
    if df.empty:
        return 0

    work_df = df.copy()
    work_df["company_id"] = work_df["symbol"].map(symbol_map)
    work_df = work_df.dropna(subset=["company_id"])
    work_df["company_id"] = work_df["company_id"].astype(int)

    if work_df.empty:
        return 0

    metadata = MetaData()
    table = Table("fact_market", metadata, autoload_with=engine)

    records = work_df[
        ["company_id", "snapshot_date", "market_cap", "shares_outstanding"]
    ].to_dict(orient="records")

    stmt = insert(table).values(records)
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=["company_id", "snapshot_date"],
        set_={
            "market_cap": stmt.excluded.market_cap,
            "shares_outstanding": stmt.excluded.shares_outstanding,
        },
    )
    with engine.begin() as conn:
        result = conn.execute(upsert_stmt)
        return result.rowcount


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the database loader."""
    parser = argparse.ArgumentParser(
        description="Clean and load financial statements, prices, and market data into PostgreSQL.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--pilot",
        action="store_true",
        default=True,
        help="Load pilot symbols from config/pilot_symbols.csv (default)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        default=False,
        dest="load_all",
        help="Load all non-financial symbols from database",
    )
    return parser.parse_args()


def run_load_pipeline() -> None:
    """Execute cleaning, data quality verification, and idempotent database loading."""
    start_time = time.perf_counter()
    configure_logging()
    args = parse_args()

    pilot_mode = not args.load_all
    logger.info("Starting load pipeline (mode: %s)...", "pilot" if pilot_mode else "all")

    db_url = get_database_url()
    try:
        engine = create_engine(db_url)
        symbol_map = get_symbol_to_id_mapping(engine)
        logger.info("Retrieved %d symbols from dim_company", len(symbol_map))
    except SQLAlchemyError as exc:
        logger.warning(
            "Database connection failed: %s. Cleaning will run, database upsert skipped.",
            exc,
        )
        engine = None
        symbol_map = {}

    symbols = load_target_symbols(pilot_mode=pilot_mode, engine=engine)
    logger.info("Processing %d symbols...", len(symbols))

    logger.info("Cleaning financial statements...")
    financials_df = clean_financials(symbols)
    logger.info("Cleaned %d financial statement facts.", len(financials_df))

    logger.info("Cleaning monthly price histories...")
    prices_df = clean_prices(symbols)
    logger.info("Cleaned %d monthly price records.", len(prices_df))

    logger.info("Cleaning market snapshot data...")
    market_df = clean_market(symbols)
    logger.info("Cleaned %d market snapshot rows.", len(market_df))

    logger.info("Running data quality checks...")
    issues_df = run_quality_checks(financials_df, market_df)
    logger.info("Quality checks complete. Flagged %d potential issues.", len(issues_df))

    if engine is None:
        logger.warning("Database unavailable. Terminating pipeline without loading.")
        return

    try:
        fiscal_years = financials_df["fiscal_year"].dropna().unique().tolist()
        period_count = upsert_dim_period(engine, fiscal_years)
        logger.info("Upserted %d periods into dim_period.", period_count)

        fin_count = upsert_fact_financials(engine, financials_df, symbol_map)
        logger.info("Upserted %d rows into fact_financials.", fin_count)

        price_count = upsert_fact_prices_monthly(engine, prices_df, symbol_map)
        logger.info("Upserted %d rows into fact_prices_monthly.", price_count)

        market_count = upsert_fact_market(engine, market_df, symbol_map)
        logger.info("Upserted %d rows into fact_market.", market_count)
    except SQLAlchemyError as exc:
        logger.error("Failed during database upsert transaction: %s", exc)

    elapsed = time.perf_counter() - start_time
    logger.info("Pipeline completed in %.2f seconds.", elapsed)


if __name__ == "__main__":
    run_load_pipeline()
