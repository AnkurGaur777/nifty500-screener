-- dimensions
create table if not exists dim_company (
    company_id serial primary key,
    symbol text unique not null,
    name text not null,
    isin text,
    sector text,
    industry text,
    is_financial boolean not null default false,
    snapshot_date date
);

create table if not exists dim_period (
    fiscal_year integer primary key,
    period_end_date date not null
);

-- facts
create table if not exists fact_financials (
    company_id integer references dim_company(company_id),
    fiscal_year integer references dim_period(fiscal_year),
    line_item text not null,
    value_inr numeric,
    source text,
    downloaded_at timestamp not null default now(),
    unique (company_id, fiscal_year, line_item)
);

create table if not exists fact_prices_monthly (
    company_id integer references dim_company(company_id),
    month_end date not null,
    adj_close numeric not null,
    unique (company_id, month_end)
);

create table if not exists fact_market (
    company_id integer references dim_company(company_id),
    snapshot_date date not null,
    market_cap numeric,
    shares_outstanding numeric,
    unique (company_id, snapshot_date)
);

-- logging
create table if not exists load_log (
    log_id serial primary key,
    symbol text not null,
    status text not null,
    error_message text,
    logged_at timestamp not null default now()
);

-- indexes
create index if not exists idx_fact_financials_company_id on fact_financials(company_id);
create index if not exists idx_fact_financials_fiscal_year on fact_financials(fiscal_year);
create index if not exists idx_fact_prices_monthly_company_id on fact_prices_monthly(company_id);
create index if not exists idx_fact_market_company_id on fact_market(company_id);
