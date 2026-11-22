# Project 5: Nifty 500 Fundamental Screener

**Role:** Data Analyst · **Difficulty:** ★★★ · **Time:** ~16-20 days (2-3 hrs/day)
**Stack:** Python · pandas · yfinance · PostgreSQL/MySQL (or SQLite to start) · SQL · Power BI or Tableau · Excel (validation) · Docker

*Plan-only guide (no code). It covers what to build, the data and its real limits, every ratio and how to define it, the scoring method, the backtest, the dashboard, code-style rules and a brief to paste into your AI assistant.*

---

## 1. What you are building (and why anyone cares)

Most retail investors pick stocks from tips and headlines. A **fundamental screener** does the opposite: it takes every company in an index, computes the same financial-health ratios for all of them, and ranks them transparently.

Your screener will:
1. Build a **database of financial statements** for Nifty 500 companies.
2. Compute **profitability, leverage, growth, cash-quality and valuation** ratios.
3. Flag **red flags** (profit rising while cash flow falls, debt jumping, receivables ballooning).
4. Score companies **relative to their own sector** (a bank and an IT company can't be compared directly).
5. Show everything in a **Power BI or Tableau dashboard** with company drill-down.
6. Run an **honest backtest**: did high-scoring companies actually do better afterwards?
7. Publish a **methodology document** and a short **research note** on three companies.

**Why it impresses recruiters:** it ties your finance study to real SQL, data modelling, KPI design and dashboarding, and it forces you to deal with the genuinely hard parts of financial data (missing values, restatements, units, look-ahead bias). It is a very natural project for a "data analyst with a finance focus" CV.

**README pitch:** "A transparent, rules-based screener for Nifty 500 companies: ratios, sector-relative scores, red flags and an honest backtest."

---

## 2. Rules of the road (read before you start)

- This is **education and analysis, not investment advice.** In India, publicly issuing buy/sell recommendations can fall under SEBI's research-analyst rules, so check the current SEBI regulations. Word your outputs as **"screen results" and "case studies"**, never "buy" or "sell". The research note in this guide is therefore a *case study of a strong, a weak and a controversial profile*, not a recommendation.
- Yahoo Finance data (via `yfinance`) is **unofficial and personal-use**. Read its terms, add pauses between requests, and keep the repo free of raw downloaded data (publish the code and a small sample or synthetic example, plus instructions).
- If you use any other website's data (Screener.in, NSE, BSE), check its terms before automating any download. Prefer manual downloads or official filings for the small validation set.

---

## 3. Final deliverables checklist

- [ ] GitHub repo with README, ER diagram, architecture diagram, one-command run
- [ ] Data download scripts with caching and a failure log
- [ ] Database schema and loaded data (companies, financials, prices)
- [ ] Metric definitions document (every formula, source fields, edge cases)
- [ ] SQL views/queries computing all ratios
- [ ] Sector-relative scoring model and documented weights
- [ ] Red-flag rules with thresholds and examples
- [ ] Hand-validation sheet in Excel (10 companies checked against annual reports)
- [ ] Power BI or Tableau dashboard with 4 pages and screenshots
- [ ] Backtest notebook with bias discussion
- [ ] Research note (case studies, 1-2 pages, PDF)
- [ ] Tests and CI
- [ ] 3-minute demo video

---

## 4. Data: what you need, where from, and the real limits

### 4.1 Sources

| Data | Source | Notes |
|------|--------|-------|
| **Nifty 500 constituents** (company, sector/industry, symbol, ISIN) | The NSE Indices (niftyindices.com) index constituent CSV for Nifty 500 | Official, free. Save a dated copy (constituents change at each rebalance). Verify the current download link on the site. |
| **Annual financial statements** (income statement, balance sheet, cash flow) | `yfinance` (Yahoo Finance), NSE symbols with the `.NS` suffix | Free and easy, but **usually only about the last 4 annual periods** and coverage for Indian companies is uneven; some fields are missing or labelled differently. Log failures instead of hiding them. |
| **Share prices** (adjusted, daily or monthly) | `yfinance` | Adjusted close accounts for splits/dividends. |
| **Benchmark** | A Nifty 500 index series (e.g., via `yfinance`; check the correct index symbol) or Nifty 50 as a fallback | For the backtest. |
| **Market cap, shares outstanding** | `yfinance` company info fields | Quality varies; spot-check. |
| **Validation data (small)** | Company annual reports (PDF) from each company's investor-relations page, or the BSE/NSE filings | You will hand-check 10 companies against these. |

**Better but harder alternatives (stretch):** NSE/BSE XBRL financial-result filings (official, structured, with longer history if you parse them), or a Kaggle dataset of Indian fundamentals. They need more parsing work and each has its own terms.

### 4.2 The consequences of the 4-year limit
- A "5-year CAGR" is **not possible** with only ~4 annual statements. Use **3-year CAGR** (needs 4 data points) and say so. Do not fake longer history.
- Use **quarterly data** only for freshness checks (latest TTM figures), not as the core history.
- Backtesting over many years is impossible with this history. Your backtest will be **short** (e.g., 2-3 forward periods). State this limitation clearly; it is honest and still instructive. (If you later parse XBRL, you can extend it.)

### 4.3 Scope plan
- **Stage A (Days 1-8):** a **pilot of about 100 companies** (non-financials) to build the full pipeline end to end.
- **Stage B (Days 9-14):** scale to the **full Nifty 500**, handle failures, and add financial companies as a separate track (or exclude them).
- Report **coverage**: how many of the 500 have complete data for each ratio.

### 4.4 Data traps specific to Indian companies
- **Fiscal year:** most Indian companies end the year on **31 March**; a few differ. Label by fiscal year (e.g., "FY2025 = year ended March 2025") and align companies by FY, not calendar year.
- **Standalone vs consolidated:** Yahoo usually reports consolidated figures; make sure you compare like with like and note it.
- **Units:** values are in rupees, not crore; define one display unit (₹ crore) and convert once, in one place.
- **Banks, NBFCs and insurers:** their balance sheets work differently (deposits are not "debt"; the key metrics are NIM, GNPA, CASA, capital adequacy, which Yahoo doesn't supply). **Exclude financials from the main screen** (or build a very small separate track) and say so.
- **Field names differ by company** and change over time: build a **mapping table** (your standard field name to the possible source labels) and log unmapped fields.
- **Restatements:** companies restate prior-year numbers; prefer the **latest filed** value for each period but record the download date.
- **Negative or zero equity/earnings** break ratios (ROE, P/E). Define explicitly how to handle them (set to missing, never divide blindly).
- **Corporate actions** (splits, bonuses, demergers) distort per-share and price history; use adjusted prices and avoid per-share growth rates without adjusting.

---

## 5. Metric definitions (put these in `docs/metrics.md`)

Define each metric precisely: formula, source fields, handling of missing/negative values, and why it matters. Use **average balances** where the metric compares a flow to a balance (ROE, ROCE) when two years of balance sheets are available; otherwise use year-end and note it.

| Group | Metric | Definition (in words) | Why it matters |
|-------|--------|----------------------|----------------|
| **Profitability** | ROE | Net profit ÷ average shareholders' equity | Return generated on owners' money |
| | ROCE | EBIT ÷ (total assets − current liabilities) (capital employed) | Return on all capital, independent of financing mix |
| | Operating margin | Operating profit (EBIT) ÷ revenue | Pricing power and cost control |
| | Net margin | Net profit ÷ revenue | Bottom-line efficiency |
| **Leverage** | Debt/Equity | Total borrowings ÷ shareholders' equity | Financial risk |
| | Interest coverage | EBIT ÷ interest expense | Ability to service debt |
| | Net debt/EBITDA | (Debt − cash) ÷ EBITDA | Years of earnings to repay debt |
| **Growth** | Revenue CAGR (3-yr) | Compound annual growth of revenue across the available span | Business growth |
| | Profit CAGR (3-yr) | Same for net profit (undefined if starting profit ≤ 0) | Earnings growth |
| | EPS growth | Year-over-year change in earnings per share | Per-owner growth |
| **Cash quality** | CFO/Net profit | Operating cash flow ÷ net profit | Are profits backed by cash? |
| | Free cash flow | Operating cash flow − capital expenditure | Cash left for owners/debt |
| | FCF margin / FCF yield | FCF ÷ revenue / FCF ÷ market cap | Cash generation vs size/price |
| **Efficiency** | Receivable days | Receivables ÷ revenue × 365 | Collection speed |
| | Inventory days | Inventory ÷ cost of goods × 365 | Stock management |
| | Asset turnover | Revenue ÷ average total assets | Productivity of assets |
| **Valuation** | P/E | Price ÷ earnings per share (trailing) | Price paid per rupee of profit |
| | P/B | Price ÷ book value per share | Price vs net assets |
| | EV/EBITDA | (Market cap + debt − cash) ÷ EBITDA | Valuation independent of capital structure |
| | Earnings yield | Earnings per share ÷ price (inverse of P/E) | Comparable across sectors; handles low earnings |
| **Composite quality** | Piotroski F-score | A 9-point checklist (profitability, leverage/liquidity, efficiency signals) from financial statements | A well-known, transparent quality score; great for interviews |

**Red-flag rules (document each threshold and the reasoning):**
- Net profit up but operating cash flow down for two consecutive years.
- CFO/Net profit persistently below a low threshold (e.g., under about 0.5 for several years).
- Receivable days rising sharply while revenue growth is modest.
- Debt/Equity jumped by a large share in one year.
- Interest coverage below a low threshold.
- Negative free cash flow for several years while capital expenditure is not driving high growth.
- Margins falling for consecutive years.
- (If data allows) Large changes in other income making up much of profit.

Thresholds are **judgement calls**: write the rationale and show the share of companies flagged by each rule (a rule flagging 80% of companies is useless).

---

## 6. Tools and why

| Purpose | Tool |
|---------|------|
| Download and wrangling | Python 3.11, pandas, NumPy, yfinance, pyarrow |
| Database | PostgreSQL (preferred) or MySQL; SQLite while prototyping |
| Loading | SQLAlchemy (idempotent upserts) |
| Metrics | SQL views for transparency, pandas for checks and the scoring step |
| Dashboard | Power BI **or** Tableau (pick one as primary; recreate a summary page in the other only if you have time) |
| Validation | Excel workbook with your hand-computed ratios |
| Notebooks | Jupyter for EDA, scoring and backtest |
| Quality | pytest, ruff, pre-commit, GitHub Actions |
| Packaging | Docker + docker-compose (database container, loader) |
| Docs | Markdown; ER diagram in draw.io or dbdiagram.io |

---

## 7. Project structure

```
nifty500-screener/
├── README.md
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── requirements.txt
├── .env.example
├── .gitignore
├── config/
│   ├── field_mapping.csv        # standard field -> possible Yahoo labels
│   ├── sector_overrides.csv     # fixes to sector labels
│   └── scoring_weights.csv
├── data/
│   ├── raw/                     # cached downloads (not committed)
│   ├── interim/
│   └── reference/
│       ├── nifty500_constituents.csv   # dated copy
│       └── validation_sheet.xlsx
├── notebooks/
│   ├── 01_coverage_audit.ipynb
│   ├── 02_ratio_checks.ipynb
│   ├── 03_scoring.ipynb
│   └── 04_backtest.ipynb
├── sql/
│   ├── 01_schema.sql
│   ├── 02_ratio_views.sql
│   ├── 03_flags_views.sql
│   └── 04_score_views.sql
├── src/screener/
│   ├── config.py
│   ├── fetch.py
│   ├── clean.py
│   ├── load.py
│   ├── metrics.py
│   ├── scoring.py
│   └── backtest.py
├── tests/
├── dashboard/
│   ├── screener.pbix            # or .twbx
│   └── screenshots/
└── docs/
    ├── metrics.md
    ├── methodology.md
    ├── data_limitations.md
    ├── erd.png
    └── research_note.pdf
```

---

## 8. Data model

Use a clean, simple model so SQL stays readable:

| Table | Grain | Key columns |
|-------|-------|-------------|
| `dim_company` | One row per company | company_id, symbol, name, isin, sector, industry, is_financial, in_nifty500_since (or snapshot date) |
| `dim_period` | One row per fiscal year | fiscal_year, period_end_date |
| `fact_financials` | company × fiscal year × line item (long format) | company_id, fiscal_year, line_item, value_inr, source, downloaded_at |
| `fact_prices_monthly` | company × month | company_id, month_end, adj_close |
| `fact_market` | company × snapshot date | market_cap, shares_outstanding |
| `metrics_annual` (view/table) | company × fiscal year | all ratios |
| `scores` (view/table) | company × scoring date | factor scores, total score, rank, flags |
| `load_log` | One row per download attempt | symbol, status, error message, timestamp |

Long-format `fact_financials` plus a mapping table means a new source field never requires a schema change. Add indexes on company_id and fiscal_year, and a unique key to make reloads idempotent.

---

## 9. Architecture

```
NSE constituents CSV ──┐
Yahoo (yfinance): financials, prices ──┤
                                       ▼
        fetch (cached, rate-limited, logged failures)
                                       ▼
        clean: map fields · fix units · align fiscal years · flag missing
                                       ▼
        PostgreSQL: dim_company · fact_financials · fact_prices_monthly
                                       ▼
        SQL views: ratios · red flags
                                       ▼
        Python: sector-relative scoring · backtest
                                       ▼
        Power BI / Tableau dashboard  +  docs (methodology, research note)
```

---

## 10. Step-by-step build

### Step 1: Setup (Day 1)
Create the repo, structure, environment, `.gitignore` (raw data excluded), Docker database. Save a **dated copy** of the constituents file and note the download date; constituents change over time.

### Step 2: Reference data and mapping (Day 1-2)
- Load the constituents into `dim_company`; mark financial-sector companies (`is_financial`) using the sector label plus your override file.
- Create the **field mapping table**: for each of about 25 standard line items (revenue, EBIT, EBITDA, net income, interest expense, total assets, current liabilities, total debt, equity, receivables, inventory, cash, operating cash flow, capital expenditure, etc.) list the Yahoo labels that may represent it.
- Decide the pilot list of ~100 non-financial companies across sectors.

### Step 3: Download pipeline (Days 2-4)
- Fetch annual income statement, balance sheet and cash flow for each symbol; fetch monthly adjusted prices.
- Rate-limit politely, retry with backoff, and **cache** every response to disk so re-runs don't re-download.
- Log every success/failure with the reason in `load_log`.
- Produce a **coverage report**: for each standard line item, the share of companies with at least 3-4 years present.

### Step 4: Cleaning and loading (Days 4-5)
- Map labels to standard names; convert units once; align to fiscal years; keep both consolidated/standalone note if known.
- Handle missing values explicitly (leave null; never fill with zero without a rule).
- Load with **idempotent upserts** (re-running changes nothing if data is unchanged).
- Add data-quality checks: balance sheet identity (assets ≈ liabilities + equity within tolerance), negative revenue, absurd jumps; list companies failing checks.

### Step 5: Ratio engine in SQL (Days 5-7)
- Write views for each metric group; keep formulas in one place and consistent with `docs/metrics.md`.
- Handle division by zero/negatives with explicit rules.
- Add trailing-year views (latest FY) and a 3-year view for growth and stability (e.g., average ROCE over 3 years; standard deviation of margins).
- **Unit-test formulas** on 2-3 companies computed by hand.

### Step 6: Hand validation (Day 7-8)
- Pick **10 companies** across sectors. For each, take values from the company's annual report and compute 5-6 key ratios in an Excel sheet.
- Compare with your pipeline output; record the **difference**. Explain each mismatch (definition difference, standalone vs consolidated, restatement).
- Report the share of ratios within a tolerance (e.g., 2%). This validation table is one of the most valuable parts of the project; interviewers rarely see it.

### Step 7: Scale to the full Nifty 500 (Days 9-10)
Run the pipeline on all companies, review the failure log, fix mapping gaps, re-run. Report final coverage by ratio and by sector. Decide and document the **minimum data rule** for scoring (e.g., at least 3 years of core metrics).

### Step 8: Red flags (Day 10-11)
Implement the rules as views. For each rule report: how many companies flagged, which sectors, and 3 example companies with the numbers behind the flag. Tune thresholds so rules are meaningful, and document why.

### Step 9: Scoring model (Days 11-13)
- **Group by sector** and compute **percentile ranks within the sector** for each metric (a 20% ROCE means different things in IT vs capital goods). If a sector has too few companies, group related sectors.
- **Winsorize** extreme values (cap at e.g. 1st/99th percentile) before ranking.
- Flip direction where lower is better (leverage, valuation multiples, receivable days).
- Build four factor scores: **Quality** (ROCE, ROE, margins, F-score, cash conversion), **Growth** (revenue and profit CAGR, EPS growth), **Value** (earnings yield, P/B, EV/EBITDA, FCF yield), **Safety** (leverage, coverage, stability of margins).
- Combine with **documented weights** (start equal or quality-tilted) and run a **sensitivity check**: how much do the top 20 change under different weights?
- Output rank, factor scores, flags and a "data completeness" indicator.
- Be clear that the score is a **screen for further research**, not a prediction.

### Step 10: Backtest (Days 13-15)
Design it carefully and report it modestly:
- **Look-ahead rule:** only use financials that were publicly available at the portfolio formation date. Annual results are typically published within a couple of months after March; use a conservative lag (e.g., about 3 months after fiscal year-end) before using a year's data.
- **Procedure:** at each formation date, rank companies by score, split into quintiles, hold for 12 months (or the longest available), compare average returns of the top quintile vs the bottom quintile and vs the benchmark.
- With ~4 years of fundamentals, you will have only a few formation dates. Say so, and avoid strong claims.
- **Known biases to state:** survivorship (you use *today's* index members, so past failures are missing), look-ahead in constituents, no transaction costs or liquidity limits, small sample, one market regime.
- Report returns, hit rate and drawdown; also show a **placebo** (randomly shuffled scores) to show what chance looks like.
- A negative or inconclusive result is **fine and credible** if explained.

### Step 11: Dashboard (Days 14-17)
Pick Power BI or Tableau as the primary. Pages:
1. **Market overview:** number of companies, median ROCE/P/E by sector, score distribution, share of companies with red flags.
2. **Screener:** filterable table (sector, score range, market-cap band, flags) with conditional formatting; sort by score; export-friendly.
3. **Company drill-down:** 4-year trends (revenue, profit, margins, ROCE, cash conversion, debt), factor scores vs sector median, flags, a peer comparison.
4. **Sector view:** scatter of ROCE vs earnings yield (or P/E) with bubble size = market cap, highlighting the top-scored names; red-flag heat map by sector.
5. (Optional) **Methodology** page summarising definitions and limitations on the dashboard itself.

Design rules: one accent colour, sorted bars, ₹ crore with separators, titles that state the finding, no more than about six visuals per page, slicers synced across pages, a visible "data as of" date and a visible disclaimer.

### Step 12: Research note (Day 17)
A 1-2 page PDF with **three case studies**: a company with strong, consistent metrics; one with weak/risky metrics; and a **controversial** one (e.g., high profit but poor cash conversion). For each: the numbers, the chart, what the screen shows, **what it can't see** (management quality, industry cycles, governance), and no buy/sell language. Add the standard disclaimer.

### Step 13: Tests, CI, polish (Days 18-20)
- Tests: field mapping, unit conversion, fiscal-year alignment, each ratio formula (hand-checked cases), division-by-zero handling, percentile ranking within sectors, look-ahead lag logic in the backtest.
- CI: lint and tests on every push.
- README with architecture, ER diagram, coverage table, validation table, backtest summary (with caveats), dashboard GIF and limitations.
- LinkedIn post: "I built a Nifty 500 screener and hand-checked it against annual reports. Here is what the data said, and where it was wrong."

---

## 11. Metrics to report (measure yours; do not copy numbers)

| Metric | Why |
|--------|-----|
| Companies loaded / complete data (overall and by sector) | Shows data reality and coverage |
| Fields mapped, unmapped fields logged | Data-engineering quality |
| Validation: % of ratios within tolerance of hand calculation | Credibility of the pipeline |
| Share of companies flagged per red-flag rule | Usefulness of rules |
| Score stability under weight changes (overlap of top 20) | Robustness |
| Backtest: top vs bottom quintile and vs benchmark, with placebo | Honest evidence |
| Pipeline runtime and re-run idempotency | Engineering quality |

---

## 12. Code and SQL style (give this to your AI assistant)

The aim is **clean, ordinary, professional work**:

- Short, descriptive snake_case names; small functions with one purpose; modules by responsibility; config and thresholds in config files, not scattered in code.
- SQL: lowercase or consistently cased keywords, one CTE per idea, meaningful CTE names, aliases that read well, no select-star in views, formulas identical to `docs/metrics.md`.
- **Few comments**, only for non-obvious reasons (e.g., why fiscal years are aligned this way, why a 3-month lag). No comments restating the code, no banner/divider or "Step 1" comments, no TODO lists, no commented-out code, no emojis.
- One-line docstrings on public functions; type hints on signatures.
- `logging` instead of prints; short, specific error messages.
- `pathlib` for paths; format and lint with `ruff`.
- Notebooks: markdown headings and short findings, outputs cleared before commit; heavy logic lives in `src/`.
- Git: many small commits over time with plain messages; README in your own words with real numbers only.

---

## 13. Brief to paste into your AI assistant

> Before writing any code, ask me a few clarifying questions about this project (database choice, Power BI vs Tableau, pilot size, how to treat financial companies, and any constraints). Then build the project in this guide step by step, starting with Step 1 and following the folder structure. Use Python 3.11, pandas, yfinance, SQLAlchemy and PostgreSQL (or the database I choose), with SQL views for the ratios. Data: the NSE Nifty 500 constituents CSV and Yahoo Finance via yfinance, with caching, polite rate limiting and a failure log. Treat fiscal years as 31 March year-ends, exclude financial companies from the main screen, use only about 4 years of annual history and say so, and never invent data or fill missing values without a documented rule. Write clean code and SQL following Section 12: minimal comments (only for non-obvious reasons), no banner or step comments, no emojis, short descriptive names, small functions, type hints on signatures, logging instead of prints. Add tests for each ratio and for the backtest look-ahead lag. After each step, run it, show me the output, and explain in plain language what it did and why. Do not invent results: every number in the README must come from a real run. Note: if your environment has no internet access, test with realistic synthetic data and tell me clearly; I will run the real downloads on my own machine.

If your AI assistant works in a sandbox with **no internet**, it cannot call Yahoo Finance or NSE. It will test on synthetic data, and you must run the real download yourself, review the coverage report and do the hand validation before trusting or publishing any number.

---

## 14. A candid note about AI-assisted building

Clean, conventional code is simply good engineering, but style does not give you understanding, and interviews test understanding. Be ready to explain without notes: what ROCE and cash conversion mean and why they matter; why you score within sectors; why banks are excluded; how you avoided look-ahead bias; what survivorship bias does to your backtest; and which of your ratios disagreed with the annual reports and why.

Use the AI as a pair-programmer: understand every module and view, be able to change a threshold or weight and re-run, and if asked whether you used AI tools, answer plainly and describe what you decided versus what the tool wrote.

---

## 15. Interview questions to prepare

1. What is ROCE, and why prefer it to ROE for comparing companies with different debt levels?
2. Why is cash conversion (CFO ÷ net profit) an important quality check?
3. Why score within sectors? What goes wrong with one global ranking?
4. Why did you exclude banks and NBFCs, and how would you analyse them?
5. How did you handle missing data, restatements, and negative earnings?
6. How did you validate the pipeline? What mismatches did you find?
7. What is look-ahead bias, and what lag did you use in the backtest and why?
8. What is survivorship bias, and how does it affect your backtest?
9. What is the Piotroski F-score and what are its limits?
10. How sensitive is the top-20 list to your weights?
11. What would you change with access to 10+ years of filings data?
12. What can a screener never tell you about a company?

---

## 16. Common mistakes

1. Trusting Yahoo data without validating against annual reports.
2. Mixing consolidated and standalone, or rupees and crore.
3. Comparing a bank with an IT company on the same ratios.
4. Dividing by zero or negative equity/earnings and publishing nonsense ratios.
5. Calling a 3-year CAGR a "5-year CAGR".
6. Using financials before they were published in the backtest.
7. Backtesting with today's index members and not mentioning survivorship bias.
8. Red-flag thresholds that flag most of the market (or none).
9. A dashboard full of tables with no finding in the titles.
10. Using words like "buy", "sell" or "target price" in public outputs.
11. Committing raw downloaded data to GitHub.
12. README numbers that did not come from a real run.

---

## 17. Stretch goals

- Parse NSE/BSE XBRL result filings to extend history beyond 4 years and enable a longer backtest.
- A separate **financials track** (banks/NBFCs) with NIM, GNPA, CASA and capital adequacy from annual reports for a small set.
- **Beneish M-score** (earnings-manipulation indicator) as an advanced red flag.
- **Shareholding pattern** features (promoter holding and pledging trends) from exchange filings.
- **Peer-valuation view:** each company's P/E vs its sector's historical range.
- **Streamlit app** version of the screener with downloadable results.
- **Monthly refresh job** (scheduled) with a data-freshness indicator on the dashboard.
- Sensitivity dashboard letting a user change factor weights and see the ranking update.

---

## 18. CV bullets (fill with your measured numbers)

- Built a fundamentals database for `[N]` Nifty 500 companies (Python, SQL, PostgreSQL) with an idempotent loader, field-mapping layer and data-quality checks; achieved `[X]%` complete coverage on core ratios.
- Defined and implemented `[N]` financial ratios and `[N]` red-flag rules in SQL; validated against annual reports for `[N]` companies with `[X]%` of ratios within `[Y]%` tolerance.
- Designed a sector-relative scoring model (quality, growth, value, safety) with weight-sensitivity analysis, and a look-ahead-aware backtest with placebo comparison; documented survivorship and sample-size limits.
- Delivered a 4-page Power BI/Tableau screener with company drill-down and a methodology note.

---

## 19. Day-by-day plan

| Day | Goal |
|-----|------|
| 1 | Repo, environment, database, constituents file (dated), config |
| 2 | Field mapping table, pilot list of ~100 companies |
| 2-4 | Cached, rate-limited download pipeline and failure log; coverage report |
| 4-5 | Cleaning, unit/fiscal-year alignment, idempotent loading, quality checks |
| 5-7 | SQL ratio views and unit tests |
| 7-8 | Hand validation in Excel (10 companies) |
| 9-10 | Scale to full Nifty 500; fix mapping gaps; final coverage |
| 10-11 | Red-flag views and rule review |
| 11-13 | Sector-relative scoring and sensitivity |
| 13-15 | Backtest with look-ahead lag, placebo and bias write-up |
| 14-17 | Dashboard (4 pages) |
| 17 | Research note (3 case studies) |
| 18-20 | Tests, CI, README, demo video, LinkedIn post |

---

*Educational project using publicly available data. Not investment advice. Output does not constitute a recommendation to buy, sell or hold any security. Check data-provider terms and current SEBI rules before publishing.*
