create or replace view v_financials_wide as
select
    f.company_id,
    f.fiscal_year,
    max(case when f.line_item = 'revenue' then f.value_inr end) / 10000000.0 as revenue_cr,
    max(case when f.line_item = 'cost_of_revenue' then f.value_inr end) / 10000000.0 as cost_of_revenue_cr,
    max(case when f.line_item = 'gross_profit' then f.value_inr end) / 10000000.0 as gross_profit_cr,
    max(case when f.line_item = 'ebit' then f.value_inr end) / 10000000.0 as ebit_cr,
    max(case when f.line_item = 'ebitda' then f.value_inr end) / 10000000.0 as ebitda_cr,
    max(case when f.line_item = 'interest_expense' then f.value_inr end) / 10000000.0 as interest_expense_cr,
    max(case when f.line_item = 'other_income' then f.value_inr end) / 10000000.0 as other_income_cr,
    max(case when f.line_item = 'net_income' then f.value_inr end) / 10000000.0 as net_income_cr,
    max(case when f.line_item = 'depreciation_amortization' then f.value_inr end) / 10000000.0 as depreciation_amortization_cr,
    max(case when f.line_item = 'total_assets' then f.value_inr end) / 10000000.0 as total_assets_cr,
    max(case when f.line_item = 'current_assets' then f.value_inr end) / 10000000.0 as current_assets_cr,
    max(case when f.line_item = 'cash_and_equivalents' then f.value_inr end) / 10000000.0 as cash_and_equivalents_cr,
    max(case when f.line_item = 'receivables' then f.value_inr end) / 10000000.0 as receivables_cr,
    max(case when f.line_item = 'inventory' then f.value_inr end) / 10000000.0 as inventory_cr,
    max(case when f.line_item = 'total_liabilities' then f.value_inr end) / 10000000.0 as total_liabilities_cr,
    max(case when f.line_item = 'current_liabilities' then f.value_inr end) / 10000000.0 as current_liabilities_cr,
    max(case when f.line_item = 'total_debt' then f.value_inr end) / 10000000.0 as total_debt_cr,
    max(case when f.line_item = 'shareholders_equity' then f.value_inr end) / 10000000.0 as shareholders_equity_cr,
    max(case when f.line_item = 'retained_earnings' then f.value_inr end) / 10000000.0 as retained_earnings_cr,
    max(case when f.line_item = 'operating_cash_flow' then f.value_inr end) / 10000000.0 as operating_cash_flow_cr,
    max(case when f.line_item = 'capital_expenditure' then f.value_inr end) / 10000000.0 as capital_expenditure_cr,
    max(case when f.line_item = 'free_cash_flow' then f.value_inr end) / 10000000.0 as free_cash_flow_cr,
    max(case when f.line_item = 'basic_eps' then f.value_inr end) as basic_eps,
    max(case when f.line_item = 'diluted_eps' then f.value_inr end) as diluted_eps,
    max(case when f.line_item = 'ordinary_shares_number' then f.value_inr end) as ordinary_shares_number
from fact_financials f
join dim_company c on f.company_id = c.company_id
where c.is_financial = false
group by f.company_id, f.fiscal_year;

