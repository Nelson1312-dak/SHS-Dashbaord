import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'digital_dashboard'))
import queries
print("queries.py path:", queries.__file__)
print("has trading_value_stock:", 'trading_value_stock' in open(queries.__file__, encoding='utf-8').read())

from db import get_engine
from datetime import date
engine = get_engine()
result = queries.trading_kpi(engine, date_from=date(2026,5,1), date_to=date(2026,5,29))
print("trading_kpi result:")
for k, v in result.items():
    print(f"  {k}: {v}")
