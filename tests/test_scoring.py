"""Unit tests for sector-relative scoring, factor ranking, and sensitivity analysis."""

import numpy as np
import pandas as pd
import pytest

from src.screener.scoring import (
    assign_peer_groups,
    compute_composite_scores,
    compute_factor_scores,
    compute_relative_percentiles,
    compute_sensitivity_overlap,
    winsorize_series,
)


def test_winsorize_series_clipping() -> None:
    """Test that extreme outliers outside 1st and 99th percentiles are clipped."""
    # Series with 100 values from 1 to 100 plus 2 extreme outliers
    data = [ -10000.0 ] + list(range(1, 101)) + [ 50000.0 ]
    series = pd.Series(data)

    clipped = winsorize_series(series, lower_q=0.01, upper_q=0.99)

    low_q = series.quantile(0.01)
    high_q = series.quantile(0.99)

    assert clipped.min() == low_q
    assert clipped.max() == high_q
    assert clipped.min() > -10000.0
    assert clipped.max() < 50000.0
    # Median / middle values should be untouched
    assert clipped.iloc[50] == series.iloc[50]


def test_assign_peer_groups_threshold() -> None:
    """Test sectors with fewer than 8 companies are grouped into Other Small Sectors."""
    sectors = (
        ["Information Technology"] * 15
        + ["Capital Goods"] * 10
        + ["Textiles"] * 5
        + ["Media Entertainment & Publication"] * 4
        + ["Diversified"] * 1
    )
    df = pd.DataFrame({"symbol": [f"S{i}" for i in range(len(sectors))], "sector": sectors})

    res = assign_peer_groups(df, min_peers=8)

    it_peers = res[res["sector"] == "Information Technology"]["peer_group"]
    assert (it_peers == "Information Technology").all()
    assert (res[res["sector"] == "Capital Goods"]["peer_group"] == "Capital Goods").all()
    assert (res[res["sector"] == "Textiles"]["peer_group"] == "Other Small Sectors").all()
    media_peers = res[res["sector"] == "Media Entertainment & Publication"]["peer_group"]
    assert (media_peers == "Other Small Sectors").all()
    assert (res[res["sector"] == "Diversified"]["peer_group"] == "Other Small Sectors").all()


def test_lower_is_better_rank_inversion() -> None:
    """Test that lower-is-better metrics are inverted so smaller values get higher ranks."""
    df = pd.DataFrame(
        {
            "symbol": ["A", "B", "C"],
            "peer_group": ["Sec1", "Sec1", "Sec1"],
            "avg_roce_3yr": [10.0, 20.0, 30.0],  # higher is better
            "debt_to_equity": [0.10, 0.50, 1.20],  # lower is better (inverted)
        }
    )
    res = compute_relative_percentiles(df)

    # For ROCE (higher is better): C > B > A
    c_roce = res.loc[res["symbol"] == "C", "avg_roce_3yr_rank"].iloc[0]
    a_roce = res.loc[res["symbol"] == "A", "avg_roce_3yr_rank"].iloc[0]
    assert c_roce > a_roce

    # For Debt/Equity (inverted): A (lowest debt) gets highest rank, C (highest debt) gets lowest
    a_de = res.loc[res["symbol"] == "A", "debt_to_equity_rank"].iloc[0]
    c_de = res.loc[res["symbol"] == "C", "debt_to_equity_rank"].iloc[0]
    assert a_de > c_de