create or replace view metrics_annual as
with lagged as (
    select
        company_id,
        fiscal_year,
        revenue_cr,
        cost_of_revenue_cr,
        gross_profit_cr,
        ebit_cr,
        ebitda_cr,
        interest_expense_cr,
        net_income_cr,
        total_assets_cr,
        current_assets_cr,
        cash_and_equivalents_cr,
        receivables_cr,
        inventory_cr,
        current_liabilities_cr,
        total_debt_cr,
        shareholders_equity_cr,
        operating_cash_flow_cr,
        capital_expenditure_cr,
        coalesce(diluted_eps, basic_eps) as eps_native,
        ordinary_shares_number,
        lag(fiscal_year) over (partition by company_id order by fiscal_year) as prior_fy,
        lag(revenue_cr) over (partition by company_id order by fiscal_year) as prior_revenue_cr,
        lag(gross_profit_cr) over (partition by company_id order by fiscal_year) as prior_gross_profit_cr,
        lag(net_income_cr) over (partition by company_id order by fiscal_year) as prior_net_income_cr,
        lag(total_assets_cr) over (partition by company_id order by fiscal_year) as prior_total_assets_cr,
        lag(current_assets_cr) over (partition by company_id order by fiscal_year) as prior_current_assets_cr,
        lag(current_liabilities_cr) over (partition by company_id order by fiscal_year) as prior_current_liabilities_cr,
        lag(total_debt_cr) over (partition by company_id order by fiscal_year) as prior_total_debt_cr,
        lag(shareholders_equity_cr) over (partition by company_id order by fiscal_year) as prior_equity_cr,
        lag(coalesce(diluted_eps, basic_eps)) over (partition by company_id order by fiscal_year) as prior_eps,
        lag(ordinary_shares_number) over (partition by company_id order by fiscal_year) as prior_shares
    from v_financials_wide
),
balances as (
    select
        company_id,
        fiscal_year,
        revenue_cr,
        cost_of_revenue_cr,
        gross_profit_cr,
        ebit_cr,
        ebitda_cr,
        interest_expense_cr,
        net_income_cr,
        total_assets_cr,
        current_assets_cr,
        cash_and_equivalents_cr,
        receivables_cr,
        inventory_cr,
        current_liabilities_cr,
        total_debt_cr,
        shareholders_equity_cr,
        operating_cash_flow_cr,
        capital_expenditure_cr,
        operating_cash_flow_cr - abs(coalesce(capital_expenditure_cr, 0.0)) as fcf_cr,
        eps_native,
        prior_fy,
        prior_eps,
        -- average balance calculations
        case
            when prior_fy = fiscal_year - 1 and prior_equity_cr is not null
            then (shareholders_equity_cr + prior_equity_cr) / 2.0
            else shareholders_equity_cr
        end as avg_equity_cr,
        case
            when prior_fy = fiscal_year - 1 and prior_total_assets_cr is not null
            then (total_assets_cr + prior_total_assets_cr) / 2.0
            else total_assets_cr
        end as avg_total_assets_cr,
        case
            when prior_fy = fiscal_year - 1
                 and (prior_total_assets_cr - prior_current_liabilities_cr) is not null
            then (
                (total_assets_cr - current_liabilities_cr)
                + (prior_total_assets_cr - prior_current_liabilities_cr)
            ) / 2.0
            else (total_assets_cr - current_liabilities_cr)
        end as avg_capital_employed_cr,
        -- piotroski binary signals (1 if met, 0 otherwise)
        case when net_income_cr > 0 then 1 else 0 end as f1,
        case when operating_cash_flow_cr > 0 then 1 else 0 end as f2,
        case
            when total_assets_cr > 0 and prior_total_assets_cr > 0
                 and (net_income_cr / total_assets_cr) > (prior_net_income_cr / prior_total_assets_cr)
            then 1 else 0
        end as f3,
        case when operating_cash_flow_cr > net_income_cr then 1 else 0 end as f4,
        case
            when total_assets_cr > 0 and prior_total_assets_cr > 0
                 and (coalesce(total_debt_cr, 0.0) / total_assets_cr) < (coalesce(prior_total_debt_cr, 0.0) / prior_total_assets_cr)
            then 1 else 0
        end as f5,
        case
            when current_liabilities_cr > 0 and prior_current_liabilities_cr > 0
                 and (current_assets_cr / current_liabilities_cr) > (prior_current_assets_cr / prior_current_liabilities_cr)
            then 1 else 0
        end as f6,
        case
            when prior_shares is not null and ordinary_shares_number <= prior_shares
            then 1 else 0
        end as f7,
        case
            when revenue_cr > 0 and prior_revenue_cr > 0
                 and (gross_profit_cr / revenue_cr) > (prior_gross_profit_cr / prior_revenue_cr)
            then 1 else 0
        end as f8,
        case
            when total_assets_cr > 0 and prior_total_assets_cr > 0
                 and (revenue_cr / total_assets_cr) > (prior_revenue_cr / prior_total_assets_cr)
            then 1 else 0
        end as f9
    from lagged
)
select
    company_id,
    fiscal_year,
    -- profitability
    case
        when avg_equity_cr > 0 then net_income_cr / avg_equity_cr
        else null
    end as roe,
    case
        when avg_capital_employed_cr > 0 then ebit_cr / avg_capital_employed_cr
        else null
    end as roce,
    case
        when revenue_cr > 0 then ebit_cr / revenue_cr
        else null
    end as operating_margin,
    case
        when revenue_cr > 0 then net_income_cr / revenue_cr
        else null
    end as net_margin,
    -- leverage
    case
        when shareholders_equity_cr > 0 then total_debt_cr / shareholders_equity_cr
        else null
    end as debt_to_equity,
    case
        when abs(interest_expense_cr) > 0 then ebit_cr / abs(interest_expense_cr)
        else null
    end as interest_coverage,
    case
        when ebitda_cr > 0
        then (coalesce(total_debt_cr, 0.0) - coalesce(cash_and_equivalents_cr, 0.0)) / ebitda_cr
        else null
    end as net_debt_to_ebitda,
    -- cash quality
    case
        when net_income_cr > 0 then operating_cash_flow_cr / net_income_cr
        else null
    end as cfo_to_net_profit,
    fcf_cr,
    case
        when revenue_cr > 0 then fcf_cr / revenue_cr
        else null
    end as fcf_margin,
    -- efficiency
    case
        when revenue_cr > 0 then (receivables_cr / revenue_cr) * 365.0
        else null
    end as receivable_days,
    case
        when cost_of_revenue_cr > 0 then (inventory_cr / cost_of_revenue_cr) * 365.0
        else null
    end as inventory_days,
    case
        when avg_total_assets_cr > 0 then revenue_cr / avg_total_assets_cr
        else null
    end as asset_turnover,
    -- growth
    case
        when prior_fy = fiscal_year - 1 and prior_eps > 0
        then (eps_native - prior_eps) / prior_eps
        else null
    end as eps_growth_yoy,
    -- composite quality (piotroski f-score requires prior fiscal year)
    case
        when prior_fy = fiscal_year - 1
        then (f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9)
        else null
    end as piotroski_f_score
