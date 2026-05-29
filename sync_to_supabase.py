# ============================================================
#  SYNC_TO_SUPABASE.PY — v5
#  Ghi vào flat tables (backward compat) VÀ
#  kpi_by_period + series_by_period (Vercel dashboard đọc)
# ============================================================
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'digital_dashboard'))

import requests, json
from datetime import date, timedelta
from db import get_engine
import queries

SUPABASE_URL = "https://zwhqitghsrgmmcthbivh.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp3aHFpdGdoc3JnbW1jdGhiaXZoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODc5MzcyNiwiZXhwIjoyMDk0MzY5NzI2fQ.NUu-DXuzq8d_Kkbt-h8BE3I0OrzbNdJ2Z6Kg0eOPCvY"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

def insert(table, data):
    """Insert rows vào bảng Supabase."""
    if not data:
        print(f"  {table}: skip (no data)")
        return
    if isinstance(data, dict):
        data = [data]
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=HEADERS,
        data=json.dumps(data, default=str)
    )
    print(f"  {table}: {r.status_code} ({len(data)} rows)")
    if r.status_code not in (200, 201):
        print(f"    BODY: {r.text[:300]}")

def clear(table, col):
    """Xoá toàn bộ rows của bảng (filter theo col is not null)."""
    r = requests.delete(
        f"{SUPABASE_URL}/rest/v1/{table}?{col}=not.is.null",
        headers=HEADERS
    )
    print(f"  clear {table}: {r.status_code}")
    if r.status_code not in (200, 204):
        print(f"    BODY: {r.text[:200]}")

