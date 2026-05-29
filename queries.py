# ============================================================
#  QUERIES.PY — v3 (Fix Timestamp & Kwargs)
#  Tất cả query nhận date_from, date_to để filter đúng kỳ
# ============================================================
import pandas as pd
import sqlalchemy
from datetime import date

def run(engine, sql, params=None):
    with engine.connect() as conn:
        return pd.read_sql(sqlalchemy.text(sql), conn, params=params)

# ── Helper: ngày mặc định nếu không truyền ──
def _today(): return date.today()
def _month_start(): d=_today(); return date(d.year,d.month,1)

# ──────────────────────────────────────────────
#  OVERVIEW
# ──────────────────────────────────────────────

def kpi_summary(engine, snapshot_date=None, **kwargs):
    """KPI tổng hợp — snapshot tại ngày chỉ định (mặc định: hôm nay)"""
    d = snapshot_date or _today()
    sql = """
        SELECT
            COUNT(DISTINCT c.CUSTID)                                             AS total_customer,
            -- Đã bọc '1' trong nháy đơn để an toàn với cả dữ liệu số lẫn chuỗi
            COUNT(DISTINCT CASE WHEN c.IS_ACTIVE_PUB = '1' THEN c.CUSTID END)    AS active_customer,
            ROUND(SUM(a.NAV)            / 1e9, 1)                                AS total_nav_bil,
            ROUND(SUM(a.AUM)            / 1e9, 1)                                AS total_aum_bil,
            ROUND(SUM(a.MARGIN_BALANCE) / 1e9, 1)                                AS total_margin_bil
        FROM DIM_CUSTOMER c
        LEFT JOIN FACT_DAILY_CUST_ASSET_MGMT a
            ON  a.CUSTID = c.CUSTID
            -- Ép kiểu TRUNC để bỏ phần timestamp giờ-phút-giây
            AND TRUNC(a.DT) = (
                SELECT MAX(TRUNC(DT)) FROM FACT_DAILY_CUST_ASSET_MGMT
                WHERE TRUNC(DT) <= :snap_date
            )
    """
    return run(engine, sql, {"snap_date": d}).iloc[0].to_dict()


def monthly_new_accounts(engine, date_from=None, date_to=None, **kwargs):
    """Mở TK mới theo Branch trong kỳ (join FACT_DAILY_CUST_BROKER_MAPPING qua CUSTID)"""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT
            TO_CHAR(TRUNC(f.DT, 'MM'), 'MM/YYYY')    AS month_label,
            NVL(b.BRANCH, 'Khác')                     AS channel,
            COUNT(DISTINCT f.CUSTID)                   AS new_accounts
        FROM FACT_FIRST_DATE_CUST_OPEN f
        LEFT JOIN (
            SELECT CUSTID, BRANCH
            FROM (
                SELECT CUSTID, BRANCH,
                       ROW_NUMBER() OVER (PARTITION BY CUSTID ORDER BY DT DESC) AS rn
                FROM FACT_DAILY_CUST_BROKER_MAPPING
            )
            WHERE rn = 1
        ) b ON f.CUSTID = b.CUSTID
        WHERE TRUNC(f.DT) BETWEEN :df AND :dt
        GROUP BY TRUNC(f.DT, 'MM'), NVL(b.BRANCH, 'Khác')
        ORDER BY TRUNC(f.DT, 'MM'), NVL(b.BRANCH, 'Khác')
    """
    df2 = run(engine, sql, {"df": df, "dt": dt})
    if df2.empty:
        return {"labels": [], "channels": {}}
    df2.columns = [c.upper() for c in df2.columns]
    pivot = df2.pivot_table(index="MONTH_LABEL", columns="CHANNEL",
                            values="NEW_ACCOUNTS", aggfunc="sum", fill_value=0)
    return {"labels": pivot.index.tolist(),
            "channels": {col: pivot[col].tolist() for col in pivot.columns}}


def monthly_active_rate(engine, date_from=None, date_to=None, **kwargs):
    """Tỉ lệ KH active theo tháng trong kỳ"""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT
            TO_CHAR(TRUNC(a.DT, 'MM'), 'MM/YYYY')                              AS month_label,
            COUNT(DISTINCT a.CUSTID)                                               AS total_kh,
            COUNT(DISTINCT CASE WHEN c.IS_ACTIVE_PUB = '1' THEN a.CUSTID END)      AS active_kh
        FROM FACT_DAILY_CUST_ASSET_MGMT a
        JOIN DIM_CUSTOMER c ON c.CUSTID = a.CUSTID
        WHERE TRUNC(a.DT) BETWEEN :df AND :dt
          AND TRUNC(a.DT) = LAST_DAY(TRUNC(a.DT))
        GROUP BY TRUNC(a.DT, 'MM')
        ORDER BY TRUNC(a.DT, 'MM')
    """
    df2 = run(engine, sql, {"df": df, "dt": dt})
    if df2.empty:
        return {"labels": [], "rates": []}
    df2.columns = [c.upper() for c in df2.columns]
    df2["RATE"] = (df2["ACTIVE_KH"] / df2["TOTAL_KH"].replace(0,1) * 100).round(1)
    return {"labels": df2["MONTH_LABEL"].tolist(), "rates": df2["RATE"].tolist()}


