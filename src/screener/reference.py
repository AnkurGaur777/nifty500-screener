"""Reference data ingestion, financial classification, and pilot list generation."""

import logging
from pathlib import Path
import numpy as np
import pandas as pd
from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from src.screener.config import (
    CONFIG_DIR,
    CONSTITUENTS_FILE,
    SNAPSHOT_DATE,
    configure_logging,
    get_database_url,
)

logger = logging.getLogger(__name__)


def load_constituents(file_path: Path) -> pd.DataFrame:
    """Load and clean index constituents from CSV file."""
    df = pd.read_csv(file_path)
    df.columns = df.columns.str.strip()

    if "Series" in df.columns:
        # BE series represents trade-to-trade equities; RR represents REITs (excluded).
        series_clean = df["Series"].astype(str).str.strip().str.upper()
        df = df[series_clean.isin(["EQ", "BE"])].copy()

    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip()

    df = df.rename(
        columns={
            "Company Name": "name",
            "Symbol": "symbol",
            "ISIN Code": "isin",
            "Industry": "industry",
        }
    )

    df["sector"] = df["industry"]
    df["is_financial"] = (
        df["industry"].str.lower().str.contains("financial services")
    )
    return df[["symbol", "name", "isin", "sector", "industry", "is_financial"]]


def apply_overrides(df: pd.DataFrame, overrides_file: Path) -> pd.DataFrame:
    """Apply manual sector and financial classification overrides."""
    if not overrides_file.exists():
        return df

    try:
        overrides = pd.read_csv(overrides_file).dropna(how="all")
    except Exception as exc:
        logger.warning("Could not read overrides file %s: %s", overrides_file, exc)
        return df

    if overrides.empty:
        return df

    df = df.copy()
    overrides["symbol"] = overrides["symbol"].astype(str).str.strip()

    for _, row in overrides.iterrows():
        symbol = row["symbol"]
        mask = df["symbol"] == symbol
        if not mask.any():
            continue

        if pd.notna(row.get("sector_override")) and str(row["sector_override"]).strip():
            new_sector = str(row["sector_override"]).strip()
            df.loc[mask, "sector"] = new_sector
            df.loc[mask, "industry"] = new_sector

        if pd.notna(row.get("is_financial_override")) and str(row["is_financial_override"]).strip():
            flag = str(row["is_financial_override"]).strip().lower() in ("true", "1", "yes")
            df.loc[mask, "is_financial"] = flag

    return df


def build_stratified_pilot(
    df: pd.DataFrame,
    target_count: int = 100,
) -> pd.DataFrame:
    """Build deterministic stratified sample of non-financial companies across sectors."""
    df_non_fin = df[~df["is_financial"]].copy()
    total_companies = len(df_non_fin)
    if total_companies == 0:
        return pd.DataFrame(columns=["symbol", "name", "sector"])

    target_count = min(target_count, total_companies)
    sector_counts = df_non_fin["sector"].value_counts()

    proportions = (sector_counts / total_companies) * target_count
    allocations = proportions.apply(np.floor).astype(int)
    remaining_slots = target_count - allocations.sum()

    remainders = (proportions - allocations).sort_values(ascending=False)
    for sector in remainders.index[:remaining_slots]:
        allocations[sector] += 1

    selected_rows = []
    for sector, quota in allocations.items():
        if quota <= 0:
            continue
        sector_companies = df_non_fin[df_non_fin["sector"] == sector]
        picked = sector_companies.sort_values("symbol").head(quota)
        selected_rows.append(picked)

    pilot_df = pd.concat(selected_rows, ignore_index=True)
    return pilot_df.sort_values(["sector", "symbol"])[["symbol", "name", "sector"]]


def upsert_companies(
    df: pd.DataFrame,
    engine: Engine,
    snapshot_date: str = SNAPSHOT_DATE,
) -> int:
    """Upsert company records idempotently into dim_company."""
    metadata = MetaData()
    table = Table("dim_company", metadata, autoload_with=engine)

    records = []
    for _, row in df.iterrows():
        records.append(
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "isin": row.get("isin"),
                "sector": row["sector"],
                "industry": row["industry"],
                "is_financial": bool(row["is_financial"]),
                "snapshot_date": snapshot_date,
            }
        )

    if not records:
        return 0

    stmt = insert(table).values(records)
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            "name": stmt.excluded.name,
            "isin": stmt.excluded.isin,
            "sector": stmt.excluded.sector,
            "industry": stmt.excluded.industry,
            "is_financial": stmt.excluded.is_financial,
            "snapshot_date": stmt.excluded.snapshot_date,
        },
    )

    with engine.begin() as conn:
        result = conn.execute(upsert_stmt)
        return result.rowcount


def run_reference_pipeline() -> None:
    """Execute reference data ingestion, pilot generation, and database sync."""
    configure_logging()
    logger.info("Starting reference data ingestion pipeline")

    df = load_constituents(CONSTITUENTS_FILE)
    df = apply_overrides(df, CONFIG_DIR / "sector_overrides.csv")

    total_count = len(df)
    financial_count = int(df["is_financial"].sum())
    non_financial_count = total_count - financial_count

    logger.info("Total companies loaded: %d", total_count)
    logger.info(
        "Financial vs Non-financial: %d financial, %d non-financial",
        financial_count,
        non_financial_count,
    )

    sector_counts = df["sector"].value_counts()
    logger.info("Sector breakdown:\n%s", sector_counts.to_string())

    pilot_df = build_stratified_pilot(df, target_count=100)
    pilot_path = CONFIG_DIR / "pilot_symbols.csv"
    pilot_df.to_csv(pilot_path, index=False)
    logger.info(
        "Generated pilot list: %d companies saved to %s",
        len(pilot_df),
        pilot_path,
    )

    db_url = get_database_url()
    try:
        engine = create_engine(db_url)
        upserted = upsert_companies(df, engine, snapshot_date=SNAPSHOT_DATE)
        logger.info("Successfully upserted %d companies into dim_company", upserted)
    except Exception as exc:
        logger.warning(
            "Database upsert skipped (database not reachable): %s",
            exc,
        )


if __name__ == "__main__":
    run_reference_pipeline()
