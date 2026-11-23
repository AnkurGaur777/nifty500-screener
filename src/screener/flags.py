"""Red-flag evaluation engine, audit reporting, and case study generation."""

import logging
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.screener.config import (
    INTERIM_DIR,
    PROJECT_ROOT,
    configure_logging,
    get_database_url,
)

logger = logging.getLogger(__name__)

FLAGS_SQL_FILE = PROJECT_ROOT / "sql" / "03_flags_views.sql"

FLAG_RULES = [
    "flag_profit_up_cfo_down_2yr",
    "flag_low_cash_conversion",
    "flag_receivables_ballooning",
    "flag_debt_spike",
    "flag_weak_interest_coverage",
    "flag_persistent_negative_fcf",
    "flag_margin_erosion",
    "flag_high_other_income",
]


def apply_flag_views(engine: Engine, sql_file: Path = FLAGS_SQL_FILE) -> None:
    """Execute SQL DDL file to deploy red-flag database views."""
    with sql_file.open("r", encoding="utf-8") as f:
        sql_content = f.read()

    with engine.begin() as conn:
        conn.execute(text(sql_content))


def fetch_flags_detail(engine: Engine) -> pd.DataFrame:
    """Query company evaluations from red_flags_detail view."""
    query = text("select * from red_flags_detail order by red_flag_count desc, symbol")
    with engine.connect() as conn:
        return pd.read_sql_query(query, conn)


def build_flags_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Compute rule-by-rule flagged counts, percentages, and top affected sectors."""
    total_companies = len(df)
    summary_rows: list[dict[str, object]] = []

    for rule in FLAG_RULES:
        if rule not in df.columns:
            continue
        flagged_sub = df[df[rule] == True]  # noqa: E712
        count_val = len(flagged_sub)
        pct_val = round((count_val / total_companies) * 100, 1) if total_companies else 0.0

        if count_val > 0:
            top_counts = flagged_sub["sector"].value_counts().head(3)
            sector_parts = [f"{sec} ({cnt})" for sec, cnt in top_counts.items()]
            top_sectors_str = ", ".join(sector_parts)
        else:
            top_sectors_str = "None"

        summary_rows.append(
            {
                "rule_name": rule,
                "flagged_count": count_val,
                "flagged_pct": pct_val,
                "top_sectors": top_sectors_str,
            }
        )

    return pd.DataFrame(summary_rows)


def fetch_underlying_metrics(engine: Engine) -> pd.DataFrame:
    """Retrieve multi-period underlying financial metrics for example formatting."""
    query = text(
        """
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
        )
        select
            r0.company_id,
            r0.fiscal_year as fy_t,
            r0.revenue_cr as rev_t,
            r0.ebit_cr as ebit_t,
            r0.net_income_cr as pat_t,
            r0.operating_cash_flow_cr as cfo_t,
            r0.other_income_cr as oth_t,
            r0.total_debt_cr as debt_t,
            r0.shareholders_equity_cr as eq_t,
            r0.operating_margin as opm_t,
            r0.debt_to_equity as de_t,
            r0.interest_coverage as int_cov_t,
            r0.cfo_to_net_profit as cfo_pat_t,
            r0.fcf_cr as fcf_t,
            r0.receivable_days as rec_days_t,
            r1.fiscal_year as fy_t1,
            r1.revenue_cr as rev_t1,
            r1.net_income_cr as pat_t1,
            r1.operating_cash_flow_cr as cfo_t1,
            r1.shareholders_equity_cr as eq_t1,
            r1.operating_margin as opm_t1,
            r1.debt_to_equity as de_t1,
            r1.cfo_to_net_profit as cfo_pat_t1,
            r1.fcf_cr as fcf_t1,
            r1.receivable_days as rec_days_t1,
            r2.fiscal_year as fy_t2,
            r2.net_income_cr as pat_t2,
            r2.operating_cash_flow_cr as cfo_t2,
            r2.operating_margin as opm_t2,
            r2.fcf_cr as fcf_t2,
            ml.revenue_cagr_3yr
        from ranked r0
        left join ranked r1 on r0.company_id = r1.company_id and r1.rn = 2
        left join ranked r2 on r0.company_id = r2.company_id and r2.rn = 3
        left join metrics_latest ml on r0.company_id = ml.company_id
        where r0.rn = 1
        """
    )
    with engine.connect() as conn:
        return pd.read_sql_query(query, conn)


def build_flags_examples(flags_df: pd.DataFrame, metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Build up to 3 concrete case-study examples per triggered rule with underlying numbers."""
    merged = pd.merge(flags_df, metrics_df, on="company_id", how="left")
    examples: list[dict[str, object]] = []

    for rule in FLAG_RULES:
        if rule not in merged.columns:
            continue
        flagged = merged[merged[rule] == True].head(3)  # noqa: E712
        for _, row in flagged.iterrows():
            details = format_rule_details(rule, row)
            examples.append(
                {
                    "rule_name": rule,
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "sector": row["sector"],
                    "details": details,
                }
            )

    return pd.DataFrame(examples)


