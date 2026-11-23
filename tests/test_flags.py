"""Unit tests for the 8 red-flag rules and boundary edge cases."""

from typing import Any


def evaluate_red_flags(
    periods: list[dict[str, Any]],
    revenue_cagr_3yr: float | None = None,
) -> dict[str, bool]:
    """Pure Python reference implementation mirroring sql/03_flags_views.sql red_flags_detail."""
    if not periods:
        return {
            "flag_profit_up_cfo_down_2yr": False,
            "flag_low_cash_conversion": False,
            "flag_receivables_ballooning": False,
            "flag_debt_spike": False,
            "flag_weak_interest_coverage": False,
            "flag_persistent_negative_fcf": False,
            "flag_margin_erosion": False,
            "flag_high_other_income": False,
        }

    # Sort descending by fiscal_year
    sorted_p = sorted(periods, key=lambda x: x["fiscal_year"], reverse=True)
    t = sorted_p[0]
    t1 = sorted_p[1] if len(sorted_p) > 1 else None
    t2 = sorted_p[2] if len(sorted_p) > 2 else None

    # Rule 1: Net income up, CFO down for 2 consecutive years
    flag_1 = False
    if t1 and t2:
        if (
            t1["fiscal_year"] == t["fiscal_year"] - 1
            and t2["fiscal_year"] == t["fiscal_year"] - 2
            and t.get("net_income_cr") is not None
            and t1.get("net_income_cr") is not None
            and t2.get("net_income_cr") is not None
            and t.get("operating_cash_flow_cr") is not None
            and t1.get("operating_cash_flow_cr") is not None
            and t2.get("operating_cash_flow_cr") is not None
        ):
            cfo_falling = (
                t["operating_cash_flow_cr"]
                < t1["operating_cash_flow_cr"]
                < t2["operating_cash_flow_cr"]
            )
            if (t["net_income_cr"] > t1["net_income_cr"] > t2["net_income_cr"]) and cfo_falling:
                flag_1 = True

    # Rule 2: Low cash conversion for 2 consecutive years (when net income > 0)
    flag_2 = False
    if t1 and t1["fiscal_year"] == t["fiscal_year"] - 1:
        pat_t = t.get("net_income_cr") or 0.0
        pat_t1 = t1.get("net_income_cr") or 0.0
        cfo_pat_t = t.get("cfo_to_net_profit")
        cfo_pat_t1 = t1.get("cfo_to_net_profit")
        if (
            pat_t > 0
            and pat_t1 > 0
            and cfo_pat_t is not None
            and cfo_pat_t1 is not None
            and cfo_pat_t < 0.50
            and cfo_pat_t1 < 0.50
        ):
            flag_2 = True

    # Rule 3: Receivables ballooning (> 20 days increase, revenue growth < 10%)
    flag_3 = False
    if t1 and t1["fiscal_year"] == t["fiscal_year"] - 1:
        rec_t = t.get("receivable_days")
        rec_t1 = t1.get("receivable_days")
        rev_t = t.get("revenue_cr")
        rev_t1 = t1.get("revenue_cr")
        if (
            rec_t is not None
            and rec_t1 is not None
            and rev_t is not None
            and rev_t1 is not None
            and rev_t1 > 0
        ):
            growth = (rev_t - rev_t1) / rev_t1
            if (rec_t - rec_t1) > 20.0 and growth < 0.10:
                flag_3 = True

    # Rule 4: Debt spike (> 0.50 jump in D/E or equity turned negative with debt > 0)
    flag_4 = False
    if t1 and t1["fiscal_year"] == t["fiscal_year"] - 1:
        de_t = t.get("debt_to_equity")
        de_t1 = t1.get("debt_to_equity")
        eq_t = t.get("shareholders_equity_cr")
        eq_t1 = t1.get("shareholders_equity_cr")
        debt_t = t.get("total_debt_cr") or 0.0

        if de_t is not None and de_t1 is not None and (de_t - de_t1) > 0.50:
            flag_4 = True
        elif (
            eq_t1 is not None
            and eq_t1 > 0
            and eq_t is not None
            and eq_t <= 0
            and debt_t > 0
        ):
            flag_4 = True

    # Rule 5: Weak interest coverage (< 2.0)
    flag_5 = False
    int_cov = t.get("interest_coverage")
    if int_cov is not None and int_cov < 2.0:
        flag_5 = True

    # Rule 6: Persistent negative FCF (3 consecutive years < 0 with rev CAGR < 15% or null)
    flag_6 = False
    if t1 and t2:
        if (
            t1["fiscal_year"] == t["fiscal_year"] - 1
            and t2["fiscal_year"] == t["fiscal_year"] - 2
            and t.get("fcf_cr") is not None
            and t1.get("fcf_cr") is not None
            and t2.get("fcf_cr") is not None
        ):
            if (
                t["fcf_cr"] < 0
                and t1["fcf_cr"] < 0
                and t2["fcf_cr"] < 0
                and (revenue_cagr_3yr is None or revenue_cagr_3yr < 0.15)
            ):
                flag_6 = True

    # Rule 7: Margin erosion (2 consecutive years of decline with cumulative drop >= 2%)
    flag_7 = False
    if t1 and t2:
        if (
            t1["fiscal_year"] == t["fiscal_year"] - 1
            and t2["fiscal_year"] == t["fiscal_year"] - 2
            and t.get("operating_margin") is not None
            and t1.get("operating_margin") is not None
            and t2.get("operating_margin") is not None
        ):
            m_t = t["operating_margin"]
            m_t1 = t1["operating_margin"]
            m_t2 = t2["operating_margin"]
            if m_t < m_t1 < m_t2 and (m_t2 - m_t) >= 0.02:
                flag_7 = True

    # Rule 8: High other income (> 30% of EBIT + other income, when PAT > 0)
    flag_8 = False
    pat = t.get("net_income_cr")
    oth = t.get("other_income_cr")
    ebit = t.get("ebit_cr") or 0.0
    if pat is not None and pat > 0 and oth is not None:
        total_earnings = ebit + oth
        if total_earnings > 0 and (oth / total_earnings) > 0.30:
            flag_8 = True

    return {
        "flag_profit_up_cfo_down_2yr": flag_1,
        "flag_low_cash_conversion": flag_2,
        "flag_receivables_ballooning": flag_3,
        "flag_debt_spike": flag_4,
        "flag_weak_interest_coverage": flag_5,
        "flag_persistent_negative_fcf": flag_6,
        "flag_margin_erosion": flag_7,
        "flag_high_other_income": flag_8,
    }