def monthly_cashflow(engine, date_from=None, date_to=None, **kwargs):
    """Net cash flow lũy kế theo tháng trong kỳ"""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT
            TO_CHAR(TRUNC(t.DT, 'MM'), 'MM/YYYY')                    AS month_label,
            ROUND(SUM(t.TOTAL_CASH_IN)  / 1e9, 1)                        AS cash_in,
            ROUND(SUM(t.TOTAL_CASH_OUT) / 1e9, 1)                        AS cash_out,
            ROUND((SUM(t.TOTAL_CASH_IN)-SUM(t.TOTAL_CASH_OUT)) / 1e9, 1) AS net
        FROM FACT_DAILY_CUST_TRADING_MGMT t
        WHERE TRUNC(t.DT) BETWEEN :df AND :dt
        GROUP BY TRUNC(t.DT, 'MM')
        ORDER BY TRUNC(t.DT, 'MM')
    """
    df2 = run(engine, sql, {"df": df, "dt": dt})
    if df2.empty:
        return {"labels": [], "cash_in": [], "cash_out": [], "net": []}
    df2.columns = [c.upper() for c in df2.columns]
    return {"labels":   df2["MONTH_LABEL"].tolist(),
            "cash_in":  df2["CASH_IN"].tolist(),
            "cash_out": (-df2["CASH_OUT"]).tolist(),
            "net":      df2["NET"].tolist()}


def nav_trend(engine, date_from=None, date_to=None, **kwargs):
    """NAV cuối tháng trong kỳ"""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT
            TO_CHAR(TRUNC(a.DT, 'MM'), 'MM/YYYY') AS month_label,
            ROUND(SUM(a.NAV) / 1e9, 1)               AS total_nav
        FROM FACT_DAILY_CUST_ASSET_MGMT a
        WHERE TRUNC(a.DT) BETWEEN :df AND :dt
          AND TRUNC(a.DT) = LAST_DAY(TRUNC(a.DT))
        GROUP BY TRUNC(a.DT, 'MM')
        ORDER BY TRUNC(a.DT, 'MM')
    """
    df2 = run(engine, sql, {"df": df, "dt": dt})
    if df2.empty:
        return {"labels": [], "nav": []}
    df2.columns = [c.upper() for c in df2.columns]
    return {"labels": df2["MONTH_LABEL"].tolist(), "nav": df2["TOTAL_NAV"].tolist()}


# ──────────────────────────────────────────────
#  TRADING
# ──────────────────────────────────────────────

def trading_kpi(engine, date_from=None, date_to=None, **kwargs):
    """KPI giao dịch lũy kế trong kỳ"""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT
            ROUND(SUM(t.TRADING_VALUE)     / 1e9, 1) AS gtgd_bil,
            ROUND(SUM(t.TRADING_FEE_NET)   / 1e6, 0) AS fee_mil,
            SUM(t.DERIVATIVE_VOL)                      AS derivative_vol
        FROM FACT_DAILY_CUST_TRADING_MGMT t
        WHERE TRUNC(t.DT) BETWEEN :df AND :dt
    """
    res = run(engine, sql, {"df": df, "dt": dt}).iloc[0].to_dict()
    m_res = margin_interest_kpi(engine, date_from=df, date_to=dt)
    res["deriv_fee_mil"] = m_res.get("margin_interest_mil") or 0
    return res


def margin_interest_kpi(engine, date_from=None, date_to=None, **kwargs):
    """Lãi margin thực thu lũy kế trong kỳ = SUM(MARGIN_xxx_INTEREST_NET)"""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT
            ROUND(SUM(NVL(a.MARGIN_NORMAL_INTEREST_NET, 0)
                    + NVL(a.MARGIN_3B_INTEREST_NET,    0)
                    + NVL(a.MARGIN_UT_INTEREST_NET,    0)) / 1e6, 0) AS margin_interest_mil
        FROM FACT_DAILY_CUST_ASSET_MGMT a
        WHERE TRUNC(a.DT) BETWEEN :df AND :dt
    """
    return run(engine, sql, {"df": df, "dt": dt}).iloc[0].to_dict()


