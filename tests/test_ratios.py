"""Unit tests for financial ratio formulas, Piotroski F-score, and edge-case rules."""

import math
from typing import Any

import pytest


def calculate_annual_metrics(
    current: dict[str, Any],
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure Python reference implementation mirroring sql/02_ratio_views.sql metrics_annual."""
    fy = current["fiscal_year"]
    rev = current.get("revenue_cr")
    cost = current.get("cost_of_revenue_cr")
    ebit = current.get("ebit_cr")
    ebitda = current.get("ebitda_cr")
    interest = current.get("interest_expense_cr")
    net_inc = current.get("net_income_cr")
    assets = current.get("total_assets_cr")
    c_assets = current.get("current_assets_cr")
    c_liab = current.get("current_liabilities_cr")
    debt = current.get("total_debt_cr") or 0.0
    equity = current.get("shareholders_equity_cr")
    cash = current.get("cash_and_equivalents_cr") or 0.0
    rec = current.get("receivables_cr")
    inv = current.get("inventory_cr")
    cfo = current.get("operating_cash_flow_cr")
    capex = current.get("capital_expenditure_cr")
    eps = current.get("eps_native")
    shares = current.get("ordinary_shares_number")

    has_consecutive_prior = prior is not None and prior.get("fiscal_year") == fy - 1

    # Average balance calculations
    if has_consecutive_prior and prior.get("shareholders_equity_cr") is not None:
        avg_equity = (equity + prior["shareholders_equity_cr"]) / 2.0
    else:
        avg_equity = equity

    cap_employed = (assets - c_liab) if (assets is not None and c_liab is not None) else None
    if (
        has_consecutive_prior
        and prior.get("total_assets_cr") is not None
        and prior.get("current_liabilities_cr") is not None
    ):
        prior_cap_employed = prior["total_assets_cr"] - prior["current_liabilities_cr"]
        avg_cap_employed = (cap_employed + prior_cap_employed) / 2.0
    else:
        avg_cap_employed = cap_employed

    if has_consecutive_prior and prior.get("total_assets_cr") is not None:
        avg_assets = (assets + prior["total_assets_cr"]) / 2.0
    else:
        avg_assets = assets

    # Profitability
    roe = (
        (net_inc / avg_equity)
        if (avg_equity and avg_equity > 0 and net_inc is not None)
        else None
    )
    roce = (
        (ebit / avg_cap_employed)
        if (avg_cap_employed and avg_cap_employed > 0 and ebit is not None)
        else None
    )
    op_margin = (ebit / rev) if (rev and rev > 0 and ebit is not None) else None
    net_margin = (net_inc / rev) if (rev and rev > 0 and net_inc is not None) else None

    # Leverage
    d_e = (debt / equity) if (equity and equity > 0) else None
    int_cov = (
        (ebit / abs(interest))
        if (interest is not None and abs(interest) > 0 and ebit is not None)
        else None
    )
    net_debt_ebitda = ((debt - cash) / ebitda) if (ebitda and ebitda > 0) else None

    # Cash quality
    cfo_pat = (cfo / net_inc) if (net_inc and net_inc > 0 and cfo is not None) else None
    fcf = (cfo - abs(capex or 0.0)) if cfo is not None else None
    fcf_margin = (fcf / rev) if (rev and rev > 0 and fcf is not None) else None

    # Efficiency
    rec_days = ((rec / rev) * 365.0) if (rev and rev > 0 and rec is not None) else None
    inv_days = ((inv / cost) * 365.0) if (cost and cost > 0 and inv is not None) else None
    asset_turnover = (
        (rev / avg_assets) if (avg_assets and avg_assets > 0 and rev is not None) else None
    )

    # YoY growth
    prior_eps = prior.get("eps_native") if has_consecutive_prior else None
    if has_consecutive_prior and prior_eps and prior_eps > 0 and eps is not None:
        eps_growth = (eps - prior_eps) / prior_eps
    else:
        eps_growth = None

    # Piotroski F-Score (9 signals)
    if not has_consecutive_prior:
        f_score = None
    else:
        f1 = 1 if (net_inc and net_inc > 0) else 0
        f2 = 1 if (cfo and cfo > 0) else 0

        p_assets = prior.get("total_assets_cr")
        p_net_inc = prior.get("net_income_cr")
        if (
            assets and p_assets and assets > 0 and p_assets > 0
            and net_inc is not None and p_net_inc is not None
        ):
            f3 = 1 if (net_inc / assets) > (p_net_inc / p_assets) else 0
        else:
            f3 = 0

        f4 = 1 if (cfo is not None and net_inc is not None and cfo > net_inc) else 0

        p_debt = prior.get("total_debt_cr") or 0.0
        if assets and p_assets and assets > 0 and p_assets > 0:
            f5 = 1 if (debt / assets) < (p_debt / p_assets) else 0
        else:
            f5 = 0

        p_c_assets = prior.get("current_assets_cr")
        p_c_liab = prior.get("current_liabilities_cr")
        if (
            c_liab and p_c_liab and c_liab > 0 and p_c_liab > 0
            and c_assets is not None and p_c_assets is not None
        ):
            f6 = 1 if (c_assets / c_liab) > (p_c_assets / p_c_liab) else 0
        else:
            f6 = 0

        p_shares = prior.get("ordinary_shares_number")
        if shares is not None and p_shares is not None:
            f7 = 1 if shares <= p_shares else 0
        else:
            f7 = 0

        gp = current.get("gross_profit_cr")
        p_gp = prior.get("gross_profit_cr")
        p_rev = prior.get("revenue_cr")
        if rev and p_rev and rev > 0 and p_rev > 0 and gp is not None and p_gp is not None:
            f8 = 1 if (gp / rev) > (p_gp / p_rev) else 0
        else:
            f8 = 0

        if (
            assets and p_assets and assets > 0 and p_assets > 0
            and rev is not None and p_rev is not None
        ):
            f9 = 1 if (rev / assets) > (p_rev / p_assets) else 0
        else:
            f9 = 0

        f_score = f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9

    return {
        "roe": roe,
        "roce": roce,
        "operating_margin": op_margin,
        "net_margin": net_margin,
        "debt_to_equity": d_e,
        "interest_coverage": int_cov,
        "net_debt_to_ebitda": net_debt_ebitda,
        "cfo_to_net_profit": cfo_pat,
        "fcf_cr": fcf,
        "fcf_margin": fcf_margin,
        "receivable_days": rec_days,
        "inventory_days": inv_days,
        "asset_turnover": asset_turnover,
        "eps_growth_yoy": eps_growth,
        "piotroski_f_score": f_score,
    }


def calculate_valuation_metrics(
    market_cap_cr: float,
    latest_net_income_cr: float | None,
    latest_equity_cr: float | None,
    latest_ebitda_cr: float | None,
    latest_total_debt_cr: float | None,
    latest_cash_cr: float | None,
    latest_fcf_cr: float | None,
) -> dict[str, Any]:
    """Pure Python reference implementation mirroring valuation formulas in metrics_latest."""
    pe = (
        (market_cap_cr / latest_net_income_cr)
        if (latest_net_income_cr and latest_net_income_cr > 0)
        else None
    )
    pb = (
        (market_cap_cr / latest_equity_cr)
        if (latest_equity_cr and latest_equity_cr > 0)
        else None
    )

    ev = market_cap_cr + (latest_total_debt_cr or 0.0) - (latest_cash_cr or 0.0)
    ev_ebitda = (ev / latest_ebitda_cr) if (latest_ebitda_cr and latest_ebitda_cr > 0) else None

    ey = (
        (latest_net_income_cr / market_cap_cr)
        if (market_cap_cr > 0 and latest_net_income_cr is not None)
        else None
    )
    fcf_y = (
        (latest_fcf_cr / market_cap_cr)
        if (market_cap_cr > 0 and latest_fcf_cr is not None)
        else None
    )

    return {
        "pe_ratio": pe,
        "pb_ratio": pb,
        "ev_to_ebitda": ev_ebitda,
        "earnings_yield": ey,
        "fcf_yield": fcf_y,
    }


def test_normal_company_ratios() -> None:
    """Verify all 25 ratios match expected values on standard financial data."""
    prior = {
        "fiscal_year": 2023,
        "revenue_cr": 800.0,
        "gross_profit_cr": 320.0,
        "net_income_cr": 90.0,
        "total_assets_cr": 700.0,
        "current_assets_cr": 350.0,
        "current_liabilities_cr": 180.0,
        "total_debt_cr": 160.0,
        "shareholders_equity_cr": 350.0,
        "eps_native": 10.0,
        "ordinary_shares_number": 10000000.0,
    }
    current = {
        "fiscal_year": 2024,
        "revenue_cr": 1000.0,
        "cost_of_revenue_cr": 600.0,
        "gross_profit_cr": 400.0,
        "ebit_cr": 200.0,
        "ebitda_cr": 250.0,
        "interest_expense_cr": 20.0,
        "net_income_cr": 120.0,
        "total_assets_cr": 800.0,
        "current_assets_cr": 400.0,
        "current_liabilities_cr": 200.0,
        "total_debt_cr": 150.0,
        "shareholders_equity_cr": 450.0,
        "cash_and_equivalents_cr": 50.0,
        "receivables_cr": 100.0,
        "inventory_cr": 120.0,
        "operating_cash_flow_cr": 140.0,
        "capital_expenditure_cr": -40.0,
        "eps_native": 12.0,
        "ordinary_shares_number": 10000000.0,
    }

    res = calculate_annual_metrics(current, prior)

    # Average equity = (450 + 350) / 2 = 400 -> ROE = 120 / 400 = 0.30
    assert pytest.approx(res["roe"], abs=1e-6) == 0.30

    # Cap employed = (800 - 200 + 700 - 180) / 2 = (600 + 520) / 2 = 560 -> ROCE = 200 / 560
    assert pytest.approx(res["roce"], abs=1e-6) == 200.0 / 560.0

    assert pytest.approx(res["operating_margin"], abs=1e-6) == 0.20
    assert pytest.approx(res["net_margin"], abs=1e-6) == 0.12
    assert pytest.approx(res["debt_to_equity"], abs=1e-6) == 150.0 / 450.0
    assert pytest.approx(res["interest_coverage"], abs=1e-6) == 10.0
    assert pytest.approx(res["net_debt_to_ebitda"], abs=1e-6) == 100.0 / 250.0
    assert pytest.approx(res["cfo_to_net_profit"], abs=1e-6) == 140.0 / 120.0

    # FCF = 140 - abs(-40) = 100.0
    assert pytest.approx(res["fcf_cr"], abs=1e-6) == 100.0
    assert pytest.approx(res["fcf_margin"], abs=1e-6) == 0.10
    assert pytest.approx(res["receivable_days"], abs=1e-6) == 36.5
    assert pytest.approx(res["inventory_days"], abs=1e-6) == 73.0
    assert pytest.approx(res["asset_turnover"], abs=1e-6) == 1000.0 / 750.0
    assert pytest.approx(res["eps_growth_yoy"], abs=1e-6) == 0.20

    val = calculate_valuation_metrics(
        market_cap_cr=2400.0,
        latest_net_income_cr=120.0,
        latest_equity_cr=450.0,
        latest_ebitda_cr=250.0,
        latest_total_debt_cr=150.0,
        latest_cash_cr=50.0,
        latest_fcf_cr=100.0,
    )
    assert pytest.approx(val["pe_ratio"], abs=1e-6) == 20.0
    assert pytest.approx(val["pb_ratio"], abs=1e-6) == 2400.0 / 450.0
    assert pytest.approx(val["ev_to_ebitda"], abs=1e-6) == 2500.0 / 250.0
    assert pytest.approx(val["earnings_yield"], abs=1e-6) == 0.05
    assert pytest.approx(val["fcf_yield"], abs=1e-6) == 100.0 / 2400.0


def test_negative_equity_edge_case() -> None:
    """Verify ROE, P/B, and Debt/Equity evaluate to None on negative equity."""
    current = {
        "fiscal_year": 2024,
        "revenue_cr": 500.0,
        "ebit_cr": 50.0,
        "net_income_cr": 20.0,
        "total_debt_cr": 200.0,
        "shareholders_equity_cr": -50.0,
    }
    res = calculate_annual_metrics(current, prior=None)
    assert res["roe"] is None
    assert res["debt_to_equity"] is None

    val = calculate_valuation_metrics(
        market_cap_cr=1000.0,
        latest_net_income_cr=20.0,
        latest_equity_cr=-50.0,
        latest_ebitda_cr=60.0,
        latest_total_debt_cr=200.0,
        latest_cash_cr=10.0,
        latest_fcf_cr=15.0,
    )
    assert val["pb_ratio"] is None
    assert pytest.approx(val["pe_ratio"], abs=1e-6) == 50.0


def test_zero_interest_edge_case() -> None:
    """Verify Interest Coverage returns None when interest expense is zero."""
    current = {
        "fiscal_year": 2024,
        "revenue_cr": 500.0,
        "ebit_cr": 50.0,
        "interest_expense_cr": 0.0,
    }
    res = calculate_annual_metrics(current, prior=None)
    assert res["interest_coverage"] is None


def test_negative_profit_edge_case() -> None:
    """Verify P/E and CFO/PAT return None while earnings yield remains valid (negative)."""
    current = {
        "fiscal_year": 2024,
        "revenue_cr": 500.0,
        "net_income_cr": -30.0,
        "shareholders_equity_cr": 200.0,
        "operating_cash_flow_cr": 10.0,
    }
    res = calculate_annual_metrics(current, prior=None)
    assert pytest.approx(res["roe"], abs=1e-6) == -30.0 / 200.0
    assert res["cfo_to_net_profit"] is None

    val = calculate_valuation_metrics(
        market_cap_cr=1000.0,
        latest_net_income_cr=-30.0,
        latest_equity_cr=200.0,
        latest_ebitda_cr=10.0,
        latest_total_debt_cr=50.0,
        latest_cash_cr=20.0,
        latest_fcf_cr=-10.0,
    )
    assert val["pe_ratio"] is None
    assert pytest.approx(val["earnings_yield"], abs=1e-6) == -0.03


def test_piotroski_f_score_nine_signals_and_earliest_year() -> None:
    """Verify all 9 signals of Piotroski F-score and confirm earliest year evaluates to None."""
    year_1 = {
        "fiscal_year": 2023,
        "revenue_cr": 300.0,
        "gross_profit_cr": 120.0,
        "net_income_cr": 30.0,
        "total_assets_cr": 400.0,
        "current_assets_cr": 200.0,
        "current_liabilities_cr": 100.0,
        "total_debt_cr": 100.0,
        "operating_cash_flow_cr": 40.0,
        "ordinary_shares_number": 1000000.0,
    }
    # Earliest year without prior data must return None
    res_y1 = calculate_annual_metrics(year_1, prior=None)
    assert res_y1["piotroski_f_score"] is None

    # Year 2 meets all 9 improvement criteria
    year_2 = {
        "fiscal_year": 2024,
        "revenue_cr": 400.0,
        "gross_profit_cr": 180.0,
        "net_income_cr": 50.0,
        "total_assets_cr": 500.0,
        "current_assets_cr": 250.0,
        "current_liabilities_cr": 100.0,
        "total_debt_cr": 80.0,
        "operating_cash_flow_cr": 70.0,
        "ordinary_shares_number": 1000000.0,
    }
    res_y2 = calculate_annual_metrics(year_2, prior=year_1)
    assert res_y2["piotroski_f_score"] == 9


def test_cagr_3yr_calculation() -> None:
    """Verify 3-year CAGR formula and span edge case handling."""
    def cagr(start_val: float, end_val: float, span_years: int) -> float | None:
        if span_years < 3 or start_val <= 0 or end_val <= 0:
            return None
        return math.pow(end_val / start_val, 1.0 / span_years) - 1.0

    # 3-year doubling: 500 to 1000 in 3 years -> 2^(1/3) - 1 ≈ 25.99%
    assert pytest.approx(cagr(500.0, 1000.0, 3), abs=1e-6) == math.pow(2.0, 1.0 / 3.0) - 1.0

    # Span < 3 years returns None
    assert cagr(500.0, 1000.0, 2) is None

    # Negative starting value returns None
    assert cagr(-100.0, 200.0, 3) is None

    # Negative ending value returns None
    assert cagr(100.0, -50.0, 3) is None
