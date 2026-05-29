# ============================================================
#  SYNC_MARKET_SHARE.PY
#  Scrape GTGD HOSE + HNX từng ngày giao dịch (vnstock/SSI)
#  Kết hợp với GTGD SHS từ Oracle → tính thị phần theo kỳ
#  Push kết quả vào Supabase: market_share + kpi_by_period
#
#  Chạy: python sync_market_share.py
#  Mặc định lấy 14 ngày gần nhất; hoặc truyền --from / --to:
#    python sync_market_share.py --from 2026-05-11 --to 2026-05-15
# ============================================================
import sys, os, argparse
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'digital_dashboard'))

import requests, json
from datetime import date, timedelta
from db import get_engine
import sqlalchemy, pandas as pd

SUPABASE_URL = "https://zwhqitghsrgmmcthbivh.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp3aHFpdGdoc3JnbW1jdGhiaXZoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODc5MzcyNiwiZXhwIjoyMDk0MzY5NzI2fQ.NUu-DXuzq8d_Kkbt-h8BE3I0OrzbNdJ2Z6Kg0eOPCvY"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ── Hằng số đơn vị ────────────────────────────────────────────────────────────
# vnstock VCI source: cột 'value' trả về đơn vị tỉ VND × 100 → chia 100
# vnstock TCBS source: cột 'value' trả về VND → chia 1e9
# TCBS direct HTTP: trường value/tradingValue là VND → chia 1e9
# FireAnt direct: trường dealVolume / dealValue → cần kiểm tra theo thực tế

VNSTOCK_VCI_DIVISOR  = 100    # vnstock source='VCI': giá trị = tỉ × 100
VNSTOCK_TCBS_DIVISOR = 1e9   # vnstock source='TCBS': giá trị = VND
TCBS_HTTP_DIVISOR    = 1e9   # TCBS direct HTTP: value là VND
VNSTOCK_VALUE_DIVISOR = 100  # giữ lại cho backward-compat


def _auto_divisor(val_sample: float) -> float:
    """
    Tự đoán divisor từ giá trị mẫu.
    HOSE GTGD thực tế ≈ 15,000 – 35,000 tỉ VND / ngày.
    """
    if val_sample > 1e13:   return 1e9    # raw VND
    if val_sample > 1e10:   return 1e6    # triệu VND
    if val_sample > 1e7:    return 1e3    # nghìn VND
    if val_sample > 5e4:    return 100    # tỉ × 100 (vnstock VCI)
    return 1.0                            # đã là tỉ VND


# ── 1. Lấy GTGD thị trường THEO TỪNG NGÀY ────────────────────────────────────

