create or replace view red_flags_detail as
with ranked as (
    select
        m.company_id,
        m.fiscal_year,
        w.revenue_cr,
        w.ebit_cr,
        w.net_income_cr,
        w.operating_cash_flow_cr,
        w.other_income_cr,
        w.total_debt_cr,
        w.shareholders_equity_cr,
        m.operating_margin,
        m.debt_to_equity,
        m.interest_coverage,
        m.cfo_to_net_profit,
        m.fcf_cr,
        m.receivable_days,
        row_number() over (partition by m.company_id order by m.fiscal_year desc) as rn
    from metrics_annual m
    join v_financials_wide w
        on m.company_id = w.company_id and m.fiscal_year = w.fiscal_year
),
period_t as (
    select
        company_id,
        fiscal_year,
        revenue_cr,
        ebit_cr,
        net_income_cr,
        operating_cash_flow_cr,
        other_income_cr,
        total_debt_cr,
        shareholders_equity_cr,
        operating_margin,
        debt_to_equity,
        interest_coverage,
        cfo_to_net_profit,
        fcf_cr,
        receivable_days
    from ranked
    where rn = 1
),
period_t1 as (
    select
        company_id,
        fiscal_year,
        revenue_cr,
        ebit_cr,
        net_income_cr,
        operating_cash_flow_cr,
        shareholders_equity_cr,
        operating_margin,
        debt_to_equity,
        cfo_to_net_profit,
        fcf_cr,
        receivable_days
    from ranked
    where rn = 2
),
period_t2 as (
    select
        company_id,
        fiscal_year,
        net_income_cr,
        operating_cash_flow_cr,
        operating_margin,
        fcf_cr
    from ranked
    where rn = 3
),
evaluated_flags as (
    select
        c.company_id,
        c.symbol,
        c.name,
        c.sector,
        t.fiscal_year as latest_fiscal_year,
        -- rule 1: net income up but cfo down for 2 consecutive years
        case
            when t1.fiscal_year = t.fiscal_year - 1
                 and t2.fiscal_year = t.fiscal_year - 2
                 and t.net_income_cr is not null and t1.net_income_cr is not null and t2.net_income_cr is not null
                 and t.operating_cash_flow_cr is not null and t1.operating_cash_flow_cr is not null
                 and t2.operating_cash_flow_cr is not null
                 and t.net_income_cr > t1.net_income_cr
                 and t1.net_income_cr > t2.net_income_cr
                 and t.operating_cash_flow_cr < t1.operating_cash_flow_cr
                 and t1.operating_cash_flow_cr < t2.operating_cash_flow_cr
            then true
            else false
        end as flag_profit_up_cfo_down_2yr,
        -- rule 2: cfo / net profit < 0.5 for 2 consecutive years (when net income > 0)
        case
            when t1.fiscal_year = t.fiscal_year - 1
                 and t.net_income_cr > 0
                 and t1.net_income_cr > 0
                 and t.cfo_to_net_profit is not null
                 and t1.cfo_to_net_profit is not null
                 and t.cfo_to_net_profit < 0.5
                 and t1.cfo_to_net_profit < 0.5
            then true
            else false
        end as flag_low_cash_conversion,
        -- rule 3: receivable days jumped > 20 days with modest revenue growth (< 10%)
        case
            when t1.fiscal_year = t.fiscal_year - 1
                 and t.receivable_days is not null
                 and t1.receivable_days is not null
                 and t.revenue_cr is not null
                 and t1.revenue_cr is not null
                 and t1.revenue_cr > 0
                 and (t.receivable_days - t1.receivable_days) > 20.0
                 and ((t.revenue_cr - t1.revenue_cr) / t1.revenue_cr) < 0.10
            then true
            else false
        end as flag_receivables_ballooning,
        -- rule 4: debt to equity jumped > 0.50 or equity flipped negative with debt > 0
        case
            when t1.fiscal_year = t.fiscal_year - 1
                 and (
                     (
                         t.debt_to_equity is not null
                         and t1.debt_to_equity is not null
                         and (t.debt_to_equity - t1.debt_to_equity) > 0.50
                     )
                     or (
                         t1.shareholders_equity_cr > 0
                         and t.shareholders_equity_cr <= 0
                         and coalesce(t.total_debt_cr, 0.0) > 0
                     )
                 )
            then true
            else false
        end as flag_debt_spike,
        -- rule 5: interest coverage < 2.0 in the latest year
        case
            when t.interest_coverage is not null
                 and t.interest_coverage < 2.0
            then true
            else false
        end as flag_weak_interest_coverage,
        -- rule 6: fcf < 0 for 3 consecutive years while 3-year revenue cagr < 15% (or null)
        case
            when t1.fiscal_year = t.fiscal_year - 1
                 and t2.fiscal_year = t.fiscal_year - 2
                 and t.fcf_cr is not null and t.fcf_cr < 0
                 and t1.fcf_cr is not null and t1.fcf_cr < 0
                 and t2.fcf_cr is not null and t2.fcf_cr < 0
                 and (ml.revenue_cagr_3yr is null or ml.revenue_cagr_3yr < 0.15)
            then true
            else false
        end as flag_persistent_negative_fcf,
        -- rule 7: operating margin dropped 2 consecutive years with cumulative drop >= 2%
        case
            when t1.fiscal_year = t.fiscal_year - 1
                 and t2.fiscal_year = t.fiscal_year - 2
                 and t.operating_margin is not null
                 and t1.operating_margin is not null
                 and t2.operating_margin is not null
                 and t.operating_margin < t1.operating_margin
                 and t1.operating_margin < t2.operating_margin
                 and (t2.operating_margin - t.operating_margin) >= 0.02
            then true
            else false
        end as flag_margin_erosion,
        -- rule 8: other income > 30% of total operating+other earnings (when pat > 0)
        case
            when t.net_income_cr is not null
                 and t.net_income_cr > 0
                 and t.other_income_cr is not null
                 and (coalesce(t.ebit_cr, 0.0) + t.other_income_cr) > 0
                 and t.other_income_cr > 0.30 * (coalesce(t.ebit_cr, 0.0) + t.other_income_cr)
            then true
            else false
        end as flag_high_other_income
    from dim_company c
    left join period_t t on c.company_id = t.company_id
    left join period_t1 t1 on c.company_id = t1.company_id
    left join period_t2 t2 on c.company_id = t2.company_id
    left join metrics_latest ml on c.company_id = ml.company_id
    where c.is_financial = false
)
select
    company_id,
    symbol,
    name,
    sector,
    latest_fiscal_year,
    flag_profit_up_cfo_down_2yr,
    flag_low_cash_conversion,
    flag_receivables_ballooning,
    flag_debt_spike,
    flag_weak_interest_coverage,
    flag_persistent_negative_fcf,
    flag_margin_erosion,
    flag_high_other_income,
    (
        case when flag_profit_up_cfo_down_2yr then 1 else 0 end +
        case when flag_low_cash_conversion then 1 else 0 end +
        case when flag_receivables_ballooning then 1 else 0 end +
        case when flag_debt_spike then 1 else 0 end +
        case when flag_weak_interest_coverage then 1 else 0 end +
        case when flag_persistent_negative_fcf then 1 else 0 end +
        case when flag_margin_erosion then 1 else 0 end +
        case when flag_high_other_income then 1 else 0 end
    ) as red_flag_count,
    nullif(
        concat_ws(
            ', ',
            case when flag_profit_up_cfo_down_2yr then 'profit_up_cfo_down_2yr' end,
            case when flag_low_cash_conversion then 'low_cash_conversion' end,
            case when flag_receivables_ballooning then 'receivables_ballooning' end,
            case when flag_debt_spike then 'debt_spike' end,
            case when flag_weak_interest_coverage then 'weak_interest_coverage' end,
            case when flag_persistent_negative_fcf then 'persistent_negative_fcf' end,
            case when flag_margin_erosion then 'margin_erosion' end,
            case when flag_high_other_income then 'high_other_income' end
        ),
        ''
    ) as red_flag_list
from evaluated_flags;
