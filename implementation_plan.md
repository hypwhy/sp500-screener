# S&P 500 Stock Screener and Backtesting Application

This application is an interactive web dashboard that screens S&P 500 stocks based on volume and price percentile criteria and backtests the strategy over the last 10 years. It tracks and visualizes subsequent price performance at 30, 60, and 90 calendar day intervals, displaying absolute prices and percentage returns.

## Design Decisions (Aligned with User Inputs)

1. **Application Type**: Interactive Web Application.
   - **Backend**: Python with FastAPI and `yfinance`.
   - **Frontend**: A modern, premium HTML/CSS/JS single-page dashboard.
2. **Volume Requirement (Condition 1)**:
   - A stock matches if its daily trading volume is over $Z\%$ of its **total outstanding shares** on that day ($Volume_{stock} > (Z / 100) \times OutstandingShares_{stock}$).
3. **Price Percentile Requirement (Condition 2)**:
   - A stock matches if its closing price is in the lower $Y\%$ percentile of its own closing price distribution over the past $X$ months.
4. **Future Price Calculation**:
   - Evaluated at exactly **30, 60, and 90 calendar days** after the signal.
   - If a target date falls on a weekend or holiday, the closest future trading day will be used.
   - Both the absolute price and the percentage return relative to the entry price will be shown.
5. **Historical Constituents (Survivorship Bias)**:
   - The current list of S&P 500 stocks will be used for the entire 10-year backtest.
6. **Financial Metrics on the Day (P/E, ROE, Quick Ratio, Cash Ratio)**:
   - For each screened stock and signaled date, the dashboard will display:
     - **P/E (Price-to-Earnings Ratio)**: Calculated as $Price_{entry} / (\text{Quarterly EPS} \times 4)$.
     - **ROE (Return on Equity)**: Calculated as $(\text{Quarterly Net Income} \times 4) / \text{Shareholders' Equity} \times 100\%$.
     - **Quick Ratio**: Calculated as $(\text{Current Assets} - \text{Inventory}) / \text{Current Liabilities}$.
     - **Cash Ratio**: Calculated as $\text{Cash \& Cash Equivalents} / \text{Current Liabilities}$.
   - **Data Limit Warning**: Yahoo Finance's free API only provides quarterly financial statements for the past 1.5 to 2 years (5-7 quarters). For signaled dates that occur prior to the earliest available financial statement, these columns will display as **"N/A"**.

---

## Proposed Technical Architecture

To deliver an exceptional, responsive user experience:
1. **Parallel Data Fetching**: We will use Python's `concurrent.futures.ThreadPoolExecutor` to download historical prices, outstanding shares, quarterly financials, and balance sheets in parallel.
2. **Disk-based Caching**: Raw historical data and financial statements for each ticker will be saved as CSV files in a `data_cache/` directory. Submitting new backtest requests with different $X$, $Y$, or $Z$ parameters will load cached data instantly (under 2 seconds) instead of re-downloading.
3. **Background Backtesting Engine**: Since the initial download of 500 stocks takes ~30-40 seconds, the backtest will run as a background task. The backend will expose a `/api/status` endpoint for real-time progress updates, displaying a beautiful loading progress bar and logs on the frontend.
4. **Interactive Dashboard**:
   - **Controls**: Sliders/inputs for Volume Threshold $Z\%$, Percentile Threshold $Y\%$, and Lookback Window $X$ months.
   - **Overview Cards**: Key performance metrics: Total Signals, Win Rate, and Average Returns at 30/60/90 days.
   - **Charts (Chart.js)**: 
     - A bar chart of Average Returns over time.
     - A timeline of signal counts (to show when the strategy triggers).
   - **Interactive Table**: Fully sortable and searchable list of all signals, with colored indicators (green for positive returns, red for negative).
   - **New Table Columns**: Symbols will show P/E, ROE, Quick Ratio, and Cash Ratio on the signaled day (or "N/A" if out of range).

---

## Proposed Changes

### Backend Structure

#### [NEW] [main.py](file:///c:/Lenovo/AI/AI%20Tools/Antigravity/backend/main.py)
* FastAPI web server exposing:
  * `/api/backtest` (POST): Starts backtest task with parameters $X, Y, Z$.
  * `/api/status` (GET): Returns progress percentage and logs.
  * `/api/results` (GET): Returns results summary and signal list.
  * Static file mounting to serve the frontend.

#### [NEW] [screener.py](file:///c:/Lenovo/AI/AI%20Tools/Antigravity/backend/screener.py)
* Historical data scraper and backtesting engine:
  * Scrapes S&P 500 symbols from Wikipedia with a custom User-Agent.
  * Fetches daily price, historical shares outstanding, quarterly financials, and quarterly balance sheets from Yahoo Finance.
  * Aligns shares outstanding with daily prices.
  * Caches aligned data and quarterly statements in `data_cache/` as CSV files.
  * For each signal date, finds the closest prior financial statements to calculate P/E, ROE, Quick Ratio, and Cash Ratio on the signal day.
  * Executes the screening and backtesting calculations.

---

### Frontend Structure

#### [NEW] [index.html](file:///c:/Lenovo/AI/AI%20Tools/Antigravity/frontend/index.html)
* A high-fidelity single page application:
  * Premium typography (Google Font 'Outfit') and dark mode layout.
  * Card-based sections for settings, status progress, overview stats, and charts.
  * Results data table with search, pagination, and columns sorting.
  * New columns: P/E, ROE, Quick Ratio, Cash Ratio on Signal Day.

#### [NEW] [styles.css](file:///c:/Lenovo/AI/AI%20Tools/Antigravity/frontend/styles.css)
* Custom CSS variables for a curated neon dark theme (slate background, cyan accents, glassmorphic card borders, smooth hover animations).

#### [NEW] [app.js](file:///c:/Lenovo/AI/AI%20Tools/Antigravity/frontend/app.js)
* Frontend logic to:
  * Post parameters and poll backtest status.
  * Handle tabular rendering with pagination and sorting.
  * Instantiate Chart.js charts for performance visualization.

---

## Verification Plan

### Automated Tests
* We will write a test script in `backend/test_screener.py` to assert:
  * Wiki scraping parses 500+ tickers.
  * Volume condition ($Volume_{stock} > Z\% \times Shares$) evaluates correctly.
  * Price percentile ($Price_{stock} \le Y\%$ of $X$-month range) evaluates correctly.
  * Forward return calculations are accurate.
  * Financial metrics (P/E, ROE, Quick Ratio, Cash Ratio) extraction and calculation handle missing rows gracefully.

### Manual Verification
1. Run `python backend/main.py` and open the app at `http://localhost:8000`.
2. Run a backtest with $Z=1.0\%, X=6$ months, $Y=10\%$. Verify the progress bar runs smoothly.
3. Once completed, verify the overview stats, charts, and results table populate correctly.
4. Filter by a ticker (e.g. AAPL) and manually verify that a signal date's price and its 30/60/90 days prices match Yahoo Finance.
5. Verify the financial columns display computed values for recent dates and "N/A" for old dates.
