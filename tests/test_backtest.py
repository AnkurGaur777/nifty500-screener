"""Unit tests for look-ahead-aware backtesting, quintile allocation, and placebo testing."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.screener.backtest import (
    assign_quintiles,
    build_pit_cohort_metrics,
    compute_backtest_summary,
    compute_cohort_returns,
    get_available_date,
    is_data_available,
    run_placebo_test,
)


def test_get_available_date_and_availability() -> None:
    """Test look-ahead lag date calculation and reporting availability boundaries."""
    assert get_available_date(2024) == date(2024, 6, 30)
    assert get_available_date(2025) == date(2025, 6, 30)

    # FY2025 is unavailable before June 30, 2025
    assert is_data_available(2025, date(2025, 5, 31)) is False
    assert is_data_available(2025, date(2025, 6, 29)) is False

    # FY2025 is available on and after June 30, 2025
    assert is_data_available(2025, date(2025, 6, 30)) is True
    assert is_data_available(2025, date(2025, 7, 1)) is True
    assert is_data_available(2024, date(2025, 6, 30)) is True


def test_quintile_assignment_and_forward_returns() -> None:
    """Test quintile allocation and 12-month forward return calculation."""
    # 20 synthetic companies sorted by score
    symbols = [f"C{i:02d}" for i in range(1, 21)]
    scores = [100.0 - (i * 4.0) for i in range(20)]
    entry_prices = [100.0] * 20
    # Positive forward returns for top companies, negative for bottom
    exit_prices = [100.0 + (30.0 - (i * 3.0)) for i in range(20)]
    fwd_returns = [(exit_prices[i] / entry_prices[i]) - 1.0 for i in range(20)]

    df = pd.DataFrame(
        {
            "symbol": symbols,
            "total_score": scores,
            "entry_price": entry_prices,
            "exit_price": exit_prices,
            "fwd_return": fwd_returns,
            "is_eligible": [True] * 20,
        }
    )

    assigned = assign_quintiles(df)

    # Each quintile should contain exactly 4 companies (20 / 5)
    counts = assigned["quintile"].value_counts()
    assert (counts == 4).all()

    # Verify top scores belong to Q1 and lowest scores belong to Q5
    q1_symbols = set(assigned[assigned["quintile"] == "Q1"]["symbol"])
    q5_symbols = set(assigned[assigned["quintile"] == "Q5"]["symbol"])
    assert q1_symbols == {"C01", "C02", "C03", "C04"}
    assert q5_symbols == {"C17", "C18", "C19", "C20"}

    # Test cohort returns calculation with benchmark return 10.0%
    cohort_returns = compute_cohort_returns(
        formation_fy=2024,
        formation_date=date(2024, 6, 30),
        exit_date=date(2025, 6, 30),
        scored_cohort_df=assigned,
        bm_return=0.10,
    )

    assert len(cohort_returns) == 5
    for _, row in cohort_returns.iterrows():
        q = row["quintile"]
        sub = assigned[assigned["quintile"] == q]
        expected_ret = round(float(sub["fwd_return"].mean()) * 100.0, 2)
        assert pytest.approx(row["return_pct"], abs=1e-2) == expected_ret
        assert pytest.approx(row["benchmark_return_pct"], abs=1e-2) == 10.0
        assert pytest.approx(row["excess_return_pct"], abs=1e-2) == expected_ret - 10.0


def test_deterministic_placebo_spread() -> None:
    """Test deterministic Monte Carlo placebo test produces reproducible null distribution."""
    # Create 2 synthetic cohorts with 25 companies each
    cohorts = []
    rng_data = np.random.default_rng(123)

    for fy in [2023, 2024]:
        n = 25
        rets = rng_data.normal(loc=0.15, scale=0.20, size=n)
        cdf = pd.DataFrame(
            {
                "symbol": [f"S{fy}_{i:02d}" for i in range(n)],
                "fwd_return": rets,
                "is_eligible": [True] * n,
            }
        )
        cohorts.append(cdf)

    res1 = run_placebo_test(cohorts, n_trials=500, random_seed=42)
    res2 = run_placebo_test(cohorts, n_trials=500, random_seed=42)

    # Determinism check with fixed seed
    assert res1 == res2

    # Distribution bounds check
    p5 = res1["placebo_p5"]
    p50 = res1["placebo_p50"]
    p95 = res1["placebo_p95"]

    assert p5 <= p50 <= p95
    # Shuffling random returns across companies should yield a median spread near zero
    assert abs(p50) < 0.05


def test_compute_backtest_summary_metrics() -> None:
    """Test performance summary aggregation across multiple historical cohorts."""
    cohort_rows = [
        # FY2023 cohort
        {"cohort_fy": 2023, "quintile": "Q1", "return_pct": 25.0, "benchmark_return_pct": 15.0},
        {"cohort_fy": 2023, "quintile": "Q2", "return_pct": 20.0, "benchmark_return_pct": 15.0},
        {"cohort_fy": 2023, "quintile": "Q3", "return_pct": 15.0, "benchmark_return_pct": 15.0},
        {"cohort_fy": 2023, "quintile": "Q4", "return_pct": 10.0, "benchmark_return_pct": 15.0},
        {"cohort_fy": 2023, "quintile": "Q5", "return_pct": 5.0, "benchmark_return_pct": 15.0},
        # FY2024 cohort
        {"cohort_fy": 2024, "quintile": "Q1", "return_pct": 35.0, "benchmark_return_pct": 20.0},
        {"cohort_fy": 2024, "quintile": "Q2", "return_pct": 25.0, "benchmark_return_pct": 20.0},
        {"cohort_fy": 2024, "quintile": "Q3", "return_pct": 18.0, "benchmark_return_pct": 20.0},
        {"cohort_fy": 2024, "quintile": "Q4", "return_pct": 12.0, "benchmark_return_pct": 20.0},
        {"cohort_fy": 2024, "quintile": "Q5", "return_pct": -5.0, "benchmark_return_pct": 20.0},
    ]
    df = pd.DataFrame(cohort_rows)

    placebo_dict = {"placebo_p5": -0.06, "placebo_p50": 0.002, "placebo_p95": 0.058}
    summary = compute_backtest_summary(df, placebo_dict)

    assert set(summary["quintile"]) == {
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "Q5",
        "Benchmark",
        "Q1_minus_Q5_Spread",
        "Placebo_Spread_p50_p95",
    }

    q1_row = summary[summary["quintile"] == "Q1"].iloc[0]
    # Q1 mean annual = (25.0 + 35.0) / 2 = 30.0%
    assert pytest.approx(q1_row["mean_annual_return"], abs=1e-2) == 30.0
    # Q1 cumulative = (1.25 * 1.35 - 1) * 100 = 68.75%
    assert pytest.approx(q1_row["cumulative_return"], abs=1e-2) == 68.75
    # Q1 hit rate = 2/2 = 100.0%
    assert pytest.approx(q1_row["hit_rate_vs_benchmark"], abs=1e-2) == 100.0

    bm_row = summary[summary["quintile"] == "Benchmark"].iloc[0]
    # Benchmark mean = (15.0 + 20.0) / 2 = 17.5%
    assert pytest.approx(bm_row["mean_annual_return"], abs=1e-2) == 17.5


def test_pit_cohort_metrics_fcf_resolution() -> None:
    """Test point-in-time metrics creation and clean FCF resolution."""
    companies_df = pd.DataFrame(
        [
            {"company_id": 1, "symbol": "C1", "name": "Company 1", "sector": "IT"},
            {"company_id": 2, "symbol": "C2", "name": "Company 2", "sector": "IT"},
        ]
    )
    # Company 1 has fcf_cr in metrics_annual; Company 2 has NaN in metrics_annual
    metrics_annual_df = pd.DataFrame(
        [
            {
                "company_id": 1,
                "fiscal_year": 2024,
                "roe": 20.0,
                "roce": 25.0,
                "operating_margin": 15.0,
                "cfo_to_net_profit": 1.1,
                "piotroski_f_score": 8.0,
                "eps_growth_yoy": 0.12,
                "debt_to_equity": 0.2,
                "interest_coverage": 10.0,
                "net_debt_to_ebitda": 0.1,
                "fcf_cr": 500.0,
            },
            {
                "company_id": 2,
                "fiscal_year": 2024,
                "roe": 18.0,
                "roce": 22.0,
                "operating_margin": 14.0,
                "cfo_to_net_profit": 0.9,
                "piotroski_f_score": 7.0,
                "eps_growth_yoy": 0.08,
                "debt_to_equity": 0.3,
                "interest_coverage": 8.0,
                "net_debt_to_ebitda": 0.2,
                "fcf_cr": np.nan,
            },
        ]
    )
    financials_wide_df = pd.DataFrame(
        [
            {
                "company_id": 1,
                "fiscal_year": 2024,
                "revenue_cr": 2000.0,
                "net_income_cr": 400.0,
                "shareholders_equity_cr": 2000.0,
                "ebitda_cr": 500.0,
                "total_debt_cr": 400.0,
                "cash_and_equivalents_cr": 100.0,
                "operating_cash_flow_cr": 600.0,
                "capital_expenditure_cr": -100.0,
            },
            {
                "company_id": 2,
                "fiscal_year": 2024,
                "revenue_cr": 1500.0,
                "net_income_cr": 300.0,
                "shareholders_equity_cr": 1500.0,
                "ebitda_cr": 400.0,
                "total_debt_cr": 300.0,
                "cash_and_equivalents_cr": 50.0,
                "operating_cash_flow_cr": 450.0,
                "capital_expenditure_cr": -150.0,
            },
        ]
    )
    market_df = pd.DataFrame(
        [
            {"company_id": 1, "shares_outstanding": 10_000_000, "market_cap": None},
            {"company_id": 2, "shares_outstanding": 5_000_000, "market_cap": None},
        ]
    )
    price_map = {
        (1, 2024, 6): 1000.0,
        (1, 2025, 6): 1200.0,
        (2, 2024, 6): 2000.0,
        (2, 2025, 6): 2200.0,
    }
    data = {
        "companies": companies_df,
        "metrics_annual": metrics_annual_df,
        "financials_wide": financials_wide_df,
        "market": market_df,
    }
    res = build_pit_cohort_metrics(
        2024,
        date(2024, 6, 30),
        date(2025, 6, 30),
        data,
        price_map,
    )
    assert len(res) == 2
    # C1: fcf_cr from metrics_annual = 500.0, mcap = 10m * 1000 / 1e7 = 1000.0 Cr
    # fcf_yield = 500 / 1000 * 100 = 50.0%
    c1 = res[res["company_id"] == 1].iloc[0]
    assert pytest.approx(c1["fcf_yield"], abs=1e-2) == 50.0

    # C2: fcf_cr resolved from CFO (450) - abs(Capex (-150)) = 300.0 Cr
    # mcap = 5m * 2000 / 1e7 = 1000.0 Cr -> fcf_yield = 300 / 1000 * 100 = 30.0%
    c2 = res[res["company_id"] == 2].iloc[0]
    assert pytest.approx(c2["fcf_yield"], abs=1e-2) == 30.0