def format_rule_details(rule: str, row: pd.Series) -> str:
    """Format human-readable underlying financial numbers for a triggered red flag."""
    if rule == "flag_profit_up_cfo_down_2yr":
        p_t = (
            f"FY{row.get('fy_t')}: PAT={row.get('pat_t', 0.0):.1f}Cr, "
            f"CFO={row.get('cfo_t', 0.0):.1f}Cr"
        )
        p_t1 = (
            f"FY{row.get('fy_t1')}: PAT={row.get('pat_t1', 0.0):.1f}Cr, "
            f"CFO={row.get('cfo_t1', 0.0):.1f}Cr"
        )
        p_t2 = (
            f"FY{row.get('fy_t2')}: PAT={row.get('pat_t2', 0.0):.1f}Cr, "
            f"CFO={row.get('cfo_t2', 0.0):.1f}Cr"
        )
        return f"{p_t} | {p_t1} | {p_t2}"
    if rule == "flag_low_cash_conversion":
        return (
            f"CFO/PAT: FY{row.get('fy_t')}={row.get('cfo_pat_t', 0.0):.2f}, "
            f"FY{row.get('fy_t1')}={row.get('cfo_pat_t1', 0.0):.2f} (both < 0.50)"
        )
    if rule == "flag_receivables_ballooning":
        delta_d = (row.get("rec_days_t") or 0.0) - (row.get("rec_days_t1") or 0.0)
        rev_t1 = row.get("rev_t1") or 1.0
        growth = ((row.get("rev_t") or 0.0) - rev_t1) / rev_t1 if rev_t1 > 0 else 0.0
        return (
            f"Receivable days: +{delta_d:.1f}d (to {row.get('rec_days_t', 0.0):.1f}d), "
            f"YoY Rev growth: {growth:.1%}"
        )
    if rule == "flag_debt_spike":
        de_t = row.get("de_t")
        de_t1 = row.get("de_t1")
        if de_t is not None and de_t1 is not None:
            return f"D/E: {de_t:.2f} (jumped by +{de_t - de_t1:.2f} from {de_t1:.2f})"
        return (
            f"Equity turned negative ({row.get('eq_t', 0.0):.1f}Cr) "
            f"with total debt={row.get('debt_t', 0.0):.1f}Cr"
        )
    if rule == "flag_weak_interest_coverage":
        return f"Interest coverage: {row.get('int_cov_t', 0.0):.2f} (< 2.0)"
    if rule == "flag_persistent_negative_fcf":
        cagr = row.get("revenue_cagr_3yr")
        cagr_str = f"{cagr:.1%}" if pd.notna(cagr) else "N/A"
        return (
            f"FCF: FY{row.get('fy_t')}={row.get('fcf_t', 0.0):.1f}Cr, "
            f"FY{row.get('fy_t1')}={row.get('fcf_t1', 0.0):.1f}Cr, "
            f"FY{row.get('fy_t2')}={row.get('fcf_t2', 0.0):.1f}Cr | 3Y Rev CAGR: {cagr_str}"
        )
    if rule == "flag_margin_erosion":
        m_t = row.get("opm_t") or 0.0
        m_t1 = row.get("opm_t1") or 0.0
        m_t2 = row.get("opm_t2") or 0.0
        drop_val = m_t2 - m_t
        return f"Op margin: {m_t2:.1%} -> {m_t1:.1%} -> {m_t:.1%} (cumulative drop: {drop_val:.1%})"
    if rule == "flag_high_other_income":
        oth = row.get("oth_t") or 0.0
        ebit = row.get("ebit_t") or 0.0
        tot = ebit + oth
        pct = (oth / tot) if tot > 0 else 0.0
        return (
            f"Other income: {oth:.1f}Cr ({pct:.1%} of EBIT+Other Inc={tot:.1f}Cr, "
            f"PAT={row.get('pat_t', 0.0):.1f}Cr)"
        )
    return "Flag triggered"


def run_flags_pipeline() -> None:
    """Deploy red-flag views, compute audit summary, and save example case studies."""
    start_time = time.perf_counter()
    configure_logging()
    logger.info("Starting red-flags evaluation pipeline...")

    db_url = get_database_url()
    try:
        engine = create_engine(db_url)
        logger.info("Deploying red-flag views from %s...", FLAGS_SQL_FILE.name)
        apply_flag_views(engine)
        logger.info("View red_flags_detail deployed successfully.")

        flags_df = fetch_flags_detail(engine)
        logger.info("Retrieved %d non-financial companies from red_flags_detail.", len(flags_df))

        summary_df = build_flags_summary(flags_df)
        summary_path = INTERIM_DIR / "red_flags_summary.csv"
        summary_df.to_csv(summary_path, index=False)
        logger.info("Red-Flags Summary Table:\n%s", summary_df.to_string(index=False))

        metrics_df = fetch_underlying_metrics(engine)
        examples_df = build_flags_examples(flags_df, metrics_df)
        examples_path = INTERIM_DIR / "red_flags_examples.csv"
        examples_df.to_csv(examples_path, index=False)
        logger.info(
            "Extracted %d case study examples across triggered rules.",
            len(examples_df),
        )
    except SQLAlchemyError as exc:
        logger.warning("Database unavailable or red-flag deployment failed: %s", exc)

    elapsed = time.perf_counter() - start_time
    logger.info("Red-flags pipeline completed in %.2f seconds.", elapsed)


if __name__ == "__main__":
    run_flags_pipeline()
