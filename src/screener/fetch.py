"""Financial statements, price histories, and market snapshot downloader."""

import argparse
import json
import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf
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

logger = logging.getLogger(__name__)

RAW_FINANCIALS_DIR = RAW_DIR / "financials"
RAW_PRICES_DIR = RAW_DIR / "prices"
RAW_MARKET_DIR = RAW_DIR / "market"
RAW_BENCHMARK_DIR = RAW_DIR / "benchmark"


def ensure_raw_directories() -> None:
    """Ensure raw cache subdirectories exist."""
    for directory in (RAW_FINANCIALS_DIR, RAW_PRICES_DIR, RAW_MARKET_DIR, RAW_BENCHMARK_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def polite_sleep(min_sec: float = 0.6, max_sec: float = 1.1) -> None:
    """Sleep for random jitter interval between network requests."""
    time.sleep(random.uniform(min_sec, max_sec))


def fetch_with_retry(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Execute callable with exponential backoff retry on failure."""
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt == max_retries:
                raise
            sleep_time = backoff_base**attempt + random.uniform(0.1, 0.5)
            logger.warning(
                "Attempt %d/%d failed: %s. Retrying in %.2fs",
                attempt,
                max_retries,
                exc,
                sleep_time,
            )
            time.sleep(sleep_time)


def save_statement_parquet(df: pd.DataFrame | None, path: Path) -> bool:
    """Save financial statement DataFrame to parquet preserving line item index."""
    if df is None or df.empty:
        empty_df = pd.DataFrame()
        empty_df.index.name = "line_item"
        empty_df.to_parquet(path)
        return False

    save_df = df.copy()
    save_df.columns = [
        c.strftime("%Y-%m-%d") if hasattr(c, "strftime") else str(c) for c in save_df.columns
    ]
    save_df.index.name = "line_item"
    save_df.to_parquet(path)
    return True


def save_prices_parquet(df: pd.DataFrame | None, path: Path) -> bool:
    """Save price history DataFrame to parquet with standardized column names."""
    if df is None or df.empty:
        empty_df = pd.DataFrame(columns=["date", "adj_close", "close", "volume"])
        empty_df.to_parquet(path, index=False)
        return False

    save_df = df.copy()
    if "Date" in save_df.columns:
        save_df = save_df.rename(columns={"Date": "date"})
    elif save_df.index.name in ("Date", "Datetime") or isinstance(save_df.index, pd.DatetimeIndex):
        save_df = save_df.reset_index()
        save_df.columns = [c.lower() if isinstance(c, str) else str(c) for c in save_df.columns]

    save_df.to_parquet(path, index=False)
    return True


def save_market_json(data: dict[str, Any], path: Path) -> None:
    """Save market snapshot dictionary to JSON file."""
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def fetch_benchmark_history(ticker_symbol: str) -> pd.DataFrame:
    """Download 6-year monthly adjusted price history for a benchmark symbol."""
    return yf.Ticker(ticker_symbol).history(period="6y", interval="1mo", auto_adjust=True)


def fetch_benchmarks(refresh: bool = False) -> None:
    """Download monthly adjusted price history for Nifty benchmark indices."""
    benchmarks = {
        "nifty500": "^CRSLDX",
        "nifty50": "^NSEI",
    }
    for name, ticker_symbol in benchmarks.items():
        target_path = RAW_BENCHMARK_DIR / f"{name}_monthly.parquet"
        if target_path.exists() and not refresh:
            continue

        polite_sleep()
        logger.info("Fetching benchmark prices for %s (%s)", name, ticker_symbol)
        try:
            hist = fetch_with_retry(fetch_benchmark_history, ticker_symbol)
            save_prices_parquet(hist, target_path)
        except Exception as exc:
            logger.warning("Failed to fetch benchmark %s: %s", name, exc)


def fetch_symbol_data(symbol: str, refresh: bool = False) -> tuple[str, str | None]:
    """Fetch and cache annual statements, monthly prices, and market snapshot for symbol."""
    income_path = RAW_FINANCIALS_DIR / f"{symbol}_income.parquet"
    balance_path = RAW_FINANCIALS_DIR / f"{symbol}_balance.parquet"
    cashflow_path = RAW_FINANCIALS_DIR / f"{symbol}_cashflow.parquet"
    price_path = RAW_PRICES_DIR / f"{symbol}_monthly.parquet"
    market_path = RAW_MARKET_DIR / f"{symbol}_market.json"

    all_cached = (
        income_path.exists()
        and balance_path.exists()
        and cashflow_path.exists()
        and price_path.exists()
        and market_path.exists()
    )
    if all_cached and not refresh:
        return "success", None

    polite_sleep()
    ticker_symbol = f"{symbol}.NS"
    ticker = yf.Ticker(ticker_symbol)
    missing: list[str] = []

    try:
        income = fetch_with_retry(lambda: ticker.income_stmt)
        if not save_statement_parquet(income, income_path):
            missing.append("income")
    except Exception as exc:
        missing.append("income")
        save_statement_parquet(None, income_path)
        logger.debug("Failed income for %s: %s", symbol, exc)

    try:
        balance = fetch_with_retry(lambda: ticker.balance_sheet)
        if not save_statement_parquet(balance, balance_path):
            missing.append("balance")
    except Exception as exc:
        missing.append("balance")
        save_statement_parquet(None, balance_path)
        logger.debug("Failed balance for %s: %s", symbol, exc)

    try:
        cashflow = fetch_with_retry(lambda: ticker.cashflow)
        if not save_statement_parquet(cashflow, cashflow_path):
            missing.append("cashflow")
    except Exception as exc:
        missing.append("cashflow")
        save_statement_parquet(None, cashflow_path)
        logger.debug("Failed cashflow for %s: %s", symbol, exc)

    try:
        prices = fetch_with_retry(
            lambda: ticker.history(period="6y", interval="1mo", auto_adjust=True)
        )
        if not save_prices_parquet(prices, price_path):
            missing.append("prices")
    except Exception as exc:
        missing.append("prices")
        save_prices_parquet(None, price_path)
        logger.debug("Failed prices for %s: %s", symbol, exc)

    market_cap = None
    shares_outstanding = None
    currency = "INR"

    try:
        info = ticker.info or {}
        market_cap = info.get("marketCap")
        shares_outstanding = info.get("sharesOutstanding")
        currency = info.get("currency", "INR")
    except Exception:
        info = {}

    if market_cap is None or shares_outstanding is None:
        try:
            fast_info = ticker.fast_info
            if market_cap is None and hasattr(fast_info, "market_cap"):
                market_cap = fast_info.market_cap
            if shares_outstanding is None and hasattr(fast_info, "shares"):
                shares_outstanding = fast_info.shares
            if hasattr(fast_info, "currency") and fast_info.currency:
                currency = fast_info.currency
        except Exception:
            pass

    snapshot = {
        "symbol": symbol,
        "market_cap": market_cap,
        "shares_outstanding": shares_outstanding,
        "currency": currency,
        "downloaded_at": datetime.now(UTC).isoformat(),
    }
    save_market_json(snapshot, market_path)

    if len(missing) == 4:
        return "failed", "All financial statements and price histories unavailable"
    if missing:
        return "partial", f"Missing data: {', '.join(missing)}"
    return "success", None


def log_download_attempt(
    engine: Engine | None,
    symbol: str,
    status: str,
    error_message: str | None = None,
) -> None:
    """Insert download status into load_log table if database is available."""
    if engine is None:
        return

    query = text(
        "insert into load_log (symbol, status, error_message, logged_at) "
        "values (:symbol, :status, :error_message, now())"
    )
    try:
        with engine.begin() as conn:
            conn.execute(
                query,
                {"symbol": symbol, "status": status, "error_message": error_message},
            )
    except SQLAlchemyError as exc:
        logger.warning("Failed to record load_log for %s: %s", symbol, exc)


def load_symbols(
    pilot_mode: bool,
    engine: Engine | None,
    limit: int | None = None,
) -> list[str]:
    """Load target symbol list from pilot file or database."""
    if pilot_mode or engine is None:
        pilot_file = CONFIG_DIR / "pilot_symbols.csv"
        if not pilot_file.exists():
            logger.warning("Pilot file %s not found. Run reference step first.", pilot_file)
            return []
        df = pd.read_csv(pilot_file)
        symbols = df["symbol"].dropna().astype(str).str.strip().tolist()
    else:
        query = text(
            "select symbol from dim_company where is_financial = false order by symbol"
        )
        try:
            with engine.connect() as conn:
                result = conn.execute(query)
                symbols = [row[0] for row in result.fetchall()]
        except SQLAlchemyError as exc:
            logger.warning("Failed to read symbols from database: %s. Falling back to pilot.", exc)
            return load_symbols(pilot_mode=True, engine=None, limit=limit)

    if limit is not None and limit > 0:
        symbols = symbols[:limit]
    return symbols


def audit_coverage_and_unmapped(
    symbols: list[str],
    mapping_file: Path,
    financials_dir: Path,
    interim_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Audit coverage of 25 standard fields and collect unmapped labels."""
    mapping_df = pd.read_csv(mapping_file)
    mapping_df["standard_field"] = mapping_df["standard_field"].astype(str).str.strip()
    mapping_df["source_label"] = mapping_df["source_label"].astype(str).str.strip()
    mapping_df["statement"] = mapping_df["statement"].astype(str).str.strip()
    mapping_df["priority"] = mapping_df["priority"].astype(int)
    mapping_df = mapping_df.sort_values(["statement", "standard_field", "priority"])

    cached_dfs: dict[str, dict[str, pd.DataFrame]] = {
        "income": {},
        "balance": {},
        "cashflow": {},
    }
    for symbol in symbols:
        for stmt in ("income", "balance", "cashflow"):
            path = financials_dir / f"{symbol}_{stmt}.parquet"
            if path.exists():
                try:
                    df = pd.read_parquet(path)
                    if not df.empty:
                        cached_dfs[stmt][symbol] = df
                except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                    pass

    total_companies = len(symbols)
    coverage_rows = []

    for _, group in mapping_df.groupby(["statement", "standard_field"], sort=False):
        stmt = group["statement"].iloc[0]
        std_field = group["standard_field"].iloc[0]
        labels_by_priority = group["source_label"].tolist()

        years_per_company: list[int] = []
        for symbol in symbols:
            stmt_df = cached_dfs[stmt].get(symbol)
            years = 0
            if stmt_df is not None:
                for label in labels_by_priority:
                    if label in stmt_df.index:
                        series = stmt_df.loc[label]
                        valid_count = int(series.dropna().count())
                        if valid_count > 0:
                            years = valid_count
                            break
            years_per_company.append(years)

        c_1 = sum(1 for y in years_per_company if y >= 1)
        c_3 = sum(1 for y in years_per_company if y >= 3)
        c_4 = sum(1 for y in years_per_company if y >= 4)
        pct_1 = round((c_1 / total_companies) * 100, 1) if total_companies else 0.0
        pct_3 = round((c_3 / total_companies) * 100, 1) if total_companies else 0.0
        pct_4 = round((c_4 / total_companies) * 100, 1) if total_companies else 0.0

        coverage_rows.append(
            {
                "statement": stmt,
                "standard_field": std_field,
                "count_ge_1yr": c_1,
                "pct_ge_1yr": pct_1,
                "count_ge_3yr": c_3,
                "pct_ge_3yr": pct_3,
                "count_ge_4yr": c_4,
                "pct_ge_4yr": pct_4,
            }
        )

    coverage_df = pd.DataFrame(coverage_rows)
    coverage_path = interim_dir / "coverage_report.csv"
    coverage_df.to_csv(coverage_path, index=False)

    unmapped_counter: dict[tuple[str, str], set[str]] = {}
    for stmt in ("income", "balance", "cashflow"):
        known_labels = set(mapping_df[mapping_df["statement"] == stmt]["source_label"])
        for symbol, df in cached_dfs[stmt].items():
            for label in df.index:
                label_str = str(label).strip()
                if label_str and label_str not in known_labels:
                    key = (stmt, label_str)
                    unmapped_counter.setdefault(key, set()).add(symbol)

    unmapped_rows = [
        {"statement": stmt, "source_label": label, "company_count": len(syms)}
        for (stmt, label), syms in unmapped_counter.items()
    ]
    unmapped_df = pd.DataFrame(unmapped_rows)
    if not unmapped_df.empty:
        unmapped_df = unmapped_df.sort_values(
            ["statement", "company_count"], ascending=[True, False]
        )
    else:
        unmapped_df = pd.DataFrame(columns=["statement", "source_label", "company_count"])

    unmapped_path = interim_dir / "unmapped_labels.csv"
    unmapped_df.to_csv(unmapped_path, index=False)

    return coverage_df, unmapped_df


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the fetch pipeline."""
    parser = argparse.ArgumentParser(
        description="Download financial data and prices for Nifty 500.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--pilot",
        action="store_true",
        default=True,
        help="Fetch pilot symbols from config/pilot_symbols.csv (default)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        default=False,
        dest="fetch_all",
        help="Fetch all non-financial symbols from database",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N symbols",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        help="Bypass local disk cache and re-download",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        default=False,
        help="Skip downloads and generate coverage report from cache",
    )
    return parser.parse_args()


def run_fetch_pipeline() -> None:
    """Execute download pipeline and generate coverage reports."""
    configure_logging()
    args = parse_args()
    ensure_raw_directories()

    db_engine: Engine | None = None
    try:
        db_engine = create_engine(get_database_url())
    except Exception:
        db_engine = None

    pilot_mode = not args.fetch_all
    symbols = load_symbols(pilot_mode=pilot_mode, engine=db_engine, limit=args.limit)
    logger.info("Target universe size: %d symbols", len(symbols))

    if not args.report_only:
        logger.info("Fetching benchmarks...")
        fetch_benchmarks(refresh=args.refresh)

        success_count = 0
        partial_count = 0
        failed_count = 0

        for idx, symbol in enumerate(symbols, start=1):
            logger.info("[%d/%d] Processing %s...", idx, len(symbols), symbol)
            status, err = fetch_symbol_data(symbol, refresh=args.refresh)
            log_download_attempt(db_engine, symbol, status, err)

            if status == "success":
                success_count += 1
            elif status == "partial":
                partial_count += 1
            else:
                failed_count += 1

        logger.info(
            "Fetch finished: %d succeeded, %d partial, %d failed",
            success_count,
            partial_count,
            failed_count,
        )

    logger.info("Generating coverage and unmapped labels audit...")
    cov_df, unmapped_df = audit_coverage_and_unmapped(
        symbols=symbols,
        mapping_file=CONFIG_DIR / "field_mapping.csv",
        financials_dir=RAW_FINANCIALS_DIR,
        interim_dir=INTERIM_DIR,
    )
    logger.info("Coverage report generated with %d standard fields.", len(cov_df))
    logger.info("Unmapped labels found: %d distinct labels.", len(unmapped_df))


if __name__ == "__main__":
    run_fetch_pipeline()
