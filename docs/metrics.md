# Metric Definitions & Ratio Methodology

This document provides the formal mathematical definitions, source line items, edge-case rules, and calculation methodologies for all financial metrics and ratios implemented in the Nifty 500 Fundamental Screener.

---

## 1. Core Conventions & Data Handling

### 1.1 Unit Normalization
- All absolute monetary statement values from financial statements are converted to **INR Crore** by dividing raw values by $10^7$ (`value_inr / 10000000.0`).
- Per-share metrics (`basic_eps`, `diluted_eps`) and share counts (`ordinary_shares_number`) remain in their native units.
- Percentage ratios (margins, returns, yields) are expressed as decimal ratios in the database (e.g., $0.15$ denotes $15\%$).

### 1.2 Average-Balance Convention
Where a metric compares an annual flow (from the Income Statement or Cash Flow Statement) to a balance sheet stock (e.g., ROE, ROCE, Asset Turnover), the denominator uses the **average balance** between the current fiscal year and the preceding fiscal year when consecutive data is available:
$$\text{Average Balance}_t = \frac{\text{Balance}_t + \text{Balance}_{t-1}}{2}$$
If the preceding fiscal year balance is missing or non-consecutive, the year-end balance ($\text{Balance}_t$) is used as a fallback to maximize coverage.

### 1.3 Capital Expenditure & Free Cash Flow Convention
In raw financial statements (including Yahoo Finance), Capital Expenditure may be reported as a negative outflow or a positive expenditure. To ensure consistency and avoid sign inversion:
$$\text{FCF} = \text{Operating Cash Flow} - |\text{Capital Expenditure}|$$

### 1.4 Market Valuation Multiples
Valuation ratios ($P/E$, $P/B$, $EV/EBITDA$, Earnings Yield, FCF Yield) are computed using **company-level market capitalization** against **company-level aggregate flows/stocks** (Total Net Income, Total Equity, Total EBITDA, Total FCF) rather than per-share quantities. This guarantees immunity to share count mismatches, stock splits, bonuses, or share consolidation timing differences.

### 1.5 Edge-Case Rules & Zero/Negative Denominator Defense
Financial ratios must never divide by zero or yield nonsensical figures on negative denominators:
- **Negative Equity**: When average or period-end equity $\le 0$, return `NULL` for ROE, P/B, and Debt/Equity.
- **Negative or Zero Capital Employed**: Return `NULL` for ROCE.
- **Negative or Zero Profit**: Return `NULL` for P/E and CFO / Net Profit.
- **Zero Interest Expense**: Return `NULL` for Interest Coverage.
- **Negative or Zero Revenue**: Return `NULL` for Operating Margin, Net Margin, Receivable Days, Inventory Days, and FCF Margin.
- **Negative EBITDA**: Return `NULL` for EV/EBITDA and Net Debt/EBITDA.

---

## 2. Metric Catalog by Group

### 2.1 Profitability

| Metric | Formula | Source Fields | Edge Cases |
| :--- | :--- | :--- | :--- |
| **ROE** (Return on Equity) | $\frac{\text{Net Income}}{\text{Average Equity}}$ | `net_income`, `shareholders_equity` | `NULL` if Average Equity $\le 0$ |
| **ROCE** (Return on Capital Employed) | $\frac{\text{EBIT}}{\text{Average Capital Employed}}$ | `ebit`, `total_assets`, `current_liabilities` | Capital Employed = Total Assets - Current Liabilities. `NULL` if Capital Employed $\le 0$ |
| **Operating Margin** | $\frac{\text{EBIT}}{\text{Revenue}}$ | `ebit`, `revenue` | `NULL` if Revenue $\le 0$ |
| **Net Margin** | $\frac{\text{Net Income}}{\text{Revenue}}$ | `net_income`, `revenue` | `NULL` if Revenue $\le 0$ |

### 2.2 Leverage & Solvency

