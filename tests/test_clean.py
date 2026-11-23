"""Unit tests for cleaning, fiscal year alignment, and priority mapping."""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.screener.clean import (
    align_fiscal_year,
    clean_financials,
    clean_prices,
    run_quality_checks,
)


def test_align_fiscal_year_standard_march() -> None:
    """Test that March 31 period end maps to the current calendar year."""
    assert align_fiscal_year(date(2025, 3, 31)) == 2025
    assert align_fiscal_year(date(2024, 3, 31)) == 2024


def test_align_fiscal_year_december() -> None:
    """Test that December 31 period end maps to next calendar year."""
    assert align_fiscal_year(date(2024, 12, 31)) == 2025
    assert align_fiscal_year(date(2023, 12, 31)) == 2024


def test_align_fiscal_year_mid_year() -> None:
    """Test that June and April period ends map to next calendar year."""
    assert align_fiscal_year(date(2024, 6, 30)) == 2025
    assert align_fiscal_year(date(2024, 4, 1)) == 2025
    assert align_fiscal_year(date(2025, 1, 15)) == 2025


def test_priority_field_mapping() -> None:
    """Test that lower priority number wins and falls back when priority 1 is missing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cfg_dir = tmp_path / "config"
        raw_dir = tmp_path / "data" / "raw" / "financials"
        cfg_dir.mkdir(parents=True)
        raw_dir.mkdir(parents=True)

        mapping_csv = cfg_dir / "field_mapping.csv"
        mapping_csv.write_text(
            "statement,standard_field,source_label,priority\n"
            "income,revenue,Total Revenue,1\n"
            "income,revenue,Operating Revenue,2\n"
            "income,net_income,Net Income,1\n",
            encoding="utf-8",
        )

        df_a = pd.DataFrame(
            {"2025-03-31": [1000.0, 950.0, 150.0]},
            index=["Total Revenue", "Operating Revenue", "Net Income"],
        )
        df_a.to_parquet(raw_dir / "TESTA_income.parquet")

        df_b = pd.DataFrame(
            {"2025-03-31": [None, 800.0, 100.0]},
            index=["Total Revenue", "Operating Revenue", "Net Income"],
        )
        df_b.to_parquet(raw_dir / "TESTB_income.parquet")

        with (
            patch("src.screener.clean.CONFIG_DIR", cfg_dir),
            patch("src.screener.clean.RAW_FINANCIALS_DIR", raw_dir),
        ):
            res_df = clean_financials(["TESTA", "TESTB"])

        assert not res_df.empty
        rev_a = res_df[
            (res_df["symbol"] == "TESTA") & (res_df["line_item"] == "revenue")
        ]["value_inr"].iloc[0]
        assert rev_a == 1000.0

        rev_b = res_df[
            (res_df["symbol"] == "TESTB") & (res_df["line_item"] == "revenue")
        ]["value_inr"].iloc[0]
        assert rev_b == 800.0


def test_duplicate_fiscal_year_resolution() -> None:
    """Test that the latest period is kept when multiple dates map to the same fiscal year."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cfg_dir = tmp_path / "config"
        raw_dir = tmp_path / "data" / "raw" / "financials"
        cfg_dir.mkdir(parents=True)
        raw_dir.mkdir(parents=True)

        mapping_csv = cfg_dir / "field_mapping.csv"
        mapping_csv.write_text(
            "statement,standard_field,source_label,priority\n"
            "income,revenue,Total Revenue,1\n",
            encoding="utf-8",
        )

        df = pd.DataFrame(
            {"2024-06-30": [400.0], "2024-12-31": [600.0]},
            index=["Total Revenue"],
        )
        df.to_parquet(raw_dir / "TESTDUP_income.parquet")

        with (
            patch("src.screener.clean.CONFIG_DIR", cfg_dir),
            patch("src.screener.clean.RAW_FINANCIALS_DIR", raw_dir),
        ):
            res = clean_financials(["TESTDUP"])

        assert len(res) == 1
        assert res["fiscal_year"].iloc[0] == 2025
        assert res["value_inr"].iloc[0] == 600.0


def test_clean_prices_monthly_dedup() -> None:
    """Test that monthly prices deduplicate to the latest date per calendar month."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_dir = Path(tmp_dir)
        df = pd.DataFrame(
            {
                "date": ["2024-03-15", "2024-03-28", "2024-04-10"],
                "adj_close": [100.0, 105.0, 110.0],
            }
        )
        df.to_parquet(raw_dir / "TESTP_monthly.parquet")

        with patch("src.screener.clean.RAW_PRICES_DIR", raw_dir):
            cleaned = clean_prices(["TESTP"])

        assert len(cleaned) == 2
        march_row = cleaned[cleaned["month_end"] == date(2024, 3, 28)]
        assert not march_row.empty
        assert march_row["adj_close"].iloc[0] == 105.0


def test_quality_checks_identity_and_revenue() -> None:
    """Test detection of balance sheet mismatch and negative revenue."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        interim_dir = Path(tmp_dir)

        fin_rows = [
            {"symbol": "X", "fiscal_year": 2024, "line_item": "total_assets", "value_inr": 100.0},
            {
                "symbol": "X",
                "fiscal_year": 2024,
                "line_item": "total_liabilities",
                "value_inr": 50.0,
            },
            {
                "symbol": "X",
                "fiscal_year": 2024,
                "line_item": "shareholders_equity",
                "value_inr": 40.0,
            },
            {"symbol": "X", "fiscal_year": 2024, "line_item": "revenue", "value_inr": -10.0},
            {
                "symbol": "X",
                "fiscal_year": 2024,
                "line_item": "capital_expenditure",
                "value_inr": -15.0,
            },
        ]
        fin_df = pd.DataFrame(fin_rows)
        market_rows = [
            {
                "symbol": "X",
                "snapshot_date": date(2024, 3, 31),
                "market_cap": 500.0,
                "currency": "USD",
            }
        ]
        market_df = pd.DataFrame(market_rows)

        with patch("src.screener.clean.INTERIM_DIR", interim_dir):
            issues = run_quality_checks(fin_df, market_df)

        check_names = set(issues["check_name"])
        assert "balance_sheet_identity_mismatch" in check_names
        assert "negative_or_zero_revenue" in check_names
        assert "non_inr_currency" in check_names