def test_flag_profit_up_cfo_down_2yr() -> None:
    """Test profit divergence rule triggers only on 2 consecutive years of opposite moves."""
    p_trigger = [
        {"fiscal_year": 2024, "net_income_cr": 120.0, "operating_cash_flow_cr": 80.0},
        {"fiscal_year": 2023, "net_income_cr": 100.0, "operating_cash_flow_cr": 110.0},
        {"fiscal_year": 2022, "net_income_cr": 80.0, "operating_cash_flow_cr": 140.0},
    ]
    flags = evaluate_red_flags(p_trigger)
    assert flags["flag_profit_up_cfo_down_2yr"] is True

    # Non-trigger: CFO rose in 2024
    p_false = [
        {"fiscal_year": 2024, "net_income_cr": 120.0, "operating_cash_flow_cr": 130.0},
        {"fiscal_year": 2023, "net_income_cr": 100.0, "operating_cash_flow_cr": 110.0},
        {"fiscal_year": 2022, "net_income_cr": 80.0, "operating_cash_flow_cr": 140.0},
    ]
    assert evaluate_red_flags(p_false)["flag_profit_up_cfo_down_2yr"] is False

    # Non-trigger: missing year (non-consecutive)
    p_gap = [
        {"fiscal_year": 2024, "net_income_cr": 120.0, "operating_cash_flow_cr": 80.0},
        {"fiscal_year": 2022, "net_income_cr": 100.0, "operating_cash_flow_cr": 110.0},
        {"fiscal_year": 2021, "net_income_cr": 80.0, "operating_cash_flow_cr": 140.0},
    ]
    assert evaluate_red_flags(p_gap)["flag_profit_up_cfo_down_2yr"] is False


