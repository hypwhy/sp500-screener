import os
import sqlite3
import datetime
import pandas as pd
import yfinance as yf
from flask import Flask, request, jsonify, render_template_string
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback

import ssl
ssl._create_default_https_context = ssl._create_unverified_context

app = Flask(__name__)
DB_FILE = 'screener.db'

# ---------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS stock_data (
            ticker TEXT,
            date TEXT,
            close REAL,
            volume REAL,
            PRIMARY KEY (ticker, date)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS stock_info (
            ticker TEXT PRIMARY KEY,
            float_shares REAL,
            last_updated TEXT
        )
    ''')
    conn.commit()
    conn.close()

# ---------------------------------------------------------
# DATA PIPELINE HELPERS
# ---------------------------------------------------------
def get_sp500_tickers():
    import requests
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    
    # CLOUD FIX 1: Wikipedia actively blocks AWS IPs that use generic browser user-agents.
    # We must use a custom bot name to bypass their security on Render.
    headers = {'User-Agent': 'SP500ScreenerBot/1.0 (Educational Data Sync)'}
    response = requests.get(url, headers=headers)
    response.raise_for_status() # Force an error if Wikipedia blocks us
    
    tables = pd.read_html(response.text)
    return [t.replace('.', '-') for t in tables[0]['Symbol'].tolist()]

def fetch_info(t):
    try:
        info = yf.Ticker(t).info
        return t, info.get('floatShares') or info.get('sharesOutstanding')
    except:
        return t, None

def insert_yf_data(conn, df, ticker_list):
    if df.empty: return
    records = []
    
    if isinstance(df.columns, pd.MultiIndex):
        for ticker in ticker_list:
            if ticker in df.columns.levels[0]:
                tdf = df[ticker]
                for index, row in tdf.iterrows():
                    if pd.notna(row.get('Close')) and pd.notna(row.get('Volume')):
                        records.append((ticker, index.strftime('%Y-%m-%d'), float(row['Close']), float(row['Volume'])))
    else:
        ticker = ticker_list[0]
        for index, row in df.iterrows():
            if pd.notna(row.get('Close')) and pd.notna(row.get('Volume')):
                records.append((ticker, index.strftime('%Y-%m-%d'), float(row['Close']), float(row['Volume'])))
                
    if records:
        conn.executemany('''
            INSERT OR IGNORE INTO stock_data (ticker, date, close, volume) 
            VALUES (?, ?, ?, ?)
        ''', records)
        conn.commit()

# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/update', methods=['POST'])
def update_data():
    try:
        conn = get_db_connection()
        tickers = get_sp500_tickers()
        today = datetime.datetime.today().strftime('%Y-%m-%d')
        
        cursor = conn.execute("SELECT ticker FROM stock_info")
        info_tickers = set(row[0] for row in cursor.fetchall())
        missing_info = [t for t in tickers if t not in info_tickers]
        
        if missing_info:
            print(f"Fetching float shares for {len(missing_info)} tickers...")
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {executor.submit(fetch_info, t): t for t in missing_info}
                for future in as_completed(futures):
                    t, f_shares = future.result()
                    if f_shares:
                        conn.execute("INSERT OR IGNORE INTO stock_info (ticker, float_shares, last_updated) VALUES (?, ?, ?)", 
                                     (t, f_shares, today))
            conn.commit()

        cursor = conn.execute("SELECT ticker, MAX(date) FROM stock_data GROUP BY ticker")
        db_dates = {row[0]: row[1] for row in cursor.fetchall()}
        
        new_tickers = [t for t in tickers if t not in db_dates]
        existing_tickers = [t for t in tickers if t in db_dates]
        
        def chunker(seq, size):
            return (seq[pos:pos + size] for pos in range(0, len(seq), size))

        # CLOUD FIX 2: Turned threads=False. Render's free CPU gets overwhelmed by yfinance multithreading.
        if new_tickers:
            for chunk in chunker(new_tickers, 30):
                data_new = yf.download(chunk, period="3y", group_by="ticker", threads=False)
                insert_yf_data(conn, data_new, chunk)
                
        if existing_tickers:
            date_groups = {}
            for t in existing_tickers:
                date_groups.setdefault(db_dates[t], []).append(t)
                
            for d, t_list in date_groups.items():
                start_date = (datetime.datetime.strptime(d, '%Y-%m-%d') + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                if start_date <= today:
                    for chunk in chunker(t_list, 30):
                        data_inc = yf.download(chunk, start=start_date, group_by="ticker", threads=False)
                        insert_yf_data(conn, data_inc, chunk)

        three_years_ago = (datetime.datetime.today() - datetime.timedelta(days=3*365)).strftime('%Y-%m-%d')
        conn.execute("DELETE FROM stock_data WHERE date < ?", (three_years_ago,))
        conn.commit()
        
        return jsonify({"status": "success", "message": "Data successfully updated."})
    
    except Exception as e:
        # CLOUD FIX 3: If anything crashes, catch it and send the exact error to the browser
        error_msg = traceback.format_exc()
        print(error_msg)
        return jsonify({"status": "error", "message": f"Crash Log:\n{str(e)}"})

@app.route('/screen', methods=['POST'])
def screen():
    data = request.json
    x = float(data.get('x', 10))
    y = float(data.get('y', 1))
    z = int(data.get('z', 5))
    
    conn = get_db_connection()
    df_info = pd.read_sql('SELECT ticker, float_shares FROM stock_info', conn)
    float_map = dict(zip(df_info['ticker'], df_info['float_shares']))
    
    df = pd.read_sql('SELECT * FROM stock_data ORDER BY ticker, date', conn)
    results = []
    
    for ticker, group in df.groupby('ticker'):
        if len(group) < z: continue
        
        float_shares = float_map.get(ticker)
        if not float_shares: continue
        
        recent_data = group.tail(z)
        prices = group['close']
        percentile_threshold = prices.quantile(x / 100.0)
        
        for _, row in recent_data.iterrows():
            vol_pct = (row['volume'] / float_shares) * 100
            
            if vol_pct > y and row['close'] <= percentile_threshold:
                exact_pct = (prices < row['close']).mean() * 100
                results.append({
                    'ticker': ticker,
                    'date': row['date'],
                    'close': round(row['close'], 2),
                    'price_percentile': round(exact_pct, 2),
                    'volume_pct': round(vol_pct, 2)
                })
                
    return jsonify(results)

# ---------------------------------------------------------
# FRONTEND HTML & CSS
# ---------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>S&P 500 Screener</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 40px; background-color: #f4f7f6; color: #333; }
        .card { background: white; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; font-weight: bold; margin-bottom: 5px; }
        input[type="number"] { width: 100%; padding: 10px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; }
        button { background-color: #007bff; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 15px; margin-right: 10px; }
        button:hover { background-color: #0056b3; }
        .btn-update { background-color: #28a745; margin-bottom: 20px;}
        .btn-update:hover { background-color: #218838; }
        .btn-export { background-color: #17a2b8; display: none; }
        .btn-export:hover { background-color: #138496; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #343a40; color: white; cursor: pointer; user-select: none; }
        th:hover { background-color: #4b535b; }
        tr:hover { background-color: #f1f1f1; }
        .loading { display: none; font-weight: bold; color: #d9534f; margin-top: 10px; }
        .header-controls { display: flex; justify-content: space-between; align-items: center; }
    </style>
</head>
<body>

    <h1>S&P 500 Stock Screener</h1>

    <button class="btn-update" onclick="updateData()">1. Sync Stock Data (Sliding Window)</button>
    <div id="update-status" class="loading">Fetching data... Check your terminal for progress. The first run takes a few minutes.</div>

    <div class="card">
        <div class="form-group">
            <label>X: Max Percentile of Close Price (0-100) vs last 36 months</label>
            <input type="number" id="x" value="10" step="0.1">
        </div>
        <div class="form-group">
            <label>Y: Min Trading Volume % vs Total Floating Shares</label>
            <input type="number" id="y" value="1.0" step="0.1">
        </div>
        <div class="form-group">
            <label>Z: Lookback window (Trading Days)</label>
            <input type="number" id="z" value="5">
        </div>
        <button onclick="runScreener()">2. Run Screener</button>
        <div id="screen-status" class="loading">Calculating...</div>
    </div>

    <div class="card" id="results-card" style="display: none;">
        <div class="header-controls">
            <h2>Results</h2>
            <button id="btn-export" class="btn-export" onclick="exportTableToCSV('screener_results.csv')">Download CSV</button>
        </div>
        <table>
            <thead>
                <tr>
                    <th onclick="sortTable(0, 'string')">Ticker &#x21D5;</th>
                    <th onclick="sortTable(1, 'string')">Date &#x21D5;</th>
                    <th onclick="sortTable(2, 'number')">Close ($) &#x21D5;</th>
                    <th onclick="sortTable(3, 'number')">Price Percentile (%) &#x21D5;</th>
                    <th onclick="sortTable(4, 'number')">Vol / Float (%) &#x21D5;</th>
                </tr>
            </thead>
            <tbody id="results-body"></tbody>
        </table>
    </div>

    <script>
        function updateData() {
            document.getElementById('update-status').style.display = 'block';
            fetch('/update', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    document.getElementById('update-status').style.display = 'none';
                    if (data.status === 'error') {
                        // CLOUD FIX 3: Display the exact Python error!
                        alert("SERVER ERROR:\\n" + data.message);
                    } else {
                        alert(data.message);
                    }
                })
                .catch(err => {
                    document.getElementById('update-status').style.display = 'none';
                    alert('Network error connecting to the server.');
                });
        }

        function runScreener() {
            document.getElementById('screen-status').style.display = 'block';
            document.getElementById('results-card').style.display = 'none';
            document.getElementById('btn-export').style.display = 'none';

            fetch('/screen', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    x: document.getElementById('x').value,
                    y: document.getElementById('y').value,
                    z: document.getElementById('z').value
                })
            })
            .then(r => r.json())
            .then(data => {
                document.getElementById('screen-status').style.display = 'none';
                const tbody = document.getElementById('results-body');
                tbody.innerHTML = '';
                
                if(data.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5">No stocks met your criteria.</td></tr>';
                } else {
                    data.forEach(item => {
                        tbody.innerHTML += `<tr>
                            <td><b>${item.ticker}</b></td>
                            <td>${item.date}</td>
                            <td>${item.close}</td>
                            <td>${item.price_percentile}</td>
                            <td>${item.volume_pct}</td>
                        </tr>`;
                    });
                    document.getElementById('btn-export').style.display = 'block';
                }
                document.getElementById('results-card').style.display = 'block';
            });
        }

        let sortDirections = [true, true, true, true, true];

        function sortTable(columnIndex, type) {
            const tbody = document.getElementById('results-body');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            const isAsc = sortDirections[columnIndex];
            sortDirections[columnIndex] = !isAsc;

            rows.sort((a, b) => {
                let cellA = a.children[columnIndex].innerText.replace(/[^0-9.-]+/g,"");
                let cellB = b.children[columnIndex].innerText.replace(/[^0-9.-]+/g,"");

                if (type === 'number') {
                    return isAsc ? parseFloat(cellA) - parseFloat(cellB) : parseFloat(cellB) - parseFloat(cellA);
                } else {
                    cellA = a.children[columnIndex].innerText;
                    cellB = b.children[columnIndex].innerText;
                    return isAsc ? cellA.localeCompare(cellB) : cellB.localeCompare(cellA);
                }
            });

            tbody.innerHTML = '';
            rows.forEach(row => tbody.appendChild(row));
        }

        function exportTableToCSV(filename) {
            let csv = [];
            let rows = document.querySelectorAll("table tr");
            
            for (let i = 0; i < rows.length; i++) {
                let row = [], cols = rows[i].querySelectorAll("td, th");
                
                for (let j = 0; j < cols.length; j++) {
                    let data = cols[j].innerText.replace('⇕', '').trim();
                    row.push('"' + data + '"');
                }
                csv.push(row.join(","));
            }

            let csvFile = new Blob([csv.join("\\n")], {type: "text/csv"});
            let downloadLink = document.createElement("a");
            downloadLink.download = filename;
            downloadLink.href = window.URL.createObjectURL(csvFile);
            downloadLink.style.display = "none";
            document.body.appendChild(downloadLink);
            downloadLink.click();
            document.body.removeChild(downloadLink);
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)