import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name('smart_mirror.db')
JSON_FIELDS = {'symptom_tags_json', 'values_json', 'analysis_json', 'ai_prediction_json', 'lab_values_json', 'lab_analysis_json'}

if not DB_PATH.exists():
    raise SystemExit('smart_mirror.db does not exist yet. Run app.py and save at least one checkup first.')

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

def clean_row(row):
    data = dict(row)
    for key in JSON_FIELDS:
        if key in data and data[key]:
            try:
                data[key.replace('_json', '')] = json.loads(data[key])
            except Exception:
                pass
    return data

for table in ('users', 'lab_results', 'measurement_sessions'):
    print(f'\n=== {table} ===')
    rows = conn.execute(f'SELECT * FROM {table} ORDER BY id DESC LIMIT 10').fetchall()
    if not rows:
        print('(empty)')
        continue
    for row in rows:
        print(clean_row(row))
