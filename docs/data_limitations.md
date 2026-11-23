# Nifty 500 Fundamental Screener: Data Limitations & Empirical Caveats

> **Regulatory & Compliance Disclaimer**  
> This documentation is prepared strictly for educational, academic, and software development
> purposes. The analyses, limitations, and empirical findings presented herein do not constitute
> financial, legal, tax, or investment advice, nor an endorsement or recommendation of any security
> under SEBI (Research Analysts) Regulations, 2014, or any jurisdiction.

---

## 1. Overview

While the Nifty 500 Fundamental Screener enforces strict data cleaning, look-ahead prevention,
and forensic verification rules, all quantitative screening architectures inherit constraints
from their underlying data sources, reporting frequency, and historical horizons.

This document transparently details every real, measured empirical constraint identified during
the development and execution of this pipeline.

---

## 2. Statement History Depth Constraints

### 2.1 Four-Year Annual Limit in Yahoo Finance
- **Observed Limitation**: Public Yahoo Finance endpoints (`yfinance` API) return a maximum of
  4 historical annual periods for Indian corporate entities (typically FY2022, FY2023, FY2024,
  and FY2025/FY2026).
- **Impact on Growth Metrics**: Classical quantitative literature prefers 5-year or 7-year
  compound annual growth rates (CAGR) to capture a full economic cycle. Due to the 4-year ceiling,
  the screener calculates 3-year CAGR (`revenue_cagr_3yr`, `profit_cagr_3yr`), requiring a minimum
  of 3 consecutive annual filings ($t$, $t-1$, $t-2$).
- **Impact on Historical Backtesting**: A 4-year statement depth restricts forward backtesting to
  3 full 12-month cohorts:
  - **FY2023 Cohort**: Formed June 30, 2023 $\rightarrow$ Evaluated June 30, 2024.
  - **FY2024 Cohort**: Formed June 30, 2024 $\rightarrow$ Evaluated June 30, 2025.
  - **FY2025 Cohort**: Formed June 30, 2025 $\rightarrow$ Evaluated June 30, 2026.
  A complete multi-cycle statistical validation would require 15 to 20 years of point-in-time
  filings spanning major macroeconomic cycles (e.g., 2008 GFC, 2013 taper tantrum, 2020 crash).

---

## 3. Ticker Universe & Corporate Action Coverage

### 3.1 Universe Composition & Exclusions
- **Raw Index Constituents**: 501 instruments on the official NSE Nifty 500 index listing.
- **REIT Exclusion**: 3 Real Estate Investment Trusts (`RR` series: `BAGMANE`, `BIRET`, `EMBASSY`)
  were removed because distribution yields, Net Asset Value (NAV) accounting, and debt
  structures cannot be compared to industrial/operating equities.
- **Financial Services Exclusion**: 98 Financial Services entities (commercial banks, housing
  finance companies, NBFCs, and insurance firms) were excluded because concepts like Operating
  Margin, Working Capital, Debt/Equity, and ROCE are fundamentally inapplicable to deposit-takers.
- **Non-Financial Operating Universe**: 400 companies.

### 3.2 Ticker Resolution Rate
- **Successful Fetches**: 399 out of 400 companies were downloaded, cleaned, and ingested (99.75%
  coverage).
- **Missing Ticker**: Exactly 1 symbol could not be fetched: `DUMMYHEG.NS`.
  - **Root Cause**: `DUMMYHEG` is a temporary synthetic placeholder used by the National Stock
    Exchange to represent the demerger and corporate restructuring of HEG Ltd.'s graphite and
    insulation businesses. It does not represent an actively trading operational company with
    independent financial filings on Yahoo Finance.

---

## 4. Consolidated Balance Sheet Identity Discrepancies

### 4.1 Measured Variance
During the Stage 4 Data Quality audit, the pipeline verified the core accounting balance identity:
$$\text{Total Assets} \stackrel{?}{=} \text{Total Liabilities} + \text{Shareholders Equity}$$
Across the 400-company universe over 4 fiscal years, **273 company-years exhibited an identity
variance greater than 2.0%**.

### 4.2 Forensic Root Cause
Detailed manual reconciliation against official annual reports revealed that this variance is
**not a data corruption error**, but a structural reporting convention in Yahoo Finance:
1. **Minority Interest / Non-Controlling Interest (NCI)**: Under Indian Accounting Standards
   (Ind AS 110), consolidated balance sheets include Non-Controlling Interests as a distinct
   component of Total Equity.
2. **Aggregator Exclusion**: Yahoo Finance extracts `Stockholders Equity` representing only the
   equity attributable to parent owners, while omitting minority interest from both Total
   Liabilities and Stockholders Equity.
3. **Audit Consequence**: In conglomerates with substantial joint ventures and subsidiaries
   (e.g., Tata Motors, L&T, Adani Enterprises, Bharti Airtel), Total Assets naturally exceeds
   the sum of parent equity and debt by the exact amount of subsidiary minority interest.

---

## 5. Backtest Caveats & Market Dynamics

### 5.1 Survivorship Bias
The backtest engine evaluates the historical performance of companies that belong to the Nifty 500
**as of November 2026**. This introduces survivorship bias:
- Companies that went bankrupt, suffered severe distress, or were delisted during 2022–2025 are
  not in the dataset.
- The universe represents "winners" who successfully grew or maintained their market capitalization
  to qualify for the Nifty 500.

### 5.2 Execution & Frictionless Assumptions
The backtest reflects idealized paper returns:
- **Zero Slippage / Market Impact**: Trades are assumed to execute exactly at monthly adjusted close
  prices, regardless of market liquidity.
- **Zero Transaction Costs**: Securities Transaction Tax (STT), brokerage, exchange fees, and GST
  are omitted.

### 5.3 Quintile Spread Anomaly & Placebo Context
An important counter-intuitive empirical finding emerged during the backtest analysis:
- **Q1 Outperformance**: The Top Quality Quintile (`Q1`) beat the Nifty 500 benchmark in
  **3 out of 3 cohorts (100% hit rate)**, compounding at an annual average return of 28.4%.
- **Q5 High-Beta Rebound**: The Bottom Quintile (`Q5`) delivered an annual average return of 34.4%,
  resulting in an apparent negative $Q1 - Q5$ long-short spread of **-6.01%**.
- **Market Context**:
  During explosive mid-cap and small-cap bull markets (such as the 2023–2024 Indian market rally),
  deeply discounted, highly leveraged, and low-quality cyclicals in `Q5` experienced aggressive
  speculative rebounds and short-covering rallies.
- **Monte Carlo Placebo Comparison**:
  In the 500-trial placebo simulation (randomly shuffling cohort scores), the random $Q1 - Q5$
  spread had a 5th-to-95th percentile null distribution of **-11.55% to +11.56%** (median +0.12%).
  Because the observed spread (-6.01%) lies comfortably within the null range, it cannot be deemed
  statistically distinguishable from random cross-sectional dispersion during liquidity surges.

---

## 6. Absence of Intraday & Interim TTM Data

- **Annual Reporting Lag**: The screener relies exclusively on annual audited statements. It does
  not incorporate quarterly filings (unaudited Form 33) or Trailing Twelve Months (TTM) updates.
- **Material Events**: Mid-year management departures, unexpected quarterly write-downs, or merger
  announcements are not reflected until the subsequent March 31 reporting cycle.