def test_factor_score_minimum_metrics_rule() -> None:
    """Test factor score averaging and requiring at least 2 non-null metrics per factor."""
    # Factor with 5 metrics (quality): test with 3 metrics present vs 1 metric present
    df = pd.DataFrame(
        {
            "symbol": ["C1", "C2"],
            "peer_group": ["G1", "G1"],
            "years_available": [4, 4],
            # C1: 3 quality metrics valid -> factor score should be mean
            # C2: only 1 quality metric valid -> factor score must be NaN
            "avg_roce_3yr_rank": [80.0, 80.0],
            "avg_roe_3yr_rank": [60.0, np.nan],
            "operating_margin_rank": [70.0, np.nan],
            "cfo_to_net_profit_rank": [np.nan, np.nan],
            "piotroski_f_score_rank": [np.nan, np.nan],
            # Growth metrics (2 valid for both)
            "revenue_cagr_3yr_rank": [50.0, 50.0],
            "profit_cagr_3yr_rank": [60.0, 60.0],
            "eps_growth_yoy_rank": [np.nan, np.nan],
            # Value metrics (2 valid for both)
            "earnings_yield_rank": [40.0, 40.0],
            "pb_ratio_rank": [50.0, 50.0],
            "ev_to_ebitda_rank": [np.nan, np.nan],
            "fcf_yield_rank": [np.nan, np.nan],
            # Safety metrics (2 valid for both)
            "debt_to_equity_rank": [70.0, 70.0],
            "interest_coverage_rank": [80.0, 80.0],
            "net_debt_to_ebitda_rank": [np.nan, np.nan],
            "operating_margin_std_3yr_rank": [np.nan, np.nan],
        }
    )

    res = compute_factor_scores(df)

    # C1 Quality = mean(80, 60, 70) = 70.0
    assert pytest.approx(res.loc[res["symbol"] == "C1", "quality_score"].iloc[0], abs=1e-4) == 70.0
    # C2 Quality has only 1 metric -> NaN
    assert pd.isna(res.loc[res["symbol"] == "C2", "quality_score"].iloc[0])


def test_eligibility_criteria() -> None:
    """Test eligibility requires years_available >= 3 and all 4 factor scores present."""
    df = pd.DataFrame(
        {
            "symbol": ["EL1", "INEL1", "INEL2"],
            "sector": ["SecA", "SecA", "SecA"],
            "years_available": [4, 2, 4],
            "quality_score": [70.0, 70.0, 70.0],
            "growth_score": [60.0, 60.0, np.nan],  # INEL2 missing growth
            "value_score": [50.0, 50.0, 50.0],
            "safety_score": [80.0, 80.0, 80.0],
            "data_completeness_pct": [85.0, 85.0, 75.0],
        }
    )
    # Eligibility check logic from compute_factor_scores
    all_factors = (
        df["quality_score"].notna()
        & df["growth_score"].notna()
        & df["value_score"].notna()
        & df["safety_score"].notna()
    )
    df["is_eligible"] = all_factors & (df["years_available"] >= 3)

    weights = {"quality": 0.25, "growth": 0.25, "value": 0.25, "safety": 0.25}
    res = compute_composite_scores(df, weights)

    el1_eligible = res.loc[res["symbol"] == "EL1", "is_eligible"].iloc[0]
    assert bool(el1_eligible) is True
    assert pd.notna(res.loc[res["symbol"] == "EL1", "total_score"].iloc[0])

    inel1_eligible = res.loc[res["symbol"] == "INEL1", "is_eligible"].iloc[0]
    assert bool(inel1_eligible) is False
    assert pd.isna(res.loc[res["symbol"] == "INEL1", "total_score"].iloc[0])

    inel2_eligible = res.loc[res["symbol"] == "INEL2", "is_eligible"].iloc[0]
    assert bool(inel2_eligible) is False
    assert pd.isna(res.loc[res["symbol"] == "INEL2", "total_score"].iloc[0])


def test_sensitivity_overlap_calculation() -> None:
    """Test sensitivity analysis Top-20 overlap calculation."""
    # 25 eligible companies with differing factor balances
    rows = []
    for i in range(1, 26):
        rows.append(
            {
                "symbol": f"S{i:02d}",
                # Quality tilted: higher quality for lower i, higher value for higher i
                "quality_score": 100.0 - (i * 2.0),
                "growth_score": 50.0 + (i * 1.0),
                "value_score": 20.0 + (i * 3.0),
                "safety_score": 60.0,
            }
        )
    df = pd.DataFrame(rows)

    baseline_w = {"quality": 0.25, "growth": 0.25, "value": 0.25, "safety": 0.25}
    schemes = {
        "Quality-Tilted": {"quality": 0.40, "growth": 0.20, "value": 0.20, "safety": 0.20},
        "Value-Tilted": {"quality": 0.20, "growth": 0.15, "value": 0.45, "safety": 0.20},
    }

    res = compute_sensitivity_overlap(df, baseline_w, schemes)

    assert len(res) == 2
    for _, row in res.iterrows():
        assert 0 <= row["overlap_count"] <= 20
        assert 0.0 <= row["overlap_pct"] <= 100.0
        assert row["overlap_pct"] == round((row["overlap_count"] / 20.0) * 100.0, 1)
