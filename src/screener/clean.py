"""Data cleaning, fiscal year alignment, and data quality check engine."""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime

import pandas as pd

from src.screener.config import (
    CONFIG_DIR,
    INTERIM_DIR,
    RAW_DIR,
    SNAPSHOT_DATE,
)

logger = logging.getLogger(__name__)

RAW_FINANCIALS_DIR = RAW_DIR / "financials"
RAW_PRICES_DIR = RAW_DIR / "prices"
RAW_MARKET_DIR = RAW_DIR / "market"


def align_fiscal_year(period_end: date) -> int:
    """Align financial statement period-end date to Indian fiscal year."""
    # Indian fiscal year runs April 1 to March 31; periods ending Apr-Dec fall into next FY.
    return period_end.year if period_end.month <= 3 else period_end.year + 1


def clean_financials(symbols: Sequence[str]) -> pd.DataFrame:
    """Read, map, and standardize raw financial statement parquets into long format."""
    mapping_path = CONFIG_DIR / "field_mapping.csv"
    if not mapping_path.exists():
        logger.warning("Field mapping file %s not found.", mapping_path)
        return pd.DataFrame(
            columns=["symbol", "fiscal_year", "line_item", "value_inr", "source", "downloaded_at"]
        )

    mapping_df = pd.read_csv(mapping_path)
    mapping_df["statement"] = mapping_df["statement"].astype(str).str.strip()
    mapping_df["standard_field"] = mapping_df["standard_field"].astype(str).str.strip()
    mapping_df["source_label"] = mapping_df["source_label"].astype(str).str.strip()
    mapping_df["priority"] = mapping_df["priority"].astype(int)
    mapping_df = mapping_df.sort_values("priority", ascending=True)

    records: list[dict[str, object]] = []
    now_iso = datetime.now(UTC).isoformat()

    for symbol in symbols:
        for stmt in ("income", "balance", "cashflow"):
            file_path = RAW_FINANCIALS_DIR / f"{symbol}_{stmt}.parquet"
            if not file_path.exists():
                continue

            try:
                df = pd.read_parquet(file_path)
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
                logger.debug("Failed reading %s: %s", file_path, exc)
                continue

            if df.empty:
                continue

            stmt_mapping = mapping_df[mapping_df["statement"] == stmt]
            col_dates = {col: pd.to_datetime(col).date() for col in df.columns}
            fy_to_cols: dict[int, list[str]] = {}
            for col, p_date in col_dates.items():
                fy = align_fiscal_year(p_date)
                fy_to_cols.setdefault(fy, []).append(col)

            for fy, cols in fy_to_cols.items():
                if len(cols) > 1:
                    chosen_col = max(cols, key=lambda c: col_dates[c])
                    logger.warning(
                        "Duplicate fiscal year %d for %s in %s; keeping latest period %s",
                        fy,
                        symbol,
                        stmt,
                        col_dates[chosen_col],
                    )
                else:
                    chosen_col = cols[0]

                for std_field in stmt_mapping["standard_field"].unique():
                    candidate_labels = stmt_mapping[
                        stmt_mapping["standard_field"] == std_field
                    ]["source_label"].tolist()

                    for label in candidate_labels:
                        if label in df.index:
                            val = df.loc[label, chosen_col]
                            if pd.notna(val) and val != "":
                                try:
                                    val_float = float(val)
                                except (ValueError, TypeError):
                                    continue
                                records.append(
                                    {
                                        "symbol": symbol,
                                        "fiscal_year": int(fy),
                                        "line_item": std_field,
                                        "value_inr": val_float,
                                        "source": "yfinance",
                                        "downloaded_at": now_iso,
                                    }
                                )
                                break

    if not records:
        return pd.DataFrame(
            columns=["symbol", "fiscal_year", "line_item", "value_inr", "source", "downloaded_at"]
        )

    res_df = pd.DataFrame(records)
    res_df = res_df.drop_duplicates(subset=["symbol", "fiscal_year", "line_item"], keep="first")
    return res_df


def clean_prices(symbols: Sequence[str]) -> pd.DataFrame:
    """Read raw price parquets and produce monthly adjusted close series."""
    frames: list[pd.DataFrame] = []

    for symbol in symbols:
        price_file = RAW_PRICES_DIR / f"{symbol}_monthly.parquet"
        if not price_file.exists():
            continue

        try:
            df = pd.read_parquet(price_file)
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            logger.debug("Failed reading %s: %s", price_file, exc)
            continue

        if df.empty:
            continue

        close_col = "adj_close" if "adj_close" in df.columns else "close"
        if close_col not in df.columns:
            matches = [c for c in df.columns if c.lower() in ("close", "adj_close", "adj close")]
            if matches:
                close_col = matches[0]
            else:
                continue

        date_col = "date" if "date" in df.columns else "Date"
        if date_col not in df.columns:
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index()
                date_col = df.columns[0]
            else:
                continue

        clean_sub = df[[date_col, close_col]].dropna().copy()
        if clean_sub.empty:
            continue

        clean_sub["date_dt"] = pd.to_datetime(clean_sub[date_col])
        clean_sub["year"] = clean_sub["date_dt"].dt.year
        clean_sub["month"] = clean_sub["date_dt"].dt.month
        clean_sub["symbol"] = symbol

        clean_sub = clean_sub.sort_values("date_dt")
        clean_sub = clean_sub.drop_duplicates(subset=["symbol", "year", "month"], keep="last")

        clean_sub["month_end"] = clean_sub["date_dt"].dt.date
        clean_sub["adj_close"] = clean_sub[close_col].astype(float)
        frames.append(clean_sub[["symbol", "month_end", "adj_close"]])

    if not frames:
        return pd.DataFrame(columns=["symbol", "month_end", "adj_close"])

    return pd.concat(frames, ignore_index=True)