def test_flag_low_cash_conversion() -> None:
    """Test low cash conversion rule (< 0.50 for 2 years when net income > 0)."""
    p_trigger = [
        {"fiscal_year": 2024, "net_income_cr": 100.0, "cfo_to_net_profit": 0.35},
        {"fiscal_year": 2023, "net_income_cr": 80.0, "cfo_to_net_profit": 0.45},
    ]
    assert evaluate_red_flags(p_trigger)["flag_low_cash_conversion"] is True

    # Non-trigger: negative profit in one year must not trigger a false positive
    p_neg_profit = [
        {"fiscal_year": 2024, "net_income_cr": -20.0, "cfo_to_net_profit": -0.50},
        {"fiscal_year": 2023, "net_income_cr": 80.0, "cfo_to_net_profit": 0.45},
    ]
    assert evaluate_red_flags(p_neg_profit)["flag_low_cash_conversion"] is False


def test_flag_receivables_ballooning() -> None:
    """Test receivable days jumping > 20 days while revenue growth is modest (< 10%)."""
    p_trigger = [
        {"fiscal_year": 2024, "receivable_days": 65.0, "revenue_cr": 1050.0},
        {"fiscal_year": 2023, "receivable_days": 40.0, "revenue_cr": 1000.0},
    ]
    assert evaluate_red_flags(p_trigger)["flag_receivables_ballooning"] is True

    # Non-trigger: high revenue growth (20% >= 10%)
    p_high_growth = [
        {"fiscal_year": 2024, "receivable_days": 65.0, "revenue_cr": 1200.0},
        {"fiscal_year": 2023, "receivable_days": 40.0, "revenue_cr": 1000.0},
    ]
    assert evaluate_red_flags(p_high_growth)["flag_receivables_ballooning"] is False

    # Non-trigger: days increase <= 20
    p_small_jump = [
        {"fiscal_year": 2024, "receivable_days": 55.0, "revenue_cr": 1050.0},
        {"fiscal_year": 2023, "receivable_days": 40.0, "revenue_cr": 1000.0},
    ]
    assert evaluate_red_flags(p_small_jump)["flag_receivables_ballooning"] is False


def test_flag_debt_spike() -> None:
    """Test debt spike rule on ratio jumps (> 0.50) and equity turning negative with debt."""
    p_ratio_jump = [
        {"fiscal_year": 2024, "debt_to_equity": 1.20, "shareholders_equity_cr": 100.0},
        {"fiscal_year": 2023, "debt_to_equity": 0.50, "shareholders_equity_cr": 150.0},
    ]
    assert evaluate_red_flags(p_ratio_jump)["flag_debt_spike"] is True

    # Equity turned negative
    p_neg_equity = [
        {
            "fiscal_year": 2024,
            "debt_to_equity": None,
            "shareholders_equity_cr": -10.0,
            "total_debt_cr": 50.0,
        },
        {
            "fiscal_year": 2023,
            "debt_to_equity": 0.40,
            "shareholders_equity_cr": 80.0,
            "total_debt_cr": 32.0,
        },
    ]
    assert evaluate_red_flags(p_neg_equity)["flag_debt_spike"] is True

    # Modest increase (0.20 jump <= 0.50)
    p_modest = [
        {"fiscal_year": 2024, "debt_to_equity": 0.60, "shareholders_equity_cr": 100.0},
        {"fiscal_year": 2023, "debt_to_equity": 0.40, "shareholders_equity_cr": 120.0},
    ]
    assert evaluate_red_flags(p_modest)["flag_debt_spike"] is False


