# Nifty 500 Fundamental Screener: Methodology & Architecture

> **Regulatory & Compliance Disclaimer**  
> This documentation, model architecture, and all accompanying screen outputs are developed
> strictly for academic, educational, and computational research purposes. Nothing herein
> constitutes investment advice, financial promotion, or a recommendation or solicitation
> to buy, hold, or sell securities under the Securities and Exchange Board of India (SEBI)
> (Research Analysts) Regulations, 2014, or any global financial regulations. Screen scores
> and ranks reflect automated quantitative factor rules applied to historical public filings.

---

## 1. Executive Summary & Philosophy

The Nifty 500 Fundamental Screener provides an institutional-grade, transparent, and
reproducible quantitative screening pipeline for Indian equities. Rather than relying on
black-box commercial screeners or unadjusted headline metrics, this system:
1. Standardizes raw multi-statement disclosures from Yahoo Finance via an explicit priority map.
2. Aligns Indian fiscal periods ending around March 31 with conservative look-ahead guards.
3. Segregates and excludes financial enterprises (banks, NBFCs, insurance, and housing finance).
4. Normalizes cross-sectional metrics within peer groups using winsorized percentile ranks.
5. Evaluates 8 forensic red-flag rules to flag accounting anomalies and cash flow divergences.
6. Conducts historical backtesting with a 3-month reporting lag and Monte Carlo placebo tests.

---

## 2. Universe Definition & Sector Peer Groups

### 2.1 Universe Filtering
- **Total Constituents (BSE/NSE Listing)**: 501 instruments from the official Nifty 500 index.
- **Instrument Series Filter**: Retains regular equity (`EQ`) and trade-to-trade equity (`BE`).
  Real Estate Investment Trusts (`RR` series: `BAGMANE`, `BIRET`, `EMBASSY`) are excluded.
- **Financial Services Exclusion**: 98 Financial Services companies are excluded from the
  fundamental ratio and scoring model. Traditional balance-sheet leverage (Debt/Equity, ROCE,
  Interest Coverage) cannot be applied to deposit-taking banks or financial institutions whose
  primary inventory is money.
- **Non-Financial Universe**: 400 operating companies across 21 standard NSE sectors.

### 2.2 Peer-Group Pooling
Percentile ranks within tiny sectors lack statistical meaning. Any sector with fewer than
8 companies is automatically pooled into `"Other Small Sectors"`:
- `Diversified` (1 company)
- `Media Entertainment & Publication` (4 companies)
- `Textiles` (5 companies)
This yields 19 robust peer groups where within-sector percentiles range from $0$ to $100$.

---

## 3. Data Pipeline & Accounting Conventions

### 3.1 Fiscal Year Alignment (March 31 Rule)
The Indian corporate accounting cycle closes on March 31. Annual periods reported by global
aggregators often reflect variable calendar months. The pipeline aligns fiscal periods as:
$$\text{fiscal\_year} = \begin{cases} 
\text{year}, & \text{if } \text{month} \le 3 \\ 
\text{year} + 1, & \text{if } \text{month} \ge 4 
\end{cases}$$
For example, period ending `2025-03-31` maps to FY2025; a period ending `2024-12-31`
aligns to FY2025.

### 3.2 Field Mapping & Priority Resolution
25 standard items are mapped across income, balance sheet, and cash flow statements. When
aggregators expose alternate labels for a single accounting concept (e.g. `Stockholders Equity`
vs `Common Stock Equity`), the system enforces priority ranking where lower priority numbers
take precedence and nulls never overwrite verified values.

### 3.3 Average Balance Rule
Balance sheet items are averaged across consecutive fiscal periods ($t$ and $t-1$) when prior
period data is present to match full-year flow items:
$$\text{avg\_capital\_employed} = 
\frac{(\text{Assets}_t - \text{CurrLiab}_t) + (\text{Assets}_{t-1} - \text{CurrLiab}_{t-1})}{2}$$
$$\text{avg\_shareholders\_equity} = \frac{\text{Equity}_t + \text{Equity}_{t-1}}{2}$$
If the prior period is absent, the period-end value is used as fallback.

### 3.4 Free Cash Flow Convention
Yahoo Finance reports Capital Expenditure inconsistently across companies (sometimes positive,
sometimes negative). The ratio engine enforces an invariant formula:
$$\text{FCF} = \text{Operating Cash Flow} - |\text{Capital Expenditure}|$$

---

## 4. Multi-Factor Scoring Model

### 4.1 Four Factor Dimensions
The model groups 16 core fundamental metrics into 4 orthogonal factor scores:

1. **Quality (Weight: 25%)**:
   - `avg_roce_3yr`: 3-year average Return on Capital Employed (higher is better).
   - `avg_roe_3yr`: 3-year average Return on Equity (higher is better).
   - `operating_margin`: Operating profit margin (higher is better).
   - `cfo_to_net_profit`: Cash flow from operations to net profit (higher is better).
   - `piotroski_f_score`: 9-point composite financial health score (higher is better).