def clean_market(symbols: Sequence[str]) -> pd.DataFrame:
    """Read raw market snapshot JSON files and standardize snapshot data."""
    records: list[dict[str, object]] = []

    for symbol in symbols:
        market_file = RAW_MARKET_DIR / f"{symbol}_market.json"
        if not market_file.exists():
            continue

        try:
            with market_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Failed reading %s: %s", market_file, exc)
            continue

        snap_str = data.get("downloaded_at")
        if snap_str:
            snap_date = pd.to_datetime(snap_str).date()
        else:
            snap_date = pd.to_datetime(SNAPSHOT_DATE).date()

        records.append(
            {
                "symbol": symbol,
                "snapshot_date": snap_date,
                "market_cap": (
                    float(data["market_cap"]) if data.get("market_cap") is not None else None
                ),
                "shares_outstanding": (
                    float(data["shares_outstanding"])
                    if data.get("shares_outstanding") is not None
                    else None
                ),
                "currency": str(data.get("currency", "INR")).strip(),
            }
        )

    if not records:
        return pd.DataFrame(
            columns=["symbol", "snapshot_date", "market_cap", "shares_outstanding", "currency"]
        )

    return pd.DataFrame(records)


def run_quality_checks(
    financials_df: pd.DataFrame,
    market_df: pd.DataFrame,
) -> pd.DataFrame:
    """Execute four data quality rules and log Capex sign convention summary."""
    issues: list[dict[str, object]] = []

    if not financials_df.empty:
        pivoted = financials_df.pivot(
            index=["symbol", "fiscal_year"],
            columns="line_item",
            values="value_inr",
        )

        # Check 1: Balance sheet identity (assets = liabilities + equity within 2%)
        bs_cols = ["total_assets", "total_liabilities", "shareholders_equity"]
        if all(c in pivoted.columns for c in bs_cols):
            bs_data = pivoted[bs_cols].dropna()
            ta = bs_data["total_assets"]
            tl = bs_data["total_liabilities"]
            se = bs_data["shareholders_equity"]
            non_zero = ta.abs() > 0
            imbalance = ((ta - (tl + se)).abs() / ta.abs())
            flags = imbalance[non_zero & (imbalance > 0.02)]
            for (sym, fy), diff in flags.items():
                issues.append(
                    {
                        "symbol": sym,
                        "fiscal_year": int(fy),
                        "check_name": "balance_sheet_identity_mismatch",
                        "details": f"Discrepancy {diff:.2%}",
                    }
                )

        # Check 2: Negative or zero revenue
        if "revenue" in pivoted.columns:
            rev_data = pivoted["revenue"].dropna()
            invalid_rev = rev_data[rev_data <= 0]
            for (sym, fy), val in invalid_rev.items():
                issues.append(
                    {
                        "symbol": sym,
                        "fiscal_year": int(fy),
                        "check_name": "negative_or_zero_revenue",
                        "details": f"Revenue is {val}",
                    }
                )

        # Check 3: YoY jump > 300% or drop < -75% in revenue or total_assets
        for metric in ("revenue", "total_assets"):
            if metric in pivoted.columns:
                m_df = pivoted[metric].dropna().reset_index()
                m_df = m_df.sort_values(["symbol", "fiscal_year"])
                m_df["prior_fy"] = m_df.groupby("symbol")["fiscal_year"].shift(1)
                m_df["prior_val"] = m_df.groupby("symbol")[metric].shift(1)

                consecutive = (m_df["fiscal_year"] == m_df["prior_fy"] + 1) & (
                    m_df["prior_val"] > 0
                )
                filtered = m_df[consecutive].copy()
                filtered["yoy_change"] = (filtered[metric] - filtered["prior_val"]) / filtered[
                    "prior_val"
                ]

                anomalies = filtered[
                    (filtered["yoy_change"] > 3.0) | (filtered["yoy_change"] < -0.75)
                ]
                for _, row in anomalies.iterrows():
                    issues.append(
                        {
                            "symbol": row["symbol"],
                            "fiscal_year": int(row["fiscal_year"]),
                            "check_name": f"yoy_{metric}_outlier",
                            "details": f"YoY change {row['yoy_change']:.1%}",
                        }
                    )

        # Capex sign convention summary
        if "capital_expenditure" in financials_df["line_item"].values:
            capex = financials_df[financials_df["line_item"] == "capital_expenditure"][
                "value_inr"
            ].dropna()
            pos = int((capex > 0).sum())
            neg = int((capex < 0).sum())
            zero = int((capex == 0).sum())
            logger.info(
                "Capital expenditure sign summary: %d positive, %d negative, %d zero",
                pos,
                neg,
                zero,
            )

    # Check 4: Non-INR reporting currency in market_df
    if not market_df.empty and "currency" in market_df.columns:
        non_inr = market_df[market_df["currency"].astype(str).str.upper() != "INR"]
        for _, row in non_inr.iterrows():
            issues.append(
                {
                    "symbol": row["symbol"],
                    "fiscal_year": None,
                    "check_name": "non_inr_currency",
                    "details": f"Currency is {row['currency']}",
                }
            )

    issues_df = pd.DataFrame(
        issues, columns=["symbol", "fiscal_year", "check_name", "details"]
    )
    issues_path = INTERIM_DIR / "data_quality_issues.csv"
    issues_df.to_csv(issues_path, index=False)

    if not issues_df.empty:
        logger.info(
            "Data quality checks completed with issues:\n%s",
            issues_df["check_name"].value_counts().to_string(),
        )
    else:
        logger.info("Data quality checks completed: zero issues flagged.")

    return issues_df