def main():
    print("Connecting to Oracle...")
    engine = get_engine()
    print("Connected!\n")

    today = date.today()
    year_start  = date(today.year, 1, 1)
    month_start = date(today.year, today.month, 1)

    # ── 1. kpi_summary (snapshot, dashboard lấy row mới nhất theo id) ──
    print("[kpi_summary]")
    try:
        kpi = queries.kpi_summary(engine)
        insert("kpi_summary", {
            "total_customer":   int(kpi.get("total_customer")   or 0),
            "active_customer":  int(kpi.get("active_customer")  or 0),
            "total_nav_bil":    float(kpi.get("total_nav_bil")   or 0),
            "total_aum_bil":    float(kpi.get("total_aum_bil")   or 0),
            "total_margin_bil": float(kpi.get("total_margin_bil") or 0),
        })
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 2. new_accounts (theo tháng trong năm) ──
    print("[new_accounts]")
    try:
        na = queries.monthly_new_accounts(engine, date_from=year_start, date_to=today)
        rows = []
        for ch, vals in na.get("channels", {}).items():
            for label, v in zip(na.get("labels", []), vals):
                rows.append({"month_label": label, "channel": ch, "new_accounts": int(v or 0)})
        if rows:
            clear("new_accounts", "month_label")
            insert("new_accounts", rows)
        else:
            print("  new_accounts: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 3. active_rate (theo tháng trong năm) ──
    print("[active_rate]")
    try:
        ar = queries.monthly_active_rate(engine, date_from=year_start, date_to=today)
        rows = [
            {"month_label": l, "active_rate_pct": float(v or 0)}
            for l, v in zip(ar.get("labels", []), ar.get("rates", []))
        ]
        if rows:
            clear("active_rate", "month_label")
            insert("active_rate", rows)
        else:
            print("  active_rate: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 4. cashflow (theo tháng trong năm) ──
    print("[cashflow]")
    try:
        cf = queries.monthly_cashflow(engine, date_from=year_start, date_to=today)
        rows = [
            {"month_label": l, "cash_in_bil": float(ci or 0),
             "cash_out_bil": float(co or 0), "net_bil": float(net or 0)}
            for l, ci, co, net in zip(
                cf.get("labels", []), cf.get("cash_in", []),
                cf.get("cash_out", []),  cf.get("net", [])
            )
        ]
        if rows:
            clear("cashflow", "month_label")
            insert("cashflow", rows)
        else:
            print("  cashflow: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 5. nav_trend (theo tháng trong năm) ──
    print("[nav_trend]")
    try:
        nav = queries.nav_trend(engine, date_from=year_start, date_to=today)
        rows = [
            {"month_label": l, "total_nav_bil": float(v or 0)}
            for l, v in zip(nav.get("labels", []), nav.get("nav", []))
        ]
        if rows:
            clear("nav_trend", "month_label")
            insert("nav_trend", rows)
        else:
            print("  nav_trend: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 6. trading_kpi (snapshot tháng hiện tại) ──
    print("[trading_kpi]")
    try:
        tkpi = queries.trading_kpi(engine, date_from=month_start, date_to=today)
        insert("trading_kpi", {
            "gtgd_bil":       float(tkpi.get("gtgd_bil")       or 0),
            "fee_mil":        float(tkpi.get("fee_mil")         or 0),
            "derivative_vol": int(tkpi.get("derivative_vol")   or 0),
            "deriv_fee_mil":  float(tkpi.get("deriv_fee_mil")  or 0),
        })
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 7. trading_channel (tháng hiện tại) ──
    print("[trading_channel]")
    try:
        ch = queries.trading_by_channel(engine, date_from=month_start, date_to=today)
        rows = [
            {"channel": c, "gtgd_bil": float(g or 0), "fee_bps": float(f or 0)}
            for c, g, f in zip(ch.get("channels", []), ch.get("gtgd", []), ch.get("fee_bps", []))
        ]
        if rows:
            clear("trading_channel", "channel")
            insert("trading_channel", rows)
        else:
            print("  trading_channel: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 8. market_share ──
    print("[market_share]")
    try:
        ms = queries.market_share(engine, date_to=today)
        rows = [
            {"entity": e, "gtgd_bil": float(v or 0)}
            for e, v in zip(ms.get("entities", []), ms.get("values", []))
        ]
        if rows:
            clear("market_share", "entity")
            insert("market_share", rows)
        else:
            print("  market_share: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 9. margin_trend (theo tháng trong năm) ──
    print("[margin_trend]")
    try:
        mg = queries.margin_trend(engine, date_from=year_start, date_to=today)
        rows = [
            {"month_label": l, "normal_bil": float(n or 0),
             "m3b_bil": float(m or 0), "ut_bil": float(u or 0)}
            for l, n, m, u in zip(
                mg.get("labels", []), mg.get("normal", []),
                mg.get("m3b", []),     mg.get("ut", [])
            )
        ]
        if rows:
            clear("margin_trend", "month_label")
            insert("margin_trend", rows)
        else:
            print("  margin_trend: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 10. broker_kpi (snapshot) ──
    print("[broker_kpi]")
    try:
        bkpi = queries.broker_kpi(engine)
        insert("broker_kpi", {
            "total_broker":       int(bkpi.get("total_broker")       or 0),
            "avg_kh_per_broker":  float(bkpi.get("avg_kh_per_broker") or 0),
            "avg_nav_per_broker": float(bkpi.get("avg_nav_per_broker") or 0),
        })
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 11. broker_top10 (tháng hiện tại) ──
    print("[broker_top10]")
    try:
        bt = queries.broker_top10(engine, date_from=month_start, date_to=today)
        rows = [
            {"broker_name": b, "total_revenue_mil": float(r or 0)}
            for b, r in zip(bt.get("brokers", []), bt.get("revenues", []))
        ]
        if rows:
            clear("broker_top10", "broker_name")
            insert("broker_top10", rows)
        else:
            print("  broker_top10: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 12. broker_center (snapshot) ──
    print("[broker_center]")
    try:
        bc = queries.broker_by_center(engine)
        rows = [
            {"center": c, "active_kh": int(v or 0)}
            for c, v in zip(bc.get("centers", []), bc.get("values", []))
        ]
        if rows:
            clear("broker_center", "center")
            insert("broker_center", rows)
        else:
            print("  broker_center: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 13. broker_type (tháng hiện tại) ──
    print("[broker_type]")
    try:
        btype = queries.broker_by_type(engine, date_from=month_start, date_to=today)
        rows = [
            {"broker_type": t, "new_kh": int(v or 0)}
            for t, v in zip(btype.get("types", []), btype.get("values", []))
        ]
        if rows:
            clear("broker_type", "broker_type")
            insert("broker_type", rows)
        else:
            print("  broker_type: skip (Oracle trống)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ══════════════════════════════════════════════════════════
    #  PHẦN 2: kpi_by_period + series_by_period (Vercel dashboard)
    #  Vercel đọc từ 2 bảng này theo period_type filter
    # ══════════════════════════════════════════════════════════

    # Tính period boundaries
    from datetime import timedelta
    week_start   = today - timedelta(days=today.weekday())           # Thứ 2 tuần này
    q_month      = ((today.month - 1) // 3) * 3 + 1                 # Tháng đầu quý
    quarter_start = date(today.year, q_month, 1)
    week_num     = today.isocalendar()[1]

    q_num = (today.month - 1) // 3 + 1
    period_configs = [
        ('week',        f"{today.year}-W{week_num:02d}", week_start,
         f"Tuần {week_num}/{today.year}"),
        ('month',       today.strftime('%Y-%m'),          month_start,
         f"Tháng {today.month}/{today.year}"),
        ('quarter',     f"{today.year}-Q{q_num}",         quarter_start,
         f"Q{q_num}/{today.year}"),
        ('fiscal_year', str(today.year),                  year_start,
         f"Năm {today.year}"),
    ]

    # ── kpi_by_period: 1 row per period type (KPI tổng hợp) ──
    print("\n[kpi_by_period]")
    try:
        kpi_snap = queries.kpi_summary(engine)
        for period_type, period_key, date_from, period_label in period_configs:
            try:
                tkpi  = queries.trading_kpi(engine, date_from=date_from, date_to=today)
                mkpi  = queries.margin_interest_kpi(engine, date_from=date_from, date_to=today)
                requests.delete(
                    f"{SUPABASE_URL}/rest/v1/kpi_by_period"
                    f"?period_type=eq.{period_type}&period_key=eq.{period_key}",
                    headers=HEADERS
                )
                insert("kpi_by_period", {
                    "period_type":      period_type,
                    "period_key":       period_key,
                    "period_label":     period_label,
                    "date_from":        str(date_from),
                    "date_to":          str(today),
                    "total_customer":   int(kpi_snap.get("total_customer")   or 0),
                    "active_customer":  int(kpi_snap.get("active_customer")  or 0),
                    "total_nav_bil":    float(kpi_snap.get("total_nav_bil")   or 0),
                    "total_aum_bil":    float(kpi_snap.get("total_aum_bil")   or 0),
                    "total_margin_bil": float(kpi_snap.get("total_margin_bil") or 0),
                    "gtgd_bil":               float(tkpi.get("gtgd_bil")            or 0),
                    "trading_value_stock":    float(tkpi.get("trading_value_stock")  or 0),
                    "trading_value_bond":     float(tkpi.get("trading_value_bond")   or 0),
                    "fee_mil":          float(tkpi.get("fee_mil")              or 0),
                    "derivative_vol":   int(tkpi.get("derivative_vol")        or 0),
                    # deriv_fee_mil = lãi margin net (đọc bởi ov-deriv-fee trên Vercel)
                    "deriv_fee_mil":    float(mkpi.get("margin_interest_mil") or 0),
                })
            except Exception as e:
                print(f"  {period_type}: ERROR {e}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── series_by_period: time-series cho charts ──
    print("\n[series_by_period]")
    try:
        # Lấy monthly data (dùng YTD)
        _nav  = queries.nav_trend(engine, date_from=year_start, date_to=today)
        _na   = queries.monthly_new_accounts(engine, date_from=year_start, date_to=today)
        _ar   = queries.monthly_active_rate(engine, date_from=year_start, date_to=today)
        _cf   = queries.monthly_cashflow(engine, date_from=year_start, date_to=today)
        _mg   = queries.margin_trend(engine, date_from=year_start, date_to=today)

        series_rows = []

        # NAV trend
        for lbl, val in zip(_nav.get("labels", []), _nav.get("nav", [])):
            series_rows.append({"metric": "nav", "dimension": lbl, "value_num": float(val or 0)})

        # New accounts (dimension = MM/YYYY|channel)
        for ch_name, vals in _na.get("channels", {}).items():
            for lbl, val in zip(_na.get("labels", []), vals):
                series_rows.append({"metric": "new_accounts",
                                    "dimension": f"{lbl}|{ch_name}",
                                    "value_num": int(val or 0)})

        # Active rate
        for lbl, val in zip(_ar.get("labels", []), _ar.get("rates", [])):
            series_rows.append({"metric": "active_rate", "dimension": lbl, "value_num": float(val or 0)})

        # Cashflow
        for lbl, ci, co, net in zip(_cf.get("labels", []), _cf.get("cash_in", []),
                                     _cf.get("cash_out", []), _cf.get("net", [])):
            series_rows.append({"metric": "cash_in",   "dimension": lbl, "value_num": float(ci  or 0)})
            series_rows.append({"metric": "cash_out",  "dimension": lbl, "value_num": float(co  or 0)})
            series_rows.append({"metric": "net_cash",  "dimension": lbl, "value_num": float(net or 0)})

        # Margin trend
        for lbl, n, m, u in zip(_mg.get("labels", []), _mg.get("normal", []),
                                  _mg.get("m3b", []), _mg.get("ut", [])):
            series_rows.append({"metric": "margin_normal", "dimension": lbl, "value_num": float(n or 0)})
            series_rows.append({"metric": "margin_3b",     "dimension": lbl, "value_num": float(m or 0)})
            series_rows.append({"metric": "margin_ut",     "dimension": lbl, "value_num": float(u or 0)})

        if series_rows:
            # Push vào tất cả 4 period types (Vercel filter nào cũng có data)
            for period_type, period_key, date_from_p, period_label in period_configs:
                requests.delete(
                    f"{SUPABASE_URL}/rest/v1/series_by_period?period_type=eq.{period_type}",
                    headers=HEADERS
                )
                typed = [{**r, "period_type": period_type, "period_key": period_key,
                          "period_label": period_label,
                          "date_from": str(date_from_p), "date_to": str(today)}
                         for r in series_rows]
                insert("series_by_period", typed)
        else:
            print("  series_by_period: skip (Oracle trống)")

    except Exception as e:
        print(f"  ERROR: {e}")

    print("\n✅ Sync hoàn tất!")

if __name__ == "__main__":
    main()