def market_share(engine, date_from=None, date_to=None, **kwargs):
    """Thị phần GTGD — lấy tháng gần nhất trong kỳ"""
    dt = date_to or _today()
    sql = """
        SELECT m.ENTITY,
               ROUND(m.TOTAL_TRADING_VALUE / 1e9, 0) AS gtgd_bil
        FROM FACT_MARKET_REF m
        WHERE TRUNC(m.MONTH_DATE) = (
            SELECT MAX(TRUNC(MONTH_DATE)) FROM FACT_MARKET_REF
            WHERE TRUNC(MONTH_DATE) <= :dt
        )
        ORDER BY m.TOTAL_TRADING_VALUE DESC
        FETCH FIRST 10 ROWS ONLY
    """
    df2 = run(engine, sql, {"dt": dt})
    if df2.empty:
        return {"entities": [], "values": []}
    df2.columns = [c.upper() for c in df2.columns]
    return {"entities": df2["ENTITY"].tolist(), "values": df2["GTGD_BIL"].tolist()}


def trading_by_channel(engine, date_from=None, date_to=None, **kwargs):
    """GTGD và phí theo Team (join FACT_DAILY_CUST_BROKER_MAPPING qua CUSTID).
    Dùng subquery ROW_NUMBER() để lấy 1 dòng mới nhất per CUSTID, tránh fan-out
    khi FACT_DAILY_CUST_BROKER_MAPPING có nhiều dòng per CUSTID (snapshot hàng ngày)."""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT
            NVL(b.TEAM, 'Khác')                                                      AS channel,
            ROUND(SUM(t.TRADING_VALUE) / 1e9, 1)                                    AS gtgd_bil,
            ROUND(SUM(t.TRADING_FEE_NET)/NULLIF(SUM(t.TRADING_VALUE),0)*10000, 2)   AS fee_bps
        FROM FACT_DAILY_CUST_TRADING_MGMT t
        LEFT JOIN (
            SELECT CUSTID, TEAM
            FROM (
                SELECT CUSTID, TEAM,
                       ROW_NUMBER() OVER (PARTITION BY CUSTID ORDER BY DT DESC) AS rn
                FROM FACT_DAILY_CUST_BROKER_MAPPING
            )
            WHERE rn = 1
        ) b ON t.CUSTID = b.CUSTID
        WHERE TRUNC(t.DT) BETWEEN :df AND :dt
          AND t.TRADING_VALUE > 0
        GROUP BY NVL(b.TEAM, 'Khác')
        ORDER BY SUM(t.TRADING_VALUE) DESC
    """
    df2 = run(engine, sql, {"df": df, "dt": dt})
    if df2.empty:
        return {"channels": [], "gtgd": [], "fee_bps": []}
    df2.columns = [c.upper() for c in df2.columns]
    return {"channels": df2["CHANNEL"].tolist(),
            "gtgd":     df2["GTGD_BIL"].tolist(),
            "fee_bps":  df2["FEE_BPS"].tolist()}


def margin_trend(engine, date_from=None, date_to=None, **kwargs):
    """Dư nợ margin cuối tháng trong kỳ (snapshot)"""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT
            TO_CHAR(TRUNC(a.DT, 'MM'), 'MM/YYYY')  AS month_label,
            ROUND(SUM(a.MARGIN_NORMAL) / 1e9, 1)        AS normal,
            ROUND(SUM(a.MARGIN_3B)     / 1e9, 1)        AS m3b,
            ROUND(SUM(a.MARGIN_UT)     / 1e9, 1)        AS ut
        FROM FACT_DAILY_CUST_ASSET_MGMT a
        WHERE TRUNC(a.DT) BETWEEN :df AND :dt
          AND TRUNC(a.DT) = LAST_DAY(TRUNC(a.DT))
        GROUP BY TRUNC(a.DT, 'MM')
        ORDER BY TRUNC(a.DT, 'MM')
    """
    df2 = run(engine, sql, {"df": df, "dt": dt})
    if df2.empty:
        return {"labels": [], "normal": [], "m3b": [], "ut": []}
    df2.columns = [c.upper() for c in df2.columns]
    return {"labels": df2["MONTH_LABEL"].tolist(),
            "normal": df2["NORMAL"].tolist(),
            "m3b":    df2["M3B"].tolist(),
            "ut":     df2["UT"].tolist()}


# ──────────────────────────────────────────────
#  BROKER
# ──────────────────────────────────────────────

