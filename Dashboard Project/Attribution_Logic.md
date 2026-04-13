# Attribution Column — Derivation Logic

The `Attribution` column is **derived** from a combination of raw DB columns. It is not a passthrough.
It encodes the root-cause for each shipment's delivery status through a **priority decision tree**:

1. Cross-cutting pre-filters (RTO, 1+ Day Eligible)
2. Branch by `breach_category` (No Breach / LM Breach / MM Breach)
3. Sub-conditions by `service_type`, manifest flags, timing, and hub identity

---

## Key Source Columns

| Column | Role |
|---|---|
| `breach_category` | Coarse bucket: No Breach / LM Breach / MM Breach |
| `service_type` | Air- Intercity / Intracity NDD / Intracity SDD / Zonal NDD / Zonal + Air- Intercity |
| `ideal_service_type` | What the AWB *should* have been routed as: PRIME_AIR_PRIORITY, NEXT_DAY_DELIVERY, SURFACE_PRIORITY, SAME_DAY_DELIVERY |
| `actual_mode_of_travel` | How it actually moved: AIR / SURFACE |
| `order_status` | DELIVERED, RECEIVED_AT_HUB, IN_MANIFEST, ASSIGNED, IN_RETURN_PROCESS, etc. |
| `odc_manifest_status` | Status of the ODC (Origin DC) manifest: IN_TRANSIT, CLOSED, RECEIVED_AT_DC, IN_MM, NEW_CREATED, etc. |
| `odc_manifest_intransit` | Timestamp when Origin DC manifest went in-transit (**NULL = never dispatched**) |
| `hub_manifest_status` | Status of the hub-bound manifest: IN_TRANSIT, RECEIVED_AT_DC, IN_MM, MISROUTED, RECEIVED_AT_HUB, PARTIALLY_CLOSED, CLOSED, NEW_CREATED |
| `ddc_manifest_intransit` | Timestamp when Destination DC→hub manifest went in-transit |
| `recd_at_dest_dc` | Timestamp when AWB was received at the destination DC |
| `lpt_lh_tat` | Linehaul TAT in days (1 = same-day, 2 = 2-day, 3+ = delayed) |
| `pendency_flag` | 1 = shipment still pending delivery |
| `rto_flag` | 1 = shipment is in return-to-origin process |
| `hub` | Last-mile hub name |
| `destination_dc` | Destination DC name |
| `milk_run` | Milk run slot: 1MR (1st run), 2MR, 3MR |
| `first_ofd_date` | Timestamp of first out-for-delivery event |
| `hub_1d_eligible` | Derived: 1 if hub is in the 1+D Eligible hub list |

---

## Step 0: Cross-Cutting Pre-Filters (Checked Before Everything Else)

### RTO
```
rto_flag = 1
```
Shipment is being returned to origin. `order_status = IN_RETURN_PROCESS`. Takes priority over all other attributions.

### 1+ Day Eligible
```
hub_1d_eligible = 1  (hub is in the 1+D Eligible hub list)
AND pendency_flag = 1
```
Hub is on the 1+D list — next-day delivery is contractually acceptable. Overrides all breach attributions for pending shipments at these hubs. The 1+D Eligible list (~103 hubs) is stored in `1+D Eligible.xlsx`.

---

## Step 1: breach_category = 'No Breach'

### No Breach (default)
Most delivered-on-time shipments. Condition: no RTO, no 1+D override, not a breach.

### 1st MR miss (within No Breach)
```
service_type = 'Intracity NDD'
AND milk_run = '1MR'
AND HOUR(first_ofd_date) >= 17
```
Shipment WAS delivered (order_status = DELIVERED or NOT_CONTACTABLE) but only during the 2nd milk run window (OFD time ≥ 17:00). Counted as No Breach since it was delivered; flagged as 1st MR miss for analysis. The 1st milk run was missed but recovery was made on the 2nd run.

---

## Step 2: breach_category = 'LM Breach'

LM Breach = failure is in the **last-mile leg** (hub → customer).

### No Breach (actionable at hub)
```
order_status IN ('RECEIVED_AT_HUB', 'ASSIGNED', 'PINCODE_UPDATED')
AND pendency_flag = 1
```
~595 cases. Shipment is physically at the hub and still being worked on. Breach category is LM Breach (past the promised date), but Attribution is No Breach because there is no specific failure — the shipment is live and may still be delivered.

