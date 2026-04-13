# C2 Performance Dashboard — CLAUDE.md

## Project Architecture

```
Dashboard Project/
├── generate_report.py          # Main script — run this each morning
├── dashboard_app.py            # Streamlit web dashboard — run separately
├── C2-Performance.xlsx         # INPUT: raw shipment data from DB (extracted ~7 AM)
├── 1+D Eligible.xlsx           # INPUT: hubs eligible for next-day delivery
├── performance_history.json    # AUTO: rolling 8-day trend store (generate_report.py)
├── dashboard_history.json      # AUTO: rolling 35-day trend store (dashboard_app.py)
├── C2_Performance_Report_*.xlsx # OUTPUT: daily report (date-stamped)
├── requirements_dashboard.txt  # Web dashboard dependencies
├── CLAUDE.md                   # This file
└── Project_context.md          # Full data model and business logic reference
```

## Key Commands

```bash
# Run the daily Excel report (unchanged)
cd "C:\Users\sneharika.das\Downloads\Dashboard Project"
python generate_report.py

# Run the web dashboard
streamlit run dashboard_app.py

# Install dashboard dependencies (one-time)
pip install -r requirements_dashboard.txt

# Install Excel report dependencies (one-time)
pip install pandas openpyxl
```

## Essential Conventions

- **DC grouping**: always use `origin_dc` column (never `destination_dc`)
- **Performance metric**: `No Breach count / Total count` per DC × service_type
- **Attribution**: derived column — the logic is documented in `Attribution_Logic.md`; see that file before modifying any attribution-related code
- **Client name formatting**: `AJIO_EXPRESS` → `Ajio` (first segment before `_`, capitalized)
- **Report date**: read from `eligible_attempt_date` column (all rows share same date)
- **Tab name limit**: Excel tab names max 31 characters — use `[:31]` when constructing names

## Output Tab Order

1. `{Client}-(DD-MM-YYYY)` — raw data per client
2. `{Client}-Overall` — performance summary per client
3. `C2-Overall` — all clients combined
4. `C2-PDF` — breach breakdown (4 sections: C2-Air, C2-Zonal, Intracity SDD, Intracity NDD)
5. `C2-Pivot` or `{Client}-Pivot` — Attribution % + D-1..D-8 trend (non-AJIO clients)
6. `1+D Eligible Hubs` — hub reference list

## Full Details

See [Project_context.md](Project_context.md) for complete data model, column descriptions,
business rules, breach categories, and future roadmap.

See [Attribution_Logic.md](Attribution_Logic.md) for the full reverse-engineered derivation rules
for all 13 attribution buckets (priority tree, key separating columns, edge cases).

## Dashboard Architecture Notes

- **Performance metric** in the dashboard uses `Attribution == 'No Breach'` (not `breach_category`) — matches `generate_report.py`
- **`dashboard_history.json`** stores richer data than `performance_history.json`: DC-level perf, attribution %, by-service vol — capped at 35 days
- **`performance_history.json`** is managed exclusively by `generate_report.py` (8-day pivot trend); the dashboard never writes to it
- **Auto-log**: click "💾 Log Today's Data" in the Daily Dashboard sidebar each morning after loading the file
- Service type key string: `'Air- Intercity'` (note the space before the hyphen) — do not normalize