def broker_kpi(engine, snapshot_date=None, **kwargs):
    """KPI môi giới — snapshot tại ngày chỉ định"""
    d = snapshot_date or _today()
    sql = """
        SELECT
            COUNT(DISTINCT b.BROKER_ID)                                         AS total_broker,
            ROUND(COUNT(DISTINCT c.CUSTID)/NULLIF(COUNT(DISTINCT b.BROKER_ID),0),0) AS avg_kh_per_broker,
            ROUND(SUM(a.NAV)/NULLIF(COUNT(DISTINCT b.BROKER_ID),0)/1e9,1)       AS avg_nav_per_broker
        FROM FACT_DAILY_CUST_BROKER_MAPPING b
        JOIN DIM_CUSTOMER c ON c.CUSTID = b.CUSTID
        LEFT JOIN FACT_DAILY_CUST_ASSET_MGMT a
            ON  a.CUSTID = b.CUSTID
            AND TRUNC(a.DT) = (SELECT MAX(TRUNC(DT)) FROM FACT_DAILY_CUST_ASSET_MGMT WHERE TRUNC(DT) <= :d)
        WHERE TRUNC(b.DT) = (SELECT MAX(TRUNC(DT)) FROM FACT_DAILY_CUST_BROKER_MAPPING WHERE TRUNC(DT) <= :d)
    """
    return run(engine, sql, {"d": d}).iloc[0].to_dict()


def broker_top10(engine, date_from=None, date_to=None, **kwargs):
    """Top 10 MG theo doanh thu lũy kế trong kỳ"""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT b.BROKER_ID, b.BROKER_NAME,
            ROUND((SUM(t.TRADING_FEE_NET) + SUM(t.DERIVATIVE_FEE_NET)
                   + SUM(a.MARGIN_NORMAL_INTEREST_NET)
                   + SUM(a.MARGIN_3B_INTEREST_NET)
                   + SUM(a.MARGIN_UT_INTEREST_NET)) / 1e6, 1) AS total_revenue_mil
        FROM FACT_DAILY_CUST_BROKER_MAPPING b
        JOIN FACT_DAILY_CUST_TRADING_MGMT t
            ON t.CUSTID = b.CUSTID AND TRUNC(t.DT) = TRUNC(b.DT)
        JOIN FACT_DAILY_CUST_ASSET_MGMT a
            ON a.CUSTID = b.CUSTID AND TRUNC(a.DT) = TRUNC(b.DT)
        WHERE TRUNC(b.DT) BETWEEN :df AND :dt
        GROUP BY b.BROKER_ID, b.BROKER_NAME
        ORDER BY total_revenue_mil DESC
        FETCH FIRST 10 ROWS ONLY
    """
    df2 = run(engine, sql, {"df": df, "dt": dt})
    if df2.empty:
        return {"brokers": [], "revenues": []}
    df2.columns = [c.upper() for c in df2.columns]
    return {"brokers":  df2["BROKER_NAME"].fillna(df2["BROKER_ID"]).tolist(),
            "revenues": df2["TOTAL_REVENUE_MIL"].tolist()}


def broker_by_center(engine, snapshot_date=None, **kwargs):
    """KH active theo Branch — snapshot"""
    d = snapshot_date or _today()
    sql = """
        SELECT b.BRANCH AS CENTER,
            COUNT(DISTINCT CASE WHEN c.IS_ACTIVE_PUB = '1' THEN b.CUSTID END) AS active_kh
        FROM FACT_DAILY_CUST_BROKER_MAPPING b
        JOIN DIM_CUSTOMER c ON c.CUSTID = b.CUSTID
        WHERE TRUNC(b.DT) = (SELECT MAX(TRUNC(DT)) FROM FACT_DAILY_CUST_BROKER_MAPPING WHERE TRUNC(DT) <= :d)
          AND b.BRANCH IS NOT NULL
        GROUP BY b.BRANCH
        ORDER BY active_kh DESC
    """
    df2 = run(engine, sql, {"d": d})
    if df2.empty:
        return {"centers": [], "values": []}
    df2.columns = [c.upper() for c in df2.columns]
    return {"centers": df2["CENTER"].tolist(), "values": df2["ACTIVE_KH"].tolist()}


def broker_by_type(engine, date_from=None, date_to=None, **kwargs):
    """KH mở mới theo loại MG trong kỳ"""
    df = date_from or _month_start()
    dt = date_to   or _today()
    sql = """
        SELECT b.BROKER_TYPE, COUNT(DISTINCT f.CUSTID) AS new_kh
        FROM FACT_FIRST_DATE_CUST_OPEN f
        JOIN FACT_DAILY_CUST_BROKER_MAPPING b
            ON b.CUSTID = f.CUSTID AND TRUNC(b.DT) = TRUNC(f.DT)
        WHERE TRUNC(f.DT) BETWEEN :df AND :dt
          AND b.BROKER_TYPE IS NOT NULL
        GROUP BY b.BROKER_TYPE
        ORDER BY new_kh DESC
    """
    df2 = run(engine, sql, {"df": df, "dt": dt})
    if df2.empty:
        return {"types": [], "values": []}
    df2.columns = [c.upper() for c in df2.columns]
    return {"types": df2["BROKER_TYPE"].tolist(), "values": df2["NEW_KH"].tolist()}