def fetch_daily_via_vnstock(date_from: str, date_to: str) -> dict:
    """
    Lấy GTGD HOSE + HNX cho từng ngày.
    Thử lần lượt: source='VCI' → source='TCBS' → source='FMARKET'
    Trả về dict: { 'YYYY-MM-DD': {'hose': <tỉ VND>, 'hnx': <tỉ VND>} }
    """
    result = {}
    try:
        from vnstock import Vnstock
    except ImportError:
        print("  vnstock chưa cài — bỏ qua (chạy: pip install vnstock)")
        return result

    VALUE_COLS  = ['value', 'trading_value', 'totalValue', 'matchValue',
                   'accumulatedTradingValue', 'total_trading_value']
    DATE_COLS   = ['time', 'date', 'tradingDate', 'trading_date']

    def extract_df(df):
        """Parse DataFrame → dict {date: gtgd_bil}"""
        if df is None or df.empty:
            return {}
        cols = list(df.columns)
        date_col = next((c for c in DATE_COLS if c in cols), None)
        val_col  = next((c for c in VALUE_COLS if c in cols), None)
        if not date_col or not val_col:
            print(f"    columns không nhận dạng được: {cols}")
            return {}
        sample = df[val_col].dropna().iloc[0] if not df[val_col].dropna().empty else 0
        divisor = _auto_divisor(float(sample))
        print(f"    col={val_col}, sample={sample:.2e}, divisor={divisor:.0e}")
        rows = {}
        for _, row in df.iterrows():
            dt = str(row[date_col])[:10]
            if not dt or dt < date_from or dt > date_to:
                continue
            val_raw = float(row[val_col] or 0)
            rows[dt] = round(val_raw / divisor, 1)
        return rows

    def get_index_range(symbol, source, **extra):
        try:
            s = Vnstock().stock(symbol=symbol, source=source)
            # Thử gọi với interval (API mới) trước, nếu lỗi thì không truyền
            for kwargs in [{'interval': '1D', **extra}, {**extra}]:
                try:
                    df = s.quote.history(start=date_from, end=date_to, **kwargs)
                    data = extract_df(df)
                    if data:
                        return data
                except TypeError:
                    continue
                except Exception as e2:
                    print(f"    {symbol}/{source} kwargs={kwargs}: {e2}")
                    continue
        except Exception as e:
            print(f"    {symbol}/{source}: {e}")
        return {}

    # Thứ tự thử: VCI trước (nguồn quen), TCBS sau
    for source in ['VCI', 'TCBS']:
        print(f"  vnstock: VNINDEX source={source}  {date_from}→{date_to}")
        hose_map = get_index_range('VNINDEX',  source)
        print(f"  vnstock: HNXINDEX source={source}  {date_from}→{date_to}")
        hnx_map  = get_index_range('HNXINDEX', source)

        all_dates = set(hose_map) | set(hnx_map)
        if all_dates:
            for dt in sorted(all_dates):
                h = hose_map.get(dt, 0.0)
                n = hnx_map.get(dt, 0.0)
                result[dt] = {'hose': h, 'hnx': n}
                print(f"    {dt}  HOSE={h:,.0f}  HNX={n:,.0f}  Tổng={h+n:,.0f} tỉ")
            print(f"  ✓ vnstock/{source} thành công: {len(result)} ngày")
            return result
        print(f"  vnstock/{source}: không có dữ liệu, thử source tiếp theo...")

    print("  vnstock: tất cả source thất bại")
    return result


def fetch_daily_via_tcbs(date_from: str, date_to: str) -> dict:
    """
    Fallback 1: TCBS public API (không cần auth).
    GET https://apipubaws.tcbs.com.vn/stock-insight/v2/stock/bars-long-term
    Params: ticker, type=index, resolution=D, from=unix, to=unix
    """
    from datetime import datetime
    result = {}

    def to_unix(date_str):
        return int(datetime.strptime(date_str, '%Y-%m-%d').timestamp())

    ts_from = to_unix(date_from)
    ts_to   = to_unix(date_to) + 86400  # +1 ngày để lấy đủ ngày cuối

    TCBS_URL = "https://apipubaws.tcbs.com.vn/stock-insight/v2/stock/bars-long-term"
    DATE_FIELDS  = ['tradingDate', 'date', 't', 'time', 'TradingDate']
    VALUE_FIELDS = ['value', 'tradingValue', 'totalMatchedValue',
                    'accumulatedTradingValue', 'Value']

    for symbol, key in [('VNINDEX', 'hose'), ('HNXINDEX', 'hnx')]:
        params = {'ticker': symbol, 'type': 'index', 'resolution': 'D',
                  'from': ts_from, 'to': ts_to}
        try:
            r = requests.get(TCBS_URL, params=params,
                             headers=REQUEST_HEADERS, timeout=20)
            r.raise_for_status()
            resp = r.json()
            # TCBS có thể trả về list trực tiếp hoặc bọc trong key
            bars = resp if isinstance(resp, list) else \
                   resp.get('data', resp.get('bars', resp.get('ohlcList', [])))
            if not bars:
                print(f"  TCBS {symbol}: empty response — {str(resp)[:200]}")
                continue

            sample_val = 0
            for bar in bars:
                for vf in VALUE_FIELDS:
                    if bar.get(vf):
                        sample_val = float(bar[vf])
                        break
                if sample_val:
                    break
            divisor = _auto_divisor(sample_val) if sample_val else TCBS_HTTP_DIVISOR

            for bar in bars:
                dt = None
                for df_ in DATE_FIELDS:
                    if bar.get(df_):
                        dt = str(bar[df_])[:10]
                        break
                if not dt or dt < date_from or dt > date_to:
                    continue
                val_vnd = 0.0
                for vf in VALUE_FIELDS:
                    if bar.get(vf):
                        val_vnd = float(bar[vf])
                        break
                val_bil = round(val_vnd / divisor, 1)
                if dt not in result:
                    result[dt] = {'hose': 0.0, 'hnx': 0.0}
                result[dt][key] = val_bil

            print(f"  TCBS direct {symbol}: OK ({len([b for b in bars if str(b.get('tradingDate',b.get('date','?')))[:10] >= date_from])} bars)")
        except Exception as e:
            print(f"  TCBS direct {symbol}: {e}")

    if result:
        for dt in sorted(result.keys()):
            h = result[dt]['hose']
            n = result[dt]['hnx']
            print(f"    {dt}  HOSE={h:,.0f}  HNX={n:,.0f}  Tổng={h+n:,.0f} tỉ")
        print(f"  ✓ TCBS direct thành công: {len(result)} ngày")
    return result