def test_flag_weak_interest_coverage() -> None:
    """Test interest coverage rule triggers when coverage is below 2.0."""
    res_1 = evaluate_red_flags([{"fiscal_year": 2024, "interest_coverage": 1.50}])
    assert res_1["flag_weak_interest_coverage"] is True

    res_2 = evaluate_red_flags([{"fiscal_year": 2024, "interest_coverage": 2.50}])
    assert res_2["flag_weak_interest_coverage"] is False

    res_3 = evaluate_red_flags([{"fiscal_year": 2024, "interest_coverage": None}])
    assert res_3["flag_weak_interest_coverage"] is False


def test_flag_persistent_negative_fcf() -> None:
    """Test 3 consecutive years of negative FCF combined with revenue growth threshold."""
    p_trigger = [
        {"fiscal_year": 2024, "fcf_cr": -30.0},
        {"fiscal_year": 2023, "fcf_cr": -20.0},
        {"fiscal_year": 2022, "fcf_cr": -10.0},
    ]
    assert evaluate_red_flags(p_trigger, revenue_cagr_3yr=0.08)["flag_persistent_negative_fcf"]
    assert evaluate_red_flags(p_trigger, revenue_cagr_3yr=None)["flag_persistent_negative_fcf"]

    # Non-trigger: rapid revenue growth (CAGR 20% >= 15%)
    res_high = evaluate_red_flags(p_trigger, revenue_cagr_3yr=0.20)
    assert res_high["flag_persistent_negative_fcf"] is False

    # Non-trigger: positive FCF in one year
    p_pos = [
        {"fiscal_year": 2024, "fcf_cr": -30.0},
        {"fiscal_year": 2023, "fcf_cr": 5.0},
        {"fiscal_year": 2022, "fcf_cr": -10.0},
    ]
    assert evaluate_red_flags(p_pos, revenue_cagr_3yr=0.08)["flag_persistent_negative_fcf"] is False


def test_flag_margin_erosion() -> None:
    """Test 2 consecutive years of margin decline with cumulative drop >= 2%."""
    # 18% -> 15% -> 12% (cumulative drop 6% >= 2%)
    p_trigger = [
        {"fiscal_year": 2024, "operating_margin": 0.12},
        {"fiscal_year": 2023, "operating_margin": 0.15},
        {"fiscal_year": 2022, "operating_margin": 0.18},
    ]
    assert evaluate_red_flags(p_trigger)["flag_margin_erosion"] is True

    # Drop is only 1% (< 2%)
    p_small_drop = [
        {"fiscal_year": 2024, "operating_margin": 0.14},
        {"fiscal_year": 2023, "operating_margin": 0.145},
        {"fiscal_year": 2022, "operating_margin": 0.15},
    ]
    assert evaluate_red_flags(p_small_drop)["flag_margin_erosion"] is False

    # Margin rebounded in latest year
    p_rebound = [
        {"fiscal_year": 2024, "operating_margin": 0.16},
        {"fiscal_year": 2023, "operating_margin": 0.13},
        {"fiscal_year": 2022, "operating_margin": 0.18},
    ]
    assert evaluate_red_flags(p_rebound)["flag_margin_erosion"] is False


def test_flag_high_other_income() -> None:
    """Test other income making up > 30% of total operating and non-operating income."""
    # Other income = 40, EBIT = 60 -> Total = 100, Ratio = 40% > 30%
    p_trigger = [
        {"fiscal_year": 2024, "net_income_cr": 50.0, "other_income_cr": 40.0, "ebit_cr": 60.0}
    ]
    assert evaluate_red_flags(p_trigger)["flag_high_other_income"] is True

    # Other income = 15, EBIT = 85 -> Total = 100, Ratio = 15% <= 30%
    p_low = [
        {"fiscal_year": 2024, "net_income_cr": 50.0, "other_income_cr": 15.0, "ebit_cr": 85.0}
    ]
    assert evaluate_red_flags(p_low)["flag_high_other_income"] is False

    # Net income <= 0 (loss-making company, not propped up by other income)
    p_loss = [
        {"fiscal_year": 2024, "net_income_cr": -10.0, "other_income_cr": 40.0, "ebit_cr": 60.0}
    ]
    assert evaluate_red_flags(p_loss)["flag_high_other_income"] is False