2. **Growth (Weight: 25%)**:
   - `revenue_cagr_3yr`: 3-year compound annual revenue growth (higher is better).
   - `profit_cagr_3yr`: 3-year compound annual net profit growth (higher is better).
   - `eps_growth_yoy`: Year-over-year earnings per share growth (higher is better).

3. **Value (Weight: 25%)**:
   - `earnings_yield`: $\text{Net Income} / \text{Market Cap} \times 100$ (higher is better).
   - `pb_ratio`: $\text{Market Cap} / \text{Equity}$ (lower is better, inverted).
   - `ev_to_ebitda`: $\text{Enterprise Value} / \text{EBITDA}$ (lower is better, inverted).
   - `fcf_yield`: $\text{FCF} / \text{Market Cap} \times 100$ (higher is better).

4. **Safety (Weight: 25%)**:
   - `debt_to_equity`: Total debt to shareholders equity (lower is better, inverted).
   - `interest_coverage`: $\text{EBIT} / \text{Interest Expense}$ (higher is better).
   - `net_debt_to_ebitda`: $\text{Net Debt} / \text{EBITDA}$ (lower is better, inverted).
   - `operating_margin_std_3yr`: 3-year standard deviation of margin (lower is better, inverted).

### 4.2 Statistical Normalization Protocol
1. **Universe Winsorization**: Metrics are clipped at the 1st and 99th percentiles across the
   non-financial universe to eliminate distortion from extreme outliers.
2. **Within-Peer-Group Percentile Ranks**: Metrics are ranked within peer groups on a $0$ to $100$
   scale. For inverted metrics (where lower is better), rank is inverted as $100 - \text{pct}$.
3. **Factor Scores**: Each factor score is the arithmetic mean of available percentile ranks in
   that factor, requiring a minimum of 2 valid metrics per factor.
4. **Master Eligibility**: Companies must possess $\ge 3$ fiscal years of history and valid scores
   in all 4 factor dimensions. Ineligible companies receive `NaN` ranks.
5. **Composite Score & Ranking**: Weighted average of factor scores produces `total_score` ($0$ to
   $100$), overall rank ($1$ to $N$), and sector rank.

### 4.3 Weight Sensitivity Analysis
To assess model stability, baseline equal-weighted scores were tested against 3 tilt schemes:
- **Quality-Tilted** (40% Quality, 20% others): **65.0% overlap** in top-20 leaders.
- **Value-Tilted** (45% Value, 20% Quality/Safety, 15% Growth): **65.0% overlap** in top-20 leaders.
- **Growth-Tilted** (45% Growth, 20% Quality/Safety, 15% Value):
  **50.0% overlap** in top-20 leaders.
The high stability (65% overlap) across value and quality tilts confirms the core leaders are
not fragile artifacts of a single arbitrary weighting choice.

---

## 5. Forensic Red-Flag Audit Engine

The engine monitors 8 independent red-flag rules targeting accounting anomalies:

| Red-Flag Rule | Formula / Condition | Observed Trigger Rate |
| :--- | :--- | :---: |
| `flag_profit_up_cfo_down_2yr` | PAT up YoY while CFO down YoY for 2 consecutive years | 2.3% |
| `flag_low_cash_conversion` | CFO / Net Profit $< 0.50$ for 2 consecutive years | 8.5% |
| `flag_receivables_ballooning`| DSO increased $>20$ days while revenue growth was $<10\%$ | 4.1% |
| `flag_debt_spike` | Total debt increased $>50\%$ YoY and Debt/Equity $> 1.0$ | 6.8% |
| `flag_weak_interest_coverage`| Interest coverage $< 1.5\text{x}$ or EBIT $\le 0$ | 14.5% |
| `flag_persistent_negative_fcf`| Free Cash Flow negative for 2 consecutive years | 11.2% |
| `flag_margin_erosion` | Operating margin contracted $>500$ bps ($>5\%$) YoY | 7.6% |
| `flag_high_other_income` | Other Income $> 40\%$ of EBIT | 3.2% |

Triggered flags do not mechanically disqualify companies; they are preserved as audit metadata
and surfaced in reports and dashboards.

---

## 6. Look-Ahead-Aware Backtest Design

### 6.1 Point-in-Time Formation Windows
- **Formation Date**: June 30 of each fiscal year (e.g. `2024-06-30` for FY2024).
- **Lag Rationale**: In India, companies have 60 days following March 31 to file audited
  annual reports with stock exchanges. June 30 provides a conservative 3-month lag, guaranteeing
  that all statements, balance sheet disclosures, and notes were publicly known.
- **Holding Period**: 12-month forward holding period from June 30 ($t$) to June 30 ($t+1$).
- **Point-in-Time Valuation**: Market capitalization is computed at formation date using
  historical monthly adjusted close prices, preventing future price look-ahead in P/E and EV ratios.

### 6.2 Quintile Allocation & Performance Benchmarking
- Eligible companies are partitioned into 5 equal quintiles (`Q1` Top 20% to `Q5` Bottom 20%).
- 12-month equal-weighted forward returns are measured against the Nifty 500 Index benchmark.
- **Placebo Test**: 500 Monte Carlo simulations randomly shuffle scores within each cohort to
  generate the null distribution of random $Q1 - Q5$ spreads.