def fetch_daily_via_fireant(date_from: str, date_to: str) -> dict:
    """
    Fallback 2: FireAnt public REST v2.
    GET https://restv2.fireant.vn/securities/{symbol}/historical-quotes
    Params: startDate, endDate, offset=0, limit=100, sort=1
    """
    result = {}
    FA_URL = "https://restv2.fireant.vn/securities/{symbol}/historical-quotes"
    FA_HEADERS = {**REQUEST_HEADERS,
                  "Accept": "application/json",
                  "Origin": "https://fireant.vn"}

    for symbol, key in [('VNINDEX', 'hose'), ('HNX30', 'hnx')]:
        try:
            params = {'startDate': date_from, 'endDate': date_to,
                      'offset': 0, 'limit': 100, 'sort': 1}
            r = requests.get(FA_URL.format(symbol=symbol), params=params,
                             headers=FA_HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
            bars = data if isinstance(data, list) else data.get('data', [])
            for bar in bars:
                dt = str(bar.get('date', bar.get('tradingDate', '')))[:10]
                if not dt or dt < date_from or dt > date_to:
                    continue
                # FireAnt trả về dealVolume / dealValue / totalVolume / totalValue
                val = float(bar.get('totalValue', bar.get('dealValue',
                             bar.get('totalTradingValue', 0))) or 0)
                divisor = _auto_divisor(val) if val else 1e9
                val_bil = round(val / divisor, 1)
                if dt not in result:
                    result[dt] = {'hose': 0.0, 'hnx': 0.0}
                result[dt][key] = val_bil
            print(f"  FireAnt {symbol}: OK")
        except Exception as e:
            print(f"  FireAnt {symbol}: {e}")

    if result:
        print(f"  ✓ FireAnt thành công: {len(result)} ngày")
    return result


def fetch_daily_via_ssi(date_from: str, date_to: str) -> dict:
    """
    Fallback: SSI MarketStatistic — chỉ trả về ngày mới nhất.
    Dùng khi vnstock không có dữ liệu.
    """
    result = {}
    try:
        for market in ["HOSE", "HNX"]:
            url = "https://fc-data.ssi.com.vn/api/v2/Market/MarketStatistic"
            r = requests.get(url, params={"market": market, "pageIndex": 1, "pageSize": 10},
                             headers=REQUEST_HEADERS, timeout=15)
            r.raise_for_status()
            rows = r.json().get("data", [])
            for row in rows:
                dt = str(row.get("date", ""))[:10]
                if not dt or dt < date_from or dt > date_to:
                    continue
                val = float(row.get("totalTradingValue", row.get("tradingValue", 0)) or 0)
                val_bil = round(val / 1e9, 1)
                if dt not in result:
                    result[dt] = {'hose': 0.0, 'hnx': 0.0}
                if market == "HOSE":
                    result[dt]['hose'] = val_bil
                else:
                    result[dt]['hnx'] = val_bil
        if result:
            print(f"  ✓ SSI thành công: {len(result)} ngày")
    except Exception as e:
        print(f"  SSI lỗi: {e}")
    return result


def fetch_daily_market_gtgd(date_from: str, date_to: str) -> dict:
    """Thử vnstock → SSI."""
    print(f"\n[Fetch HOSE/HNX GTGD từng ngày: {date_from} → {date_to}]")
    data = fetch_daily_via_vnstock(date_from, date_to)
    if not data:
        print("  Thử SSI fallback...")
        data = fetch_daily_via_ssi(date_from, date_to)
    if not data:
        print("  Tất cả nguồn thất bại — không có dữ liệu thị trường.")
    return data


# ── 2. Lấy GTGD SHS từng ngày từ Oracle ──────────────────────────────────────

def get_shs_daily(engine, date_from: str, date_to: str) -> dict:
    """
    Trả về dict { 'YYYY-MM-DD': gtgd_bil } cho từng ngày có dữ liệu.
    """
    sql = sqlalchemy.text("""
        SELECT TRUNC(DT) AS trading_date,
               ROUND(NVL(SUM(TRADING_VALUE), 0) / 1e9, 2) AS gtgd_bil
        FROM FACT_DAILY_CUST_TRADING_MGMT
        WHERE TRUNC(DT) BETWEEN TO_DATE(:df, 'YYYY-MM-DD') AND TO_DATE(:dt, 'YYYY-MM-DD')
        GROUP BY TRUNC(DT)
        ORDER BY TRUNC(DT)
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"df": date_from, "dt": date_to})
    result = {}
    for _, row in df.iterrows():
        dt = str(row["trading_date"])[:10]
        result[dt] = float(row["gtgd_bil"] or 0)
    return result


def get_shs_period_total(engine, date_from: str, date_to: str) -> float:
    """Tổng GTGD SHS lũy kế (tỉ VND)."""
    sql = sqlalchemy.text("""
        SELECT ROUND(NVL(SUM(TRADING_VALUE), 0) / 1e9, 2) AS gtgd_bil
        FROM FACT_DAILY_CUST_TRADING_MGMT
        WHERE TRUNC(DT) BETWEEN TO_DATE(:df, 'YYYY-MM-DD') AND TO_DATE(:dt, 'YYYY-MM-DD')
    """)
    with engine.connect() as conn:
        row = pd.read_sql(sql, conn, params={"df": date_from, "dt": date_to}).iloc[0]
    return float(row["gtgd_bil"] or 0)


# ── 3. Supabase helpers ────────────────────────────────────────────────────────

def sb_upsert(table, data, on_conflict="trading_date,entity"):
    """Upsert rows — cần index UNIQUE (trading_date, entity) trong Supabase."""
    if not data:
        return
    if isinstance(data, dict):
        data = [data]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS, "Prefer": f"resolution=merge-duplicates,return=minimal"},
        data=json.dumps(data, default=str)
    )
    print(f"  upsert {table}: {r.status_code} ({len(data)} rows)")
    if r.status_code not in (200, 201):
        print(f"    BODY: {r.text[:400]}")


def sb_insert(table, data):
    if not data:
        return
    if isinstance(data, dict):
        data = [data]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=HEADERS,
        data=json.dumps(data, default=str)
    )
    print(f"  insert {table}: {r.status_code} ({len(data)} rows)")
    if r.status_code not in (200, 201):
        print(f"    BODY: {r.text[:400]}")


def sb_clear(table, where_col, where_val=None):
    """Xoá tất cả rows (nếu where_val=None) hoặc rows match where_col=where_val."""
    if where_val:
        url = f"{SUPABASE_URL}/rest/v1/{table}?{where_col}=eq.{where_val}"
    else:
        url = f"{SUPABASE_URL}/rest/v1/{table}?{where_col}=not.is.null"
    r = requests.delete(url, headers=HEADERS)
    print(f"  clear {table}: {r.status_code}")


def sb_patch_kpi_period(period_type, period_key, patch_dict):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/kpi_by_period"
        f"?period_type=eq.{period_type}&period_key=eq.{period_key}",
        headers=HEADERS,
        data=json.dumps(patch_dict, default=str)
    )
    print(f"  patch kpi_by_period [{period_type}/{period_key}]: {r.status_code}")
    if r.status_code not in (200, 204):
        print(f"    BODY: {r.text[:200]}")


# ── MAIN ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from', dest='date_from', default=None,
                        help='Ngày bắt đầu YYYY-MM-DD (mặc định: 14 ngày trước)')
    parser.add_argument('--to',   dest='date_to',   default=None,
                        help='Ngày kết thúc YYYY-MM-DD (mặc định: hôm nay)')
    args = parser.parse_args()

    today      = date.today()
    date_to    = args.date_to   or str(today)
    date_from  = args.date_from or str(today - timedelta(days=14))

    print(f"=== sync_market_share  {date_from} → {date_to} ===")

    # ── Bước 1: Dữ liệu thị trường theo ngày ──
    market_data = fetch_daily_market_gtgd(date_from, date_to)

    if not market_data:
        print("Không có dữ liệu thị trường. Dừng.")
        return

    # ── Bước 2: Dữ liệu SHS từ Oracle ──
    print("\n[Fetch SHS GTGD từ Oracle]")
    try:
        engine = get_engine()
        shs_daily = get_shs_daily(engine, date_from, date_to)
        print(f"  Oracle: {len(shs_daily)} ngày có dữ liệu")
        for dt, v in sorted(shs_daily.items()):
            print(f"    {dt}  SHS={v:,.2f} tỉ")
    except Exception as e:
        print(f"  Oracle lỗi: {e}")
        shs_daily = {}

    # ── Bước 3: Tổng hợp và insert market_share_daily ──
    print("\n[market_share_daily — upsert từng ngày]")
    rows = []
    for dt in sorted(market_data.keys()):
        hose_bil  = market_data[dt]['hose']
        hnx_bil   = market_data[dt]['hnx']
        total_bil = round(hose_bil + hnx_bil, 1)
        shs_bil   = shs_daily.get(dt, None)
        share_pct = None
        if shs_bil is not None and total_bil > 0:
            share_pct = round(shs_bil / total_bil * 100, 4)
        print(f"  {dt}: HOSE={hose_bil:,.0f}  HNX={hnx_bil:,.0f}  SHS={shs_bil or '?'}  "
              f"Thị phần={share_pct or '?'}%")
        rows.append({"trading_date": dt, "entity": "HOSE Total", "gtgd_bil": hose_bil})
        rows.append({"trading_date": dt, "entity": "HNX Total",  "gtgd_bil": hnx_bil})
        if shs_bil is not None:
            rows.append({"trading_date": dt, "entity": "SHS", "gtgd_bil": shs_bil,
                         "market_share_pct": share_pct})

    sb_upsert("market_share_daily", rows)

    # ── Bước 4: Cập nhật market_share flat (snapshot ngày mới nhất) ──
    latest_dt = sorted(market_data.keys())[-1]
    latest    = market_data[latest_dt]
    latest_shs = shs_daily.get(latest_dt)
    print(f"\n[market_share — snapshot ngày {latest_dt}]")
    sb_clear("market_share", "entity")
    flat_rows = [
        {"entity": "HOSE Total", "gtgd_bil": latest['hose']},
        {"entity": "HNX Total",  "gtgd_bil": latest['hnx']},
    ]
    if latest_shs is not None:
        flat_rows.insert(0, {"entity": "SHS", "gtgd_bil": latest_shs})
    sb_insert("market_share", flat_rows)

    # ── Bước 5: Patch market_share_pct vào kpi_by_period ──
    # Tính thị phần lũy kế = SUM SHS / SUM (HOSE+HNX) trong khoảng date_from → date_to
    total_market_bil = sum(v['hose'] + v['hnx'] for v in market_data.values())
    shs_dates = set(market_data.keys()) & set(shs_daily.keys())
    total_shs_bil    = sum(shs_daily[d] for d in shs_dates)

    if total_market_bil > 0 and total_shs_bil > 0:
        period_share_pct = round(total_shs_bil / total_market_bil * 100, 4)
        print(f"\n[kpi_by_period ← market_share_pct = {period_share_pct}%]")
        print(f"  SHS lũy kế = {total_shs_bil:,.1f} tỉ / Thị trường = {total_market_bil:,.1f} tỉ")

        td = today
        q_num = (td.month - 1) // 3 + 1
        week_num = td.isocalendar()[1]
        period_configs = [
            ('week',        f"{td.year}-W{week_num:02d}"),
            ('month',       td.strftime('%Y-%m')),
            ('quarter',     f"{td.year}-Q{q_num}"),
            ('fiscal_year', str(td.year)),
        ]
        for period_type, period_key in period_configs:
            sb_patch_kpi_period(period_type, period_key, {
                "market_share_pct": period_share_pct,
                "market_rank": None
            })
    else:
        print("\n  Không đủ dữ liệu để tính thị phần lũy kế.")

    print("\n✅ Sync thị phần hoàn tất!")


if __name__ == "__main__":
    main()