| Metric | Formula | Source Fields | Edge Cases |
| :--- | :--- | :--- | :--- |
| **Debt to Equity** | $\frac{\text{Total Debt}}{\text{Shareholders' Equity}}$ | `total_debt`, `shareholders_equity` | `NULL` if Equity $\le 0$ |
| **Interest Coverage** | $\frac{\text{EBIT}}{\|\text{Interest Expense}\|}$ | `ebit`, `interest_expense` | `NULL` if $\mid\text{Interest Expense}\mid = 0$ |
| **Net Debt to EBITDA** | $\frac{\text{Total Debt} - \text{Cash}}{\text{EBITDA}}$ | `total_debt`, `cash_and_equivalents`, `ebitda` | `NULL` if EBITDA $\le 0$. Value can be negative if Cash > Debt |

### 2.3 Growth & Stability

| Metric | Formula | Source Fields | Edge Cases |
| :--- | :--- | :--- | :--- |
| **EPS Growth YoY** | $\frac{\text{EPS}_t - \text{EPS}_{t-1}}{\text{EPS}_{t-1}}$ | `diluted_eps` (fallback: `basic_eps`) | `NULL` if prior EPS $\le 0$ or non-consecutive FY |
| **Revenue CAGR (3-Yr)** | $\left(\frac{\text{Revenue}_t}{\text{Revenue}_{t-n}}\right)^{\frac{1}{n}} - 1$ | `revenue` | Requires $n \ge 3$ years span. `NULL` if $\text{Revenue}_{t-n} \le 0$ or $\text{Revenue}_t \le 0$ |
| **Profit CAGR (3-Yr)** | $\left(\frac{\text{Net Income}_t}{\text{Net Income}_{t-n}}\right)^{\frac{1}{n}} - 1$ | `net_income` | Requires $n \ge 3$ years span. `NULL` if $\text{Net Income}_{t-n} \le 0$ or $\text{Net Income}_t \le 0$ |
| **Average ROCE (3-Yr)** | $\text{Mean}(\text{ROCE}_t, \dots, \text{ROCE}_{t-n})$ | `roce` across last 3–4 periods | Ignores null annual ROCE entries |
| **Average ROE (3-Yr)** | $\text{Mean}(\text{ROE}_t, \dots, \text{ROE}_{t-n})$ | `roe` across last 3–4 periods | Ignores null annual ROE entries |
| **Operating Margin Std (3-Yr)** | Sample Standard Deviation of Operating Margin | `operating_margin` across last 3–4 periods | Measures margin stability/pricing power consistency |

### 2.4 Cash Quality

| Metric | Formula | Source Fields | Edge Cases |
| :--- | :--- | :--- | :--- |
| **CFO / Net Profit** | $\frac{\text{Operating Cash Flow}}{\text{Net Income}}$ | `operating_cash_flow`, `net_income` | `NULL` if Net Income $\le 0$ |
| **Free Cash Flow (₹ Cr)** | $\text{Operating Cash Flow} - \|\text{Capital Expenditure}\|$ | `operating_cash_flow`, `capital_expenditure` | Absolute Capex used to handle signed variations |
| **FCF Margin** | $\frac{\text{Free Cash Flow}}{\text{Revenue}}$ | `fcf`, `revenue` | `NULL` if Revenue $\le 0$ |

### 2.5 Operating Efficiency

| Metric | Formula | Source Fields | Edge Cases |
| :--- | :--- | :--- | :--- |
| **Receivable Days** | $\frac{\text{Receivables}}{\text{Revenue}} \times 365$ | `receivables`, `revenue` | `NULL` if Revenue $\le 0$ |
| **Inventory Days** | $\frac{\text{Inventory}}{\text{Cost of Revenue}} \times 365$ | `inventory`, `cost_of_revenue` | `NULL` if Cost of Revenue $\le 0$ |
| **Asset Turnover** | $\frac{\text{Revenue}}{\text{Average Total Assets}}$ | `revenue`, `total_assets` | `NULL` if Average Total Assets $\le 0$ |

### 2.6 Valuation Multiples

| Metric | Formula | Source Fields | Edge Cases |
| :--- | :--- | :--- | :--- |
| **P/E Ratio** | $\frac{\text{Market Cap}}{\text{Net Income}}$ | `market_cap`, `net_income` | `NULL` if Net Income $\le 0$ |
| **P/B Ratio** | $\frac{\text{Market Cap}}{\text{Shareholders' Equity}}$ | `market_cap`, `shareholders_equity` | `NULL` if Equity $\le 0$ |
| **EV / EBITDA** | $\frac{\text{Market Cap} + \text{Debt} - \text{Cash}}{\text{EBITDA}}$ | `market_cap`, `total_debt`, `cash_and_equivalents`, `ebitda` | `NULL` if EBITDA $\le 0$ |
| **Earnings Yield** | $\frac{\text{Net Income}}{\text{Market Cap}}$ | `net_income`, `market_cap` | `NULL` if Market Cap $\le 0$ |
| **FCF Yield** | $\frac{\text{Free Cash Flow}}{\text{Market Cap}}$ | `fcf`, `market_cap` | `NULL` if Market Cap $\le 0$ |

---

## 3. Composite Quality: Piotroski F-Score (9 Signals)

The Piotroski F-score evaluates financial health and turnaround potential using 9 binary signals ($1$ if condition met, else $0$). It requires consecutive two-year data; for a company's earliest available fiscal year, the score evaluates to `NULL`.

### Group 1: Profitability (4 points)
1. **$F_1$ (ROA positive)**: Net Income $> 0$
2. **$F_2$ (CFO positive)**: Operating Cash Flow $> 0$
3. **$F_3$ ($\Delta$ ROA)**: Return on Assets improved:
   $$\frac{\text{Net Income}_t}{\text{Total Assets}_t} > \frac{\text{Net Income}_{t-1}}{\text{Total Assets}_{t-1}}$$
4. **$F_4$ (Accrual / Cash Quality)**: Cash flow exceeds accounting earnings:
   $$\text{Operating Cash Flow}_t > \text{Net Income}_t$$

### Group 2: Leverage, Liquidity & Source of Funds (3 points)
5. **$F_5$ ($\Delta$ Leverage)**: Long-term gearing ratio decreased:
   $$\frac{\text{Total Debt}_t}{\text{Total Assets}_t} < \frac{\text{Total Debt}_{t-1}}{\text{Total Assets}_{t-1}}$$
6. **$F_6$ ($\Delta$ Current Ratio)**: Short-term liquidity improved:
   $$\frac{\text{Current Assets}_t}{\text{Current Liabilities}_t} > \frac{\text{Current Assets}_{t-1}}{\text{Current Liabilities}_{t-1}}$$
7. **$F_7$ (No Share Dilution)**: Common shares outstanding did not increase:
   $$\text{Ordinary Shares Number}_t \le \text{Ordinary Shares Number}_{t-1}$$

### Group 3: Operating Efficiency (2 points)
8. **$F_8$ ($\Delta$ Gross Margin)**: Gross margin expanded:
   $$\frac{\text{Gross Profit}_t}{\text{Revenue}_t} > \frac{\text{Gross Profit}_{t-1}}{\text{Revenue}_{t-1}}$$
9. **$F_9$ ($\Delta$ Asset Turnover)**: Asset turnover improved:
   $$\frac{\text{Revenue}_t}{\text{Total Assets}_t} > \frac{\text{Revenue}_{t-1}}{\text{Total Assets}_{t-1}}$$

$$\text{Piotroski F-Score} = \sum_{i=1}^{9} F_i \quad \in [0, 9]$$