from balances;

create or replace view metrics_latest as
with ranked_annual as (
    select
        company_id,
        fiscal_year,
        roe,
        roce,
        operating_margin,
        net_margin,
        debt_to_equity,
        interest_coverage,
        net_debt_to_ebitda,
        cfo_to_net_profit,
        fcf_cr,
        fcf_margin,
        receivable_days,
        inventory_days,
        asset_turnover,
        eps_growth_yoy,
        piotroski_f_score,
        row_number() over (partition by company_id order by fiscal_year desc) as rn
    from metrics_annual
),
recent_periods as (
    select
        company_id,
        fiscal_year,
        roe,
        roce,
        operating_margin
    from ranked_annual
    where rn <= 4
),
summary_3yr as (
    select
        company_id,
        count(*) as years_available,
        min(fiscal_year) as earliest_fy,
        max(fiscal_year) as latest_fy,
        avg(roce) as avg_roce_3yr,
        avg(roe) as avg_roe_3yr,
        stddev_samp(operating_margin) as operating_margin_std_3yr
    from recent_periods
    group by company_id
),
earliest_financials as (
    select
        s.company_id,
        w.revenue_cr as base_revenue_cr,
        w.net_income_cr as base_net_income_cr
    from summary_3yr s
    join v_financials_wide w
        on s.company_id = w.company_id and s.earliest_fy = w.fiscal_year
),
latest_financials as (
    select
        r.company_id,
        r.fiscal_year,
        r.roe,
        r.roce,
        r.operating_margin,
        r.net_margin,
        r.debt_to_equity,
        r.interest_coverage,
        r.net_debt_to_ebitda,
        r.cfo_to_net_profit,
        r.fcf_cr,
        r.fcf_margin,
        r.receivable_days,
        r.inventory_days,
        r.asset_turnover,
        r.eps_growth_yoy,
        r.piotroski_f_score,
        w.revenue_cr as latest_revenue_cr,
        w.net_income_cr as latest_net_income_cr,
        w.shareholders_equity_cr as latest_equity_cr,
        w.ebitda_cr as latest_ebitda_cr,
        w.total_debt_cr as latest_total_debt_cr,
        w.cash_and_equivalents_cr as latest_cash_cr
    from ranked_annual r
    join v_financials_wide w
        on r.company_id = w.company_id and r.fiscal_year = w.fiscal_year
    where r.rn = 1
),
ranked_market as (
    select
        company_id,
        market_cap / 10000000.0 as market_cap_cr,
        row_number() over (partition by company_id order by snapshot_date desc) as rn
    from fact_market
),
latest_market as (
    select
        company_id,
        market_cap_cr
    from ranked_market
    where rn = 1
),
ranked_prices as (
    select
        company_id,
        adj_close,
        row_number() over (partition by company_id order by month_end desc) as rn
    from fact_prices_monthly
),
latest_price as (
    select
        company_id,
        adj_close
    from ranked_prices
    where rn = 1
)
select
    c.company_id,
    c.symbol,
    c.name,
    c.sector,
    lf.fiscal_year as latest_fiscal_year,
    -- annual ratios
    lf.roe,
    lf.roce,
    lf.operating_margin,
    lf.net_margin,
    lf.debt_to_equity,
    lf.interest_coverage,
    lf.net_debt_to_ebitda,
    lf.cfo_to_net_profit,
    lf.fcf_cr,
    lf.fcf_margin,
    lf.receivable_days,
    lf.inventory_days,
    lf.asset_turnover,
    lf.eps_growth_yoy,
    lf.piotroski_f_score,
    -- 3-year growth and stability
    s3.years_available,
    s3.avg_roce_3yr,
    s3.avg_roe_3yr,
    s3.operating_margin_std_3yr,
    case
        when (s3.latest_fy - s3.earliest_fy) >= 3
             and ef.base_revenue_cr > 0
             and lf.latest_revenue_cr > 0
        then power(lf.latest_revenue_cr / ef.base_revenue_cr, 1.0 / (s3.latest_fy - s3.earliest_fy)) - 1.0
        else null
    end as revenue_cagr_3yr,
    case
        when (s3.latest_fy - s3.earliest_fy) >= 3
             and ef.base_net_income_cr > 0
             and lf.latest_net_income_cr > 0
        then power(lf.latest_net_income_cr / ef.base_net_income_cr, 1.0 / (s3.latest_fy - s3.earliest_fy)) - 1.0
        else null
    end as profit_cagr_3yr,
    -- market data
    lm.market_cap_cr,
    lp.adj_close as latest_close_price,
    -- valuation ratios (using company-level aggregate totals)
    case
        when lf.latest_net_income_cr > 0 then lm.market_cap_cr / lf.latest_net_income_cr
        else null
    end as pe_ratio,
    case
        when lf.latest_equity_cr > 0 then lm.market_cap_cr / lf.latest_equity_cr
        else null
    end as pb_ratio,
    case
        when lf.latest_ebitda_cr > 0
        then (
            lm.market_cap_cr
            + coalesce(lf.latest_total_debt_cr, 0.0)
            - coalesce(lf.latest_cash_cr, 0.0)
        ) / lf.latest_ebitda_cr
        else null
    end as ev_to_ebitda,
    case
        when lm.market_cap_cr > 0 then lf.latest_net_income_cr / lm.market_cap_cr
        else null
    end as earnings_yield,
    case
        when lm.market_cap_cr > 0 then lf.fcf_cr / lm.market_cap_cr
        else null
    end as fcf_yield
from dim_company c
left join latest_financials lf on c.company_id = lf.company_id
left join summary_3yr s3 on c.company_id = s3.company_id
left join earliest_financials ef on c.company_id = ef.company_id
left join latest_market lm on c.company_id = lm.company_id
left join latest_price lp on c.company_id = lp.company_id
where c.is_financial = false;
