import sys, os, requests, json
import sqlalchemy, pandas as pd
from datetime import date
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'digital_dashboard'))
from db import get_engine

SUPABASE_URL = "https://zwhqitghsrgmmcthbivh.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp3aHFpdGdoc3JnbW1jdGhiaXZoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODc5MzcyNiwiZXhwIjoyMDk0MzY5NzI2fQ.NUu-DXuzq8d_Kkbt-h8BE3I0OrzbNdJ2Z6Kg0eOPCvY"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

# 1. Doc HOSE/HNX/UPCOM tu Supabase
r = requests.get(
    f"{SUPABASE_URL}/rest/v1/market_share_daily?select=trading_date,entity,gtgd_bil&limit=2000&order=trading_date.asc",
    headers=HEADERS
)
rows = r.json()
by_date = defaultdict(dict)
for row in rows:
    if row['entity'] in ('HOSE Total', 'HNX Total', 'UPCOM Total'):
        by_date[row['trading_date']][row['entity']] = float(row['gtgd_bil'] or 0)

all_dates = sorted(by_date.keys())
print(f"Thi truong: {len(all_dates)} ngay ({all_dates[0]} -> {all_dates[-1]})")

# 2. Doc TRADING_VALUE_STOCK cua SHS tu Oracle theo tung ngay
engine = get_engine()
sql = sqlalchemy.text("""
    SELECT TRUNC(DT) AS dt,
           ROUND(SUM(TRADING_VALUE_STOCK) / 1e9, 2) AS shs_stock
    FROM FACT_DAILY_CUST_TRADING_MGMT
    WHERE TRUNC(DT) BETWEEN TO_DATE(:df,'YYYY-MM-DD') AND TO_DATE(:dt,'YYYY-MM-DD')
    GROUP BY TRUNC(DT)
    ORDER BY TRUNC(DT)
""")
with engine.connect() as conn:
    df = pd.read_sql(sql, conn, params={"df": all_dates[0], "dt": all_dates[-1]})
shs_by_date = {str(row['dt'])[:10]: float(row['shs_stock'] or 0) for _, row in df.iterrows()}
print(f"Oracle SHS: {len(shs_by_date)} ngay co data")

# 3. Tinh lai market_share_pct va upsert
updated = 0
all_shs = 0
all_market = 0

for dt in all_dates:
    d = by_date[dt]
    total = d.get('HOSE Total',0) + d.get('HNX Total',0) + d.get('UPCOM Total',0)
    shs = shs_by_date.get(dt)
    if shs is None or total == 0:
        continue
    pct = round(shs / total * 100, 4)
    # Upsert SHS row voi gtgd_bil = TRADING_VALUE_STOCK va pct moi
    r2 = requests.patch(
        f"{SUPABASE_URL}/rest/v1/market_share_daily?trading_date=eq.{dt}&entity=eq.SHS",
        headers=HEADERS,
        data=json.dumps({"gtgd_bil": shs, "market_share_pct": pct})
    )
    print(f"  {dt}: SHS_stock={shs:,.1f} / Market={total:,.1f} = {pct}% [{r2.status_code}]")
    all_shs += shs
    all_market += total
    updated += 1

print(f"\nCap nhat {updated} ngay")

# 4. Patch kpi_by_period
if all_market > 0:
    period_pct = round(all_shs / all_market * 100, 4)
    print(f"Thi phan luy ke: {all_shs:,.1f} / {all_market:,.1f} = {period_pct}%")
    td = date.today()
    q = (td.month-1)//3+1
    w = td.isocalendar()[1]
    for pt, pk in [
        ('week', f"{td.year}-W{w:02d}"),
        ('month', td.strftime('%Y-%m')),
        ('quarter', f"{td.year}-Q{q}"),
        ('fiscal_year', str(td.year)),
    ]:
        r3 = requests.patch(
            f"{SUPABASE_URL}/rest/v1/kpi_by_period?period_type=eq.{pt}&period_key=eq.{pk}",
            headers=HEADERS,
            data=json.dumps({"market_share_pct": period_pct})
        )
        print(f"  [{pt}/{pk}]: {r3.status_code}")

print("Xong!")
