# C2 Performance Dashboard — Project Context

See [CLAUDE.md](CLAUDE.md) for architecture and commands.

---

## Business Context

Daily logistics delivery performance tracking for the **C2 client group** (fashion/e-commerce clients
using Shadowfax last-mile delivery). Currently AJIO_EXPRESS is the only active client; more will be added.

The report is extracted from the DB at **7 AM** each morning, processed by `generate_report.py`,
and sent via email to stakeholders (email automation is a future step).

---

## Input Files

### C2-Performance.xlsx
- **Sheet**: `Sheet1`
- **Rows**: ~10,000+ per day (one row per shipment)
- **Columns**: 116 (see key columns below)
- **eligible_attempt_date**: the report date — ALL rows share the same date (today's report)

Key columns:
| Column | Description |
|--------|-------------|
| `eligible_attempt_date` | Report date (all rows same date) |
| `picked_date` | When shipment was picked up from seller |
| `awb_number` | Shipment ID |
| `client_name` | e.g., `AJIO_EXPRESS` |
| `origin_dc` | **Use this for DC grouping** (not destination_dc) |
| `destination_dc` | Delivery DC |
| `hub` | Last-mile hub |
| `service_type` | One of 5 types (see below) |
| `order_type` | NDD or SDD |
| `order_status` | DELIVERED, RECEIVED_AT_HUB, IN_MANIFEST, etc. |
| `breach_category` | No Breach / LM Breach / MM Breach (from DB) |
| `Attribution` | **Root-cause label** — pre-computed by DB, used as-is |
| `odc_manifest_status` | ODC manifest status (IN_TRANSIT, CLOSED, etc.) |
| `relative_attempt_date` | D0, D1, D2, D3 (attempt day relative to eligible date) |
| `pendency_flag` | 1 = shipment still pending delivery |
| `at_lm_hub_flag` | 1 = shipment is at last-mile hub |
| `ofd_flag` | 1 = out for delivery |

### 1+D Eligible.xlsx
- Single column: hub names eligible for 1+D (next-day) delivery
- Used to flag `Attribution = '1+ Day Eligible'` in the source system
- Currently ~103 hubs

---

## Service Types (5 categories)

| Service Type | Description |
|---|---|
| `Air- Intercity` | Air freight, inter-city delivery |
| `Intracity NDD` | Same-city, next-day delivery |
| `Intracity SDD` | Same-city, same-day delivery |
| `Zonal + Air- Intercity` | Zonal with air component |
| `Zonal NDD` | Zonal, next-day delivery |

---

## Attribution Categories (root-cause labels)

Attribution is **pre-computed by the database** and stored in the `Attribution` column.
Do not recompute it. The values are:

| Attribution | Meaning |
|---|---|
| `No Breach` | Delivered on time, no issue |
| `ODC Connection miss` | Outbound DC manifest connection missed |
| `DDC Connection miss` | Destination DC manifest connection missed |
| `AH-Intransit` | Air hub still in transit |
| `Air offload` | Shipment offloaded from air to surface |
| `Retrieval Delay` | ODC manifest in transit, retrieval delayed |
| `JIT/AD miss` | Just-in-time / arrival deadline missed (Zonal NDD) |
| `Surface Tagging` | Incorrectly tagged as surface instead of air |
| `1st MR miss` | First milk-run missed (Intracity NDD) |
| `Pending LM Inscan` | Shipment in manifest, not yet inscanned at LM hub |
| `Hub Capping` | Hub volume capped (manual — e.g., BOM_Chembur) |
| `MKT Breach` | Market breach |
| `RTO` | Return to origin |
| `1+ Day Eligible` | Hub is on the 1+D eligible list (next-day is acceptable) |

---

## C2-PDF Sections (breach breakdown sheet)

Four sections placed horizontally, separated by blank columns:

| Section | Service Types Covered | Breach Columns |
|---|---|---|
| C2-Air | Air- Intercity | No Breach, ODC miss, DDC miss, AH-Intransit, Air offload, Surface Tagging, MKT Breach, Retrieval Delay, Non Eligible, RTO, Pending LM Inscan, 1+Day Eligible |
| C2-Zonal | Zonal NDD, Zonal + Air- Intercity | No Breach, ODC miss, DDC miss, JIT/AD miss, MKT Breach, Non Eligible, RTO, Pending LM Inscan, 1+Day Eligible |
| Intracity SDD | Intracity SDD | No Breach, DDC miss, Pending LM Inscan, 1+Day Eligible |
| Intracity NDD | Intracity NDD | No Breach, 1st MR miss, DDC miss, MKT Breach, Post Cutoff, RTO, Pending LM Inscan |

Values = breach_type_count / DC_total (percentage share of each failure reason).

---

## D-1 to D-8 Performance Trend

The Pivot sheet right half shows a rolling 8-day performance trend.

- **D-1** = current run's performance (today)
- **D-2 to D-8** = loaded from `performance_history.json` (previous daily runs)
- Each daily run prepends today's entry and keeps the last 8 entries
- On first run: only D-1 is populated; trend fills up over subsequent days

---

## C2 vs AJIO Tabs

- **C2** = the overall program name (all clients in the input file)
- `C2-Overall` and `C2-PDF` = all clients combined
- Each client gets its own dedicated raw data tab + Overall tab
- `C2-Pivot` = attribution breakdown for **non-AJIO** clients
  - Falls back to all clients when AJIO is the only client

---

## Verified Numbers (09-04-2026 sample)

| DC | Service | Vol | Perf |
|---|---|---|---|
| Bangalore DC | Air-Intercity | 2,239 | 96.96% |
| NCR Bamnoli DC | Air-Intercity | 1,097 | 84.14% |
| Grand Total | All | 10,408 | 96.21% |

---

## Future Roadmap

1. **Email automation**: send the generated report by email each morning after the 7 AM DB extract
2. **More clients**: Nykaa, Myntra, etc. will be added to the input file — system handles this automatically
3. **Streamlit/web dashboard**: potential upgrade from Excel to interactive web dashboard
4. **Scheduling**: automate the DB extract + report generation + email via a cron/Task Scheduler job