### Hub Capping (LM)
```
hub IN capped_hub_list   (currently: BOM_Chembur)
AND order_status = 'IN_MANIFEST'
```
Hub is volume-capped (manually maintained list in the DB). Shipments stuck in a manifest at this hub are attributed to Hub Capping, not to a routing failure. Different hubs can be added/removed from this list.

### 1st MR miss (LM Breach — pending)
```
service_type = 'Intracity NDD'
AND milk_run = '1MR'
AND order_status = 'IN_MANIFEST'
```
Shipment is still pending (not yet delivered) and was assigned to the 1st milk run which has already passed. The 2nd run is the only option now.

---

### IN_MANIFEST shipments — by hub_manifest_status

#### hub_manifest_status = 'RECEIVED_AT_DC'
The hub manifest is received at the destination DC but not yet dispatched to the hub.
```
hub IN capped_hub_list         → Hub Capping
hub IN eligible_hubs           → 1+ Day Eligible   (pre-filter, Step 0)
otherwise                      → DDC Connection miss
```

#### hub_manifest_status = 'IN_MM'
Hub manifest is in middle-mile routing.
```
hub IN eligible_hubs           → 1+ Day Eligible   (pre-filter)
otherwise                      → DDC Connection miss
```

#### hub_manifest_status = 'MISROUTED'
Hub manifest has been misrouted.
```
ideal_service_type = 'NEXT_DAY_DELIVERY'    → DDC Connection miss   (Zonal NDD routes)
ideal_service_type = 'PRIME_AIR_PRIORITY'   → Pending LM Inscan     (Air-Intercity routes)
```
For Zonal NDD: misrouting = wrong DDC connection. For Air-Intercity: misrouting = AWB not properly inscanned at hub.

#### hub_manifest_status = 'RECEIVED_AT_HUB'
Manifest received at hub, but AWB still IN_MANIFEST (not individually inscanned yet).
```
service_type = 'Intracity NDD' AND milk_run = '1MR'   → 1st MR miss
otherwise                                               → Pending LM Inscan
```

#### hub_manifest_status = 'PARTIALLY_CLOSED'
```
→ Pending LM Inscan
```

#### hub_manifest_status = 'IN_TRANSIT'
Hub-bound manifest is currently en route. Key sub-conditions:

**Sub-case A: odc_manifest_status = 'IN_TRANSIT'**
```
DATE(ddc_manifest_intransit) > report_date   → DDC Connection miss
    The DDC→hub manifest goes out TOMORROW — too late for today's delivery.

DATE(ddc_manifest_intransit) <= report_date  → Pending LM Inscan
    The DDC→hub manifest dispatched TODAY or earlier — in transit, just not arrived at hub yet.
```
This is the **key separator** for the Pending LM Inscan vs DDC Connection miss cases in LM Breach.

**Sub-case B: odc_manifest_status = 'CLOSED'**
```
odc_manifest_intransit IS NOT NULL   → DDC Connection miss
    The ODC manifest completed its journey (went in transit AND closed).
    The failure is at the DDC→hub leg.

odc_manifest_intransit IS NULL       → ODC Connection miss
    The ODC manifest was created/closed but NEVER went in transit.
    The ODC handoff itself failed — the shipment never left the origin DC properly.
```

---

## Step 3: breach_category = 'MM Breach'

MM Breach = failure is in the **mid-mile leg** (Origin DC → Destination DC / air hubs).

---

### For service_type = 'Air- Intercity' (and 'Zonal + Air- Intercity')

> **Critical insight**: `DATE(odc_manifest_intransit)` relative to the report date is the primary separator for most Air-Intercity MM Breach attributions.

#### Hub Capping (MM)
```
hub IN capped_hub_list   (currently: BOM_Chembur, PNQ_Manjari_E)
```
Applies across all service types and manifest states. Hub is volume-capped.

#### Surface Tagging
```
ideal_service_type = 'SURFACE_PRIORITY'
AND odc_manifest_status = 'IN_TRANSIT'
```
The AWB should have been routed as surface (SURFACE_PRIORITY) but was incorrectly inducted into an Air-Intercity service. Root cause: wrong service tagging at origin.

#### AH-Intransit (Air Hub In Transit)
```
actual_mode_of_travel = 'AIR'
AND hub_manifest_status = 'IN_TRANSIT'
AND lpt_lh_tat >= 3
AND DATE(odc_manifest_intransit) < report_date
```
Shipment is in an air manifest that went in transit **before** the report date and is still in transit. It's been sitting in the air hub for multiple days without forwarding. Root cause: air hub transit congestion or delay.

#### Air offload
```
actual_mode_of_travel = 'AIR'
AND (
    -- Case A: ODC manifest dispatched today → just now offloaded
    odc_manifest_status = 'IN_TRANSIT'
    AND hub_manifest_status = 'IN_TRANSIT'
    AND DATE(odc_manifest_intransit) = report_date
)
OR (
    -- Case B: ODC completed, manifest received at DC surface side
    odc_manifest_status IN ('RECEIVED_AT_DC', 'IN_MM')
    AND hub_manifest_status = 'RECEIVED_AT_DC'
)
OR (
    -- Case C: ODC completed, new surface hub manifest created
    odc_manifest_status = 'CLOSED'
    AND hub_manifest_status IN ('RECEIVED_AT_DC', 'NEW_CREATED')
    AND DATE(odc_manifest_intransit) = report_date
)
```
AIR shipment was offloaded from its air route and placed into surface routing. Root cause: air capacity constraint or operational offload. The distinguishing marker is that the ODC manifest went in transit **on the report date** (Case A, C) or the manifest was received at the DC surface-side (Case B).

#### Retrieval Delay
```
actual_mode_of_travel = 'AIR'
AND odc_manifest_status = 'IN_TRANSIT'
AND hub_manifest_status IS NULL   (no hub manifest yet)
AND DATE(odc_manifest_intransit) < report_date
```
Shipment moved by air, ODC manifest is still in transit (went in transit **before** the report date, so it's been in the system 1+ days), but the AWB has not yet been inducted into any hub-bound manifest. Root cause: delayed retrieval from the air hub — the air hub received the manifest but hasn't yet processed the shipment onward.

#### ODC Connection miss (Air)
```
actual_mode_of_travel = 'AIR'
AND odc_manifest_status = 'IN_TRANSIT'
AND hub_manifest_status IS NULL
AND DATE(odc_manifest_intransit) = report_date
```
ODC manifest just went in transit **today** — the shipment has barely started its linehaul journey and cannot make today's delivery. Root cause: ODC dispatched the manifest too late or the connection window was missed at origin.

#### Pending LM Inscan (Air MM)
```
odc_manifest_status = 'CLOSED'
AND hub_manifest_status IS NULL
```
ODC manifest completed its journey but no hub manifest exists yet. The shipment is staged at the destination DC awaiting hub induction. Root cause: not yet inscanned into a hub-bound manifest at the DC.

#### DDC Connection miss (Air MM)
```
DATE(recd_at_dest_dc) = report_date
```
Primary condition: shipment arrived at the Destination DC **on the report date**. It made the linehaul but arrived too late to make the same-day hub dispatch cutoff. Root cause: linehaul completed on time but arrival at DC was after the DDC→hub dispatch window.

**Edge cases** (small volume): Some DDC Connection miss cases have `recd_at_dest_dc` as null or future-dated but still have odc_manifest completed — these typically involve high-lpt (≥5 days) or special routing scenarios.

#### ODC Connection miss (Air — default)
```
odc_manifest_status NOT CLOSED
AND recd_at_dest_dc IS NULL or > report_date
AND actual_mode_of_travel = 'SURFACE'   (surface-routed air shipments)
```
The shipment has not arrived at the destination DC yet. Root cause: the ODC→DDC linehaul failed or is delayed beyond the eligible attempt date.

---

### For service_type = 'Zonal NDD'

#### JIT/AD miss (Just-In-Time / Arrival Deadline miss)
```
destination_dc IN ('Ajmer DC', 'Amritsar DC', 'Panipat DC')
AND service_type = 'Zonal NDD'
AND actual_mode_of_travel = 'SURFACE'
```
Specific destination DCs operate on strict Just-In-Time surface transport schedules with tight arrival deadline windows. Missing the scheduled truck window means the shipment cannot be delivered by the eligible date. Root cause: dispatch missed the JIT truck window for these specific routes.

Note: some shipments at these DCs are still flagged as 1+ Day Eligible (pre-filter overrides JIT/AD miss for those hubs).

#### DDC Connection miss (Zonal NDD)
```
odc_manifest_status = 'CLOSED'
AND hub_manifest_status IN ('IN_TRANSIT', 'IN_MM', 'RECEIVED_AT_DC')
```
ODC leg completed; the failure is at the DDC→hub connection. Manifest exists but hasn't reached the hub in time.

#### Pending LM Inscan (Zonal NDD)
```
odc_manifest_status = 'CLOSED'
AND hub_manifest_status IS NULL
```
ODC leg completed but no hub manifest yet. Staged at the DC.

#### ODC Connection miss (Zonal NDD — default)
```
Remaining Zonal NDD MM Breach cases
(typically odc_manifest_status = 'IN_TRANSIT' or 'RECEIVED_AT_HUB'
 without a DDC-level manifest)
```
Failure in the ODC→DDC mid-mile leg. The shipment has not yet completed the origin DC to destination DC journey.

---

### For service_type = 'Intracity SDD' (Same-Day Delivery)

All MM Breach cases are IN_MANIFEST with `recd_at_dest_dc = report_date` (arrived at DC today).

#### DDC Connection miss (Intracity SDD)
```
odc_manifest_status IN ('IN_TRANSIT', 'IN_MM')
AND hub_manifest_status IN ('IN_TRANSIT', 'IN_MM')
```
Shipment arrived at DC today and is in a hub-bound manifest, but the same-day delivery cutoff has passed.

#### Pending LM Inscan (Intracity SDD)
```
odc_manifest_status IN ('NEW_CREATED')
OR hub_manifest_status IN ('NEW_CREATED')
```
A manifest has been created but not yet dispatched. Shipment is pending hub induction.

---

### For service_type = 'Intracity NDD' (Next-Day, Same-City)

#### Pending LM Inscan (Intracity NDD)
Most MM Breach cases. Shipment is IN_MANIFEST with hub not yet inscanned. Various manifest states.

---

## Summary: Priority Decision Tree

```
Attribution:

  IF rto_flag = 1
      → RTO

  ELSE IF hub_1d_eligible = 1 AND pendency_flag = 1
      → 1+ Day Eligible

  ELSE IF breach_category = 'No Breach'
      IF service_type='Intracity NDD' AND milk_run='1MR' AND HOUR(first_ofd_date)>=17
          → 1st MR miss
      ELSE → No Breach

  ELSE IF breach_category = 'LM Breach'
      IF order_status IN ('RECEIVED_AT_HUB','ASSIGNED','PINCODE_UPDATED') AND pendency_flag=1
          → No Breach  [still actionable at hub]
      IF hub IN capped_hub_list AND order_status='IN_MANIFEST'
          → Hub Capping
      IF service_type='Intracity NDD' AND milk_run='1MR' AND order_status='IN_MANIFEST'
          → 1st MR miss
      IF hub_manifest_status = 'RECEIVED_AT_DC'
          → DDC Connection miss  [Hub Capping / 1+D Eligible handled above]
      IF hub_manifest_status = 'IN_MM'
          → DDC Connection miss
      IF hub_manifest_status = 'MISROUTED'
          IF ideal='NEXT_DAY_DELIVERY'   → DDC Connection miss
          IF ideal='PRIME_AIR_PRIORITY'  → Pending LM Inscan
      IF hub_manifest_status = 'RECEIVED_AT_HUB'
          IF service_type='Intracity NDD' AND milk_run='1MR'  → 1st MR miss
          ELSE                                                  → Pending LM Inscan
      IF hub_manifest_status = 'PARTIALLY_CLOSED'
          → Pending LM Inscan
      IF hub_manifest_status = 'IN_TRANSIT'
          IF odc='IN_TRANSIT':
              IF DATE(ddc_manifest_intransit) > report_date  → DDC Connection miss
              ELSE                                            → Pending LM Inscan
          IF odc='CLOSED':
              IF odc_manifest_intransit IS NOT NULL           → DDC Connection miss
              IF odc_manifest_intransit IS NULL               → ODC Connection miss

  ELSE IF breach_category = 'MM Breach'
      IF hub IN capped_hub_list
          → Hub Capping

      IF service_type IN ('Air- Intercity', 'Zonal + Air- Intercity')
          IF ideal='SURFACE_PRIORITY' AND odc='IN_TRANSIT'
              → Surface Tagging
          IF actual='AIR' AND hub_manifest='IN_TRANSIT' AND lpt>=3
             AND DATE(odc_manifest_intransit) < report_date
              → AH-Intransit
          IF actual='AIR' AND odc='IN_TRANSIT' AND hub_manifest='IN_TRANSIT'
             AND DATE(odc_manifest_intransit) = report_date
              → Air offload  [just dispatched today, too late]
          IF actual='AIR' AND odc IN ('RECEIVED_AT_DC','IN_MM') AND hub_manifest='RECEIVED_AT_DC'
              → Air offload  [received at DC in surface manifest]
          IF actual='AIR' AND odc='CLOSED' AND hub_manifest IN ('RECEIVED_AT_DC','NEW_CREATED')
             AND DATE(odc_manifest_intransit) = report_date
              → Air offload  [ODC closed today, surface-side manifest created]
          IF actual='AIR' AND odc='IN_TRANSIT' AND hub_manifest IS NULL
             AND DATE(odc_manifest_intransit) < report_date
              → Retrieval Delay
          IF actual='AIR' AND odc='IN_TRANSIT' AND hub_manifest IS NULL
             AND DATE(odc_manifest_intransit) = report_date
              → ODC Connection miss  [just dispatched today]
          IF odc='CLOSED' AND hub_manifest IS NULL
              → Pending LM Inscan
          IF DATE(recd_at_dest_dc) = report_date
              → DDC Connection miss  [arrived at DC today, missed hub cutoff]
          ELSE
              → ODC Connection miss  [default]

      IF service_type = 'Zonal NDD'
          IF destination_dc IN ('Ajmer DC','Amritsar DC','Panipat DC')
              → JIT/AD miss
          IF odc='CLOSED' AND hub_manifest IN ('IN_TRANSIT','IN_MM','RECEIVED_AT_DC')
              → DDC Connection miss
          IF odc='CLOSED' AND hub_manifest IS NULL
              → Pending LM Inscan
          ELSE → ODC Connection miss

      IF service_type = 'Intracity SDD'
          IF odc IN ('IN_TRANSIT','IN_MM') AND hub_manifest IN ('IN_TRANSIT','IN_MM')
              → DDC Connection miss
          ELSE → Pending LM Inscan

      IF service_type = 'Intracity NDD'
          → Pending LM Inscan  [default for MM Breach in same-city NDD]
```

---

## Attribution Counts (09-04-2026 sample, n=10,408)

| Attribution | Count | % of Total | Primary Breach Category |
|---|---|---|---|
| No Breach | 10,014 | 96.2% | No Breach (+ some LM Breach) |
| DDC Connection miss | 88 | 0.85% | LM Breach + MM Breach |
| Retrieval Delay | 71 | 0.68% | MM Breach (Air-Intercity) |
| ODC Connection miss | 67 | 0.64% | LM Breach + MM Breach |
| Pending LM Inscan | 57 | 0.55% | LM Breach + MM Breach |
| 1+ Day Eligible | 41 | 0.39% | Any (pre-filter) |
| Air offload | 16 | 0.15% | MM Breach (Air-Intercity) |
| AH-Intransit | 15 | 0.14% | MM Breach (Air-Intercity) |
| Hub Capping | 14 | 0.13% | LM Breach + MM Breach |
| JIT/AD miss | 12 | 0.12% | MM Breach (Zonal NDD) |
| 1st MR miss | 11 | 0.11% | No Breach + LM Breach (Intracity NDD) |
| Surface Tagging | 1 | <0.01% | MM Breach (Air-Intercity) |
| RTO | 1 | <0.01% | No Breach (pre-filter) |
| **MKT Breach** | 0 | — | Defined but no cases in this sample |

---

## Key Separator: `odc_manifest_intransit` Date

The date of `odc_manifest_intransit` relative to the report date is the **most important single separator** for Air-Intercity MM Breach attributions:

| `DATE(odc_manifest_intransit)` | Effect |
|---|---|
| NULL | ODC manifest never dispatched → ODC Connection miss (LM Breach context) |
| = report_date (today) | Just dispatched → Air offload / ODC Connection miss |
| < report_date (before today) | Already in transit for 1+ days → AH-Intransit / Retrieval Delay |

Similarly, for LM Breach + IN_MANIFEST + hub_manifest=IN_TRANSIT:

| `DATE(ddc_manifest_intransit)` | Effect |
|---|---|
| > report_date | Hub dispatch scheduled tomorrow → DDC Connection miss |
| ≤ report_date | Hub dispatch went today → Pending LM Inscan (in transit, not yet arrived) |

---

## Notes on Manual / List-Based Attributions

- **Hub Capping**: Maintained as a manually updated hub list in the DB. Currently BOM_Chembur (LM Breach) and PNQ_Manjari_E (MM Breach). New hubs can be added during peak periods.
- **1+ Day Eligible**: Driven by the `1+D Eligible.xlsx` hub list (~103 hubs). The DB derives `hub_1d_eligible` from this list at query time.
- **JIT/AD miss**: Driven by a fixed set of destination DCs with tight arrival deadlines (currently Ajmer DC, Amritsar DC, Panipat DC).
