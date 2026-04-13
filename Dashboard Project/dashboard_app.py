#!/usr/bin/env python3
"""
C2 Performance Dashboard
========================
Streamlit web app for daily logistics performance monitoring.

Run:
    streamlit run dashboard_app.py

Two pages:
  📊 Daily Dashboard   — upload/auto-load today's file, view KPIs, charts, DC table
  📈 Historical Trends — 4-5 week trend charts, DC heatmap, attribution trend
"""

import warnings
warnings.filterwarnings('ignore')

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import json
import os
from datetime import date
from pathlib import Path
from collections import defaultdict

# ─── PATHS & CONSTANTS ────────────────────────────────────────────────────────
BASE_DIR         = Path(__file__).parent
DEFAULT_DATA     = BASE_DIR / "C2-Performance.xlsx"
DASHBOARD_HIST   = BASE_DIR / "dashboard_history.json"
MAX_HISTORY_DAYS = 35      # ~5 weeks of data

# Exact strings as they appear in the data (note space in 'Air- Intercity')
SERVICE_TYPES = [
    'Air- Intercity',
    'Intracity NDD',
    'Intracity SDD',
    'Zonal + Air- Intercity',
    'Zonal NDD',
]

ATTRIBUTION_CATEGORIES = [
    'No Breach', 'ODC Connection miss', '1st MR miss', 'DDC Connection miss',
    'JIT/AD miss', 'AH-Intransit', 'Air offload', 'Surface Tagging',
    'Retrieval Delay', 'Hub Capping', 'RTO', 'Pending LM Inscan', '1+ Day Eligible',
]

# Performance threshold colours (matching Excel colour scheme)
PERF_GREEN  = '#70AD47'   # ≥ 95%
PERF_ORANGE = '#F4B942'   # ≥ 90%
PERF_RED    = '#E05252'   # < 90%

BREACH_COLORS = {
    'No Breach':  '#70AD47',
    'LM Breach':  '#E74C3C',
    'MM Breach':  '#E67E22',
    'MKT Breach': '#9B59B6',
}

REQUIRED_COLS = {
    'eligible_attempt_date', 'awb_number', 'client_name',
    'origin_dc', 'service_type', 'Attribution', 'rto_flag',
}

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="C2 Performance Dashboard",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CUSTOM CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer     {visibility: hidden;}

    div[data-testid="metric-container"] {
        background: #f4f6f8;
        border: 1px solid #dde1e7;
        border-radius: 10px;
        padding: 12px 16px;
    }
    .section-hdr {
        font-size: 1.05rem;
        font-weight: 600;
        color: #2c3e50;
        border-left: 4px solid #4472C4;
        padding-left: 10px;
        margin: 16px 0 6px 0;
    }
    .sidebar-brand {
        font-size: 1.25rem;
        font-weight: 700;
        color: #1F497D;
    }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════════
# DATA UTILITIES
# ════════════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner="Loading C2-Performance.xlsx …")
def _load_from_path(path: str) -> pd.DataFrame:
    return _parse_df(pd.read_excel(path, engine='openpyxl'))


def _load_from_upload(file_buffer) -> pd.DataFrame:
    """Not cached — re-parsed on every upload."""
    return _parse_df(pd.read_excel(file_buffer, engine='openpyxl'))


def _parse_df(df: pd.DataFrame) -> pd.DataFrame:
    """Shared cleaning applied after reading from any source."""
    df.columns = [c.strip() for c in df.columns]

    for col in ['eligible_attempt_date', 'picked_date', 'first_ofd_date',
                'delivered_date', 'odc_manifest_intransit', 'ddc_manifest_intransit']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')

    # Client display name: AJIO_EXPRESS → Ajio
    if 'client_name' in df.columns:
        df['_client'] = df['client_name'].apply(
            lambda x: str(x).split('_')[0].capitalize() if pd.notna(x) else 'Unknown'
        )
    return df


def validate_columns(df: pd.DataFrame) -> list:
    return sorted([c for c in REQUIRED_COLS if c not in df.columns])


def get_report_date(df: pd.DataFrame):
    if 'eligible_attempt_date' in df.columns:
        dates = df['eligible_attempt_date'].dropna()
        if not dates.empty:
            d = dates.iloc[0]
            return d.date() if hasattr(d, 'date') else d
    return date.today()


# ── Metrics ──────────────────────────────────────────────────────────────────

def compute_kpis(df: pd.DataFrame) -> dict:
    total = len(df)
    if total == 0:
        return dict(total=0, no_breach=0, lm_breach=0, mm_breach=0, rto=0,
                    no_breach_pct=0.0, lm_breach_pct=0.0,
                    mm_breach_pct=0.0, rto_pct=0.0)

    no_b = int((df['Attribution'] == 'No Breach').sum()) if 'Attribution' in df.columns else 0

    bc   = df['breach_category'].value_counts() if 'breach_category' in df.columns else {}
    lm_b = int(bc.get('LM Breach', 0))
    mm_b = int(bc.get('MM Breach', 0))
    rto  = int(df['rto_flag'].sum()) if 'rto_flag' in df.columns else 0

    return dict(
        total=total,
        no_breach=no_b,  no_breach_pct=round(no_b  / total * 100, 1),
        lm_breach=lm_b,  lm_breach_pct=round(lm_b  / total * 100, 1),
        mm_breach=mm_b,  mm_breach_pct=round(mm_b  / total * 100, 1),
        rto=rto,         rto_pct=      round(rto   / total * 100, 1),
    )


def compute_performance_table(df: pd.DataFrame) -> pd.DataFrame:
    """Returns long-format DC × service_type table with Vol, NB, Perf."""
    if not {'origin_dc', 'service_type', 'Attribution', 'awb_number'}.issubset(df.columns):
        return pd.DataFrame()

    rows = []
    for svc in SERVICE_TYPES:
        svc_df = df[df['service_type'] == svc]
        if svc_df.empty:
            continue
        grp = svc_df.groupby('origin_dc').agg(
            Vol=('awb_number', 'count'),
            NB=('Attribution', lambda x: (x == 'No Breach').sum()),
        ).reset_index()
        grp['Perf']         = grp['NB'] / grp['Vol']
        grp['service_type'] = svc
        rows.append(grp)

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def pivot_perf_table(perf_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot: rows = DC, cols = service_type, values = Perf (0–100)."""
    if perf_df.empty:
        return pd.DataFrame()

    pivot = perf_df.pivot_table(
        index='origin_dc', columns='service_type', values='Perf', aggfunc='first'
    )
    # Weighted-average Grand Total row
    vol_pivot = perf_df.pivot_table(
        index='origin_dc', columns='service_type', values='Vol', aggfunc='first'
    ).fillna(0)

    gt = {}
    for svc in pivot.columns:
        v = vol_pivot[svc].fillna(0)
        p = pivot[svc].fillna(0)
        gt[svc] = (v * p).sum() / v.sum() if v.sum() > 0 else float('nan')
    pivot.loc['Grand Total'] = gt

    pivot = pivot.reset_index().rename(columns={'origin_dc': 'DC'})
    # Convert to percentage
    for c in pivot.columns:
        if c != 'DC':
            pivot[c] = pivot[c] * 100
    return pivot


def pivot_vol_table(perf_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot: rows = DC, cols = service_type, values = Vol."""
    if perf_df.empty:
        return pd.DataFrame()

    pivot = perf_df.pivot_table(
        index='origin_dc', columns='service_type', values='Vol', aggfunc='first'
    ).fillna(0).astype(int)
    pivot.loc['Grand Total'] = pivot.sum()
    return pivot.reset_index().rename(columns={'origin_dc': 'DC'})


def compute_attribution_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    if 'Attribution' not in df.columns:
        return pd.DataFrame()
    attr = df['Attribution'].value_counts().reset_index()
    attr.columns = ['Attribution', 'Count']
    attr['%'] = round(attr['Count'] / attr['Count'].sum() * 100, 1)
    return attr


# ── Styler ────────────────────────────────────────────────────────────────────

def _color_cell(val):
    """Per-cell CSS for performance % (expects 0–100 float)."""
    if pd.isna(val):
        return ''
    try:
        v = float(val)
    except (ValueError, TypeError):
        return ''
    if v >= 95:
        return f'background-color: {PERF_GREEN}; color: white; font-weight: bold'
    elif v >= 90:
        return f'background-color: {PERF_ORANGE}; font-weight: bold'
    else:
        return f'background-color: {PERF_RED}; color: white; font-weight: bold'


def _highlight_grand_total(row):
    if row.get('DC') == 'Grand Total':
        return ['background-color: #D9D9D9; font-weight: bold; color: #1a1a1a'] * len(row)
    return [''] * len(row)


def apply_perf_style(pivot: pd.DataFrame):
    svc_cols = [c for c in pivot.columns if c != 'DC']
    try:
        styled = pivot.style.map(_color_cell, subset=svc_cols)
    except AttributeError:                           # pandas < 2.1
        styled = pivot.style.applymap(_color_cell, subset=svc_cols)

    styled = styled.apply(_highlight_grand_total, axis=1)
    styled = styled.format(
        {c: (lambda x: f"{x:.1f}%" if pd.notna(x) else "—") for c in svc_cols}
    )
    return styled


# ════════════════════════════════════════════════════════════════════════════════
# HISTORY MANAGEMENT
# ════════════════════════════════════════════════════════════════════════════════

def load_history() -> list:
    if DASHBOARD_HIST.exists():
        with open(DASHBOARD_HIST) as f:
            return json.load(f)
    return []


def save_to_history(df: pd.DataFrame, report_date) -> bool:
    """
    Log one day's aggregated metrics.
    Returns True if newly added, False if overwritten.
    """
    history   = load_history()
    date_key  = str(report_date)
    existing  = next((i for i, h in enumerate(history) if h['date'] == date_key), None)

    kpis = compute_kpis(df)

    by_service = {}
    if 'service_type' in df.columns and 'Attribution' in df.columns:
        for svc in SERVICE_TYPES:
            svc_df = df[df['service_type'] == svc]
            if not svc_df.empty:
                vol  = len(svc_df)
                perf = round((svc_df['Attribution'] == 'No Breach').sum() / vol * 100, 2)
                by_service[svc] = {'vol': vol, 'perf': perf}

    by_dc = {}
    if 'origin_dc' in df.columns and 'Attribution' in df.columns:
        for dc, g in df.groupby('origin_dc'):
            vol  = len(g)
            perf = round((g['Attribution'] == 'No Breach').sum() / vol * 100, 2)
            by_dc[str(dc)] = {'vol': vol, 'perf': perf}

    attr_pct = {}
    if 'Attribution' in df.columns:
        total = len(df)
        for attr, cnt in df['Attribution'].value_counts().items():
            attr_pct[str(attr)] = round(cnt / total * 100, 2)

    entry = {
        'date':          date_key,
        'total':         int(kpis['total']),
        'no_breach_pct': kpis['no_breach_pct'],
        'lm_breach_pct': kpis['lm_breach_pct'],
        'mm_breach_pct': kpis['mm_breach_pct'],
        'rto_pct':       kpis['rto_pct'],
        'by_service':    by_service,
        'by_dc':         by_dc,
        'attribution':   attr_pct,
    }

    if existing is not None:
        history[existing] = entry
        new = False
    else:
        history.insert(0, entry)
        new = True

    history = sorted(history, key=lambda h: h['date'], reverse=True)[:MAX_HISTORY_DAYS]

    with open(DASHBOARD_HIST, 'w') as f:
        json.dump(history, f, indent=2)

    return new


def history_to_df(history: list) -> pd.DataFrame:
    """Flatten history list into a tidy DataFrame for charting."""
    rows = []
    for h in history:
        row = {
            'date':          h['date'],
            'total':         h.get('total', 0),
            'no_breach_pct': h.get('no_breach_pct', 0),
            'lm_breach_pct': h.get('lm_breach_pct', 0),
            'mm_breach_pct': h.get('mm_breach_pct', 0),
            'rto_pct':       h.get('rto_pct', 0),
        }
        for svc, data in h.get('by_service', {}).items():
            # Safe key: strip special chars for column naming
            key = 'svc||' + svc
            row[key] = data.get('perf', 0)
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)


# ════════════════════════════════════════════════════════════════════════════════
# PAGE 1 — DAILY DASHBOARD
# ════════════════════════════════════════════════════════════════════════════════

def page_daily():
    st.markdown(
        '<h1 style="color:#1F497D; margin-bottom: 2px">📦 C2 Performance Dashboard</h1>',
        unsafe_allow_html=True,
    )

    # ── File loading ──────────────────────────────────────────────────────────
    col_up, col_status = st.columns([3, 2])
    with col_up:
        uploaded = st.file_uploader(
            "Drop C2-Performance file here (xlsx / csv)",
            type=['xlsx', 'csv'],
            label_visibility="collapsed",
            help="Daily DB extract. If nothing is uploaded, auto-loads C2-Performance.xlsx from the project folder.",
        )

    df, source_label = None, ""

    if uploaded:
        try:
            df = _load_from_upload(uploaded)
            source_label = f"📎 {uploaded.name}  ({len(df):,} rows)"
        except Exception as e:
            st.error(f"❌ Could not read uploaded file: {e}")
            st.stop()
    elif DEFAULT_DATA.exists():
        try:
            df = _load_from_path(str(DEFAULT_DATA))
            mtime = DEFAULT_DATA.stat().st_mtime
            import datetime as _dt
            mod_str = _dt.datetime.fromtimestamp(mtime).strftime('%d %b %Y %H:%M')
            source_label = f"📂 Auto-loaded C2-Performance.xlsx  ({len(df):,} rows)  |  Modified {mod_str}"
        except Exception as e:
            st.error(f"❌ Could not load C2-Performance.xlsx: {e}")
            st.stop()

    if df is None:
        st.info(
            "⬆️  Upload a **C2-Performance.xlsx** (or .csv) file above, "
            "or place it in the project folder for automatic loading."
        )
        st.stop()

    with col_status:
        st.caption(source_label)

    # Column validation
    missing = validate_columns(df)
    if missing:
        st.error(
            f"❌ Missing required column(s): **{', '.join(missing)}**\n\n"
            "Make sure the file is the correct C2-Performance DB extract."
        )
        st.stop()

    # Date sanity check
    if 'eligible_attempt_date' in df.columns:
        distinct = df['eligible_attempt_date'].dropna().dt.date.unique()
        if len(distinct) > 1:
            st.warning(
                f"⚠️  Multiple dates in data: {sorted(distinct)}.  "
                f"Using the most recent date."
            )
            df = df[df['eligible_attempt_date'].dt.date == max(distinct)]

    report_date     = get_report_date(df)
    report_date_str = report_date.strftime('%d %b %Y') if hasattr(report_date, 'strftime') else str(report_date)

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown('<div class="sidebar-brand">📦 C2 Dashboard</div>', unsafe_allow_html=True)
        st.caption(f"Report: **{report_date_str}**")
        st.divider()

        st.markdown("**🔧 Filters**")
        clients = ['All'] + sorted(df['_client'].dropna().unique()) if '_client' in df.columns else ['All']
        sel_client = st.selectbox("Client", clients)

        svcs_in_data = [s for s in SERVICE_TYPES if s in df.get('service_type', pd.Series()).unique()]
        services = ['All'] + svcs_in_data
        sel_service = st.selectbox("Service Type", services)

        dcs = ['All'] + sorted(df['origin_dc'].dropna().unique()) if 'origin_dc' in df.columns else ['All']
        sel_dc = st.selectbox("Origin DC", dcs)

        st.divider()
        st.markdown("**💾 Daily Logger**")

        history      = load_history()
        already_logged = any(h['date'] == str(report_date) for h in history)

        if already_logged:
            st.success(f"✅ {report_date_str} already logged")
            if st.button("🔄 Re-log (overwrite)", use_container_width=True):
                save_to_history(df, report_date)
                st.rerun()
        else:
            if st.button("💾 Log Today's Data", type="primary", use_container_width=True):
                save_to_history(df, report_date)
                st.success("Saved!")
                st.rerun()

        n_hist = len(history)
        st.caption(f"{n_hist} day{'s' if n_hist != 1 else ''} in history")

    # ── Apply filters ─────────────────────────────────────────────────────────
    dff = df.copy()
    if sel_client  != 'All' and '_client'      in dff.columns: dff = dff[dff['_client']      == sel_client]
    if sel_service != 'All' and 'service_type' in dff.columns: dff = dff[dff['service_type'] == sel_service]
    if sel_dc      != 'All' and 'origin_dc'    in dff.columns: dff = dff[dff['origin_dc']    == sel_dc]

    if dff.empty:
        st.warning("⚠️  No data for selected filters. Try widening the selection.")
        st.stop()

    # ── KPI Row ───────────────────────────────────────────────────────────────
    st.subheader(f"📅 {report_date_str}")
    kpis = compute_kpis(dff)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📦 Total",      f"{kpis['total']:,}")
    c2.metric("✅ No Breach",  f"{kpis['no_breach']:,}",  f"{kpis['no_breach_pct']}%")
    c3.metric("🚚 LM Breach",  f"{kpis['lm_breach']:,}",  f"{kpis['lm_breach_pct']}%",  delta_color="inverse")
    c4.metric("✈️ MM Breach",  f"{kpis['mm_breach']:,}",  f"{kpis['mm_breach_pct']}%",  delta_color="inverse")
    c5.metric("↩️ RTO",        f"{kpis['rto']:,}",        f"{kpis['rto_pct']}%",         delta_color="inverse")

    st.divider()

    # ── Attribution + Breach stacked bar ──────────────────────────────────────
    left, right = st.columns(2)

    with left:
        st.markdown('<div class="section-hdr">🏷️ Attribution Breakdown</div>', unsafe_allow_html=True)
        attr_df = compute_attribution_breakdown(dff)
        if not attr_df.empty:
            fig_pie = px.pie(
                attr_df, values='Count', names='Attribution', hole=0.42,
                color_discrete_sequence=px.colors.qualitative.Set3,
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label', textfont_size=10)
            fig_pie.update_layout(height=300, showlegend=False, margin=dict(t=5, b=5, l=5, r=5))
            st.plotly_chart(fig_pie, use_container_width=True)

            # Top-8 horizontal bar
            top8 = attr_df.head(8).sort_values('%')
            fig_hbar = px.bar(
                top8, x='%', y='Attribution', orientation='h',
                color='%', color_continuous_scale='RdYlGn', range_color=[0, max(top8['%'].max(), 1)],
                text='%', labels={'%': 'Share %'},
            )
            fig_hbar.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
            fig_hbar.update_layout(height=240, coloraxis_showscale=False, margin=dict(t=5, b=5, r=50))
            st.plotly_chart(fig_hbar, use_container_width=True)
        else:
            st.info("No Attribution column found.")

    with right:
        st.markdown('<div class="section-hdr">📊 Breach by Service Type</div>', unsafe_allow_html=True)
        if 'service_type' in dff.columns and 'breach_category' in dff.columns:
            bs = (dff.groupby(['service_type', 'breach_category'])
                  .size().reset_index(name='Count'))
            fig_stk = px.bar(
                bs, x='service_type', y='Count', color='breach_category',
                barmode='stack', color_discrete_map=BREACH_COLORS,
                labels={'service_type': '', 'Count': 'Shipments', 'breach_category': ''},
            )
            fig_stk.update_layout(
                height=300,
                legend=dict(orientation='h', yanchor='bottom', y=1.02, x=0),
                margin=dict(t=40, b=5),
            )
            st.plotly_chart(fig_stk, use_container_width=True)
        else:
            st.info("breach_category column not available.")

        # Compact attribution table
        if not attr_df.empty:
            st.dataframe(
                attr_df.rename(columns={'%': 'Share %'}),
                hide_index=True, use_container_width=True, height=220,
            )

    st.divider()

    # ── DC Performance Table ──────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">🗺️ DC-Level Performance</div>', unsafe_allow_html=True)

    perf_df = compute_performance_table(dff)
    if not perf_df.empty:
        tab_pct, tab_vol = st.tabs(["📈 Performance %", "📦 Volume"])

        with tab_pct:
            pivot_p = pivot_perf_table(perf_df)
            if not pivot_p.empty:
                styled = apply_perf_style(pivot_p)
                st.dataframe(styled, use_container_width=True, height=460, hide_index=True)
                st.caption("🟢 ≥ 95%  |  🟡 90–94%  |  🔴 < 90%  ·  Performance = No Breach / Total (Attribution-based)")
            else:
                st.info("No data to display.")

        with tab_vol:
            pivot_v = pivot_vol_table(perf_df)
            st.dataframe(pivot_v, use_container_width=True, height=460, hide_index=True)
    else:
        st.info("Performance table unavailable — check that origin_dc, service_type, Attribution and awb_number columns are present.")

    st.divider()

    # ── Per-Service Attribution Tabs ──────────────────────────────────────────
    if sel_service == 'All' and 'service_type' in dff.columns:
        st.markdown('<div class="section-hdr">🔍 Attribution by Service Type</div>', unsafe_allow_html=True)
        svcs_present = [s for s in SERVICE_TYPES if s in dff['service_type'].unique()]
        if svcs_present:
            svc_tabs = st.tabs(svcs_present)
            for tab, svc in zip(svc_tabs, svcs_present):
                with tab:
                    sa = compute_attribution_breakdown(dff[dff['service_type'] == svc])
                    if not sa.empty:
                        col_a, col_b = st.columns([3, 2])
                        with col_a:
                            fig_s = px.bar(
                                sa.sort_values('%'), x='%', y='Attribution', orientation='h',
                                color='%', color_continuous_scale='RdYlGn',
                                range_color=[0, max(sa['%'].max(), 1)],
                                text='%', labels={'%': 'Share %'},
                            )
                            fig_s.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
                            fig_s.update_layout(height=320, coloraxis_showscale=False, margin=dict(t=5, r=55))
                            st.plotly_chart(fig_s, use_container_width=True)
                        with col_b:
                            st.dataframe(sa, hide_index=True, use_container_width=True)
                    else:
                        st.info(f"No data for {svc} with current filters.")

        st.divider()

    # ── Raw Data Explorer ─────────────────────────────────────────────────────
    with st.expander("🔍 Raw Data Explorer", expanded=False):
        pref_cols = ['awb_number', '_client', 'origin_dc', 'service_type',
                     'order_status', 'breach_category', 'Attribution', 'rto_flag']
        show_cols = [c for c in pref_cols if c in dff.columns]
        extra     = [c for c in dff.columns if c not in show_cols and not c.startswith('_')]
        show_cols += extra[:15]

        search_q = st.text_input("🔎  Search AWB / DC / Attribution …", "")
        view_df  = dff[show_cols]
        if search_q:
            mask    = view_df.apply(lambda r: r.astype(str).str.contains(search_q, case=False).any(), axis=1)
            view_df = view_df[mask]

        st.dataframe(view_df, use_container_width=True, height=360, hide_index=True)
        st.caption(f"Showing {len(view_df):,} of {len(dff):,} rows")


# ════════════════════════════════════════════════════════════════════════════════
# PAGE 2 — HISTORICAL TRENDS
# ════════════════════════════════════════════════════════════════════════════════

def page_history():
    st.markdown(
        '<h1 style="color:#1F497D; margin-bottom: 2px">📈 Historical Trends</h1>',
        unsafe_allow_html=True,
    )

    history = load_history()
    if not history:
        st.info(
            "📭  No history yet.\n\n"
            "Open **Daily Dashboard**, load today's file, then click **💾 Log Today's Data** "
            "in the sidebar. Come back here after logging at least 2 days."
        )
        st.stop()

    hist_df = history_to_df(history)
    if hist_df.empty:
        st.warning("History file exists but could not be parsed.")
        st.stop()

    # ── Sidebar controls ──────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown('<div class="sidebar-brand">📦 C2 Dashboard</div>', unsafe_allow_html=True)
        st.divider()

        st.markdown("**📅 Date Range**")
        min_d = hist_df['date'].min().date()
        max_d = hist_df['date'].max().date()
        date_range = st.date_input("Range", value=(min_d, max_d),
                                   min_value=min_d, max_value=max_d)

        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            hist_df = hist_df[
                (hist_df['date'].dt.date >= date_range[0]) &
                (hist_df['date'].dt.date <= date_range[1])
            ]

        st.divider()
        st.markdown("**⬇️ Export**")
        exp_cols = [c for c in ['date', 'total', 'no_breach_pct', 'lm_breach_pct',
                                 'mm_breach_pct', 'rto_pct'] if c in hist_df.columns]
        csv = hist_df[exp_cols].to_csv(index=False)
        st.download_button("Download History CSV", csv, "c2_history.csv", "text/csv",
                           use_container_width=True)

        st.divider()
        st.caption(f"Total days logged: **{len(load_history())}**")

    if hist_df.empty:
        st.warning("No data in selected date range.")
        st.stop()

    # ── Summary KPIs ─────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Days in View", len(hist_df))
    c2.metric("Avg No Breach %", f"{hist_df['no_breach_pct'].mean():.1f}%")

    if not hist_df.empty:
        best_i  = hist_df['no_breach_pct'].idxmax()
        worst_i = hist_df['no_breach_pct'].idxmin()
        c3.metric("🏆 Best Day",
                  hist_df.loc[best_i, 'date'].strftime('%d %b'),
                  f"{hist_df.loc[best_i,  'no_breach_pct']:.1f}%")
        c4.metric("⚠️ Worst Day",
                  hist_df.loc[worst_i, 'date'].strftime('%d %b'),
                  f"{hist_df.loc[worst_i, 'no_breach_pct']:.1f}%", delta_color="inverse")

    st.divider()

    if len(hist_df) < 2:
        st.info("📊 Trend charts need at least **2 days** of logged data. Log again tomorrow and check back!")
        st.stop()

    # ── Overall performance trend ─────────────────────────────────────────────
    st.markdown('<div class="section-hdr">🎯 Overall Performance Trend</div>', unsafe_allow_html=True)

    fig_ov = go.Figure()
    fig_ov.add_trace(go.Scatter(
        x=hist_df['date'], y=hist_df['no_breach_pct'], name='No Breach %',
        mode='lines+markers', line=dict(color='#70AD47', width=3), marker=dict(size=7),
        hovertemplate='<b>%{y:.1f}%</b><extra>No Breach</extra>',
    ))
    fig_ov.add_trace(go.Scatter(
        x=hist_df['date'], y=hist_df['lm_breach_pct'], name='LM Breach %',
        mode='lines+markers', line=dict(color='#E74C3C', width=2, dash='dot'), marker=dict(size=6),
        hovertemplate='<b>%{y:.1f}%</b><extra>LM Breach</extra>',
    ))
    fig_ov.add_trace(go.Scatter(
        x=hist_df['date'], y=hist_df['mm_breach_pct'], name='MM Breach %',
        mode='lines+markers', line=dict(color='#E67E22', width=2, dash='dot'), marker=dict(size=6),
        hovertemplate='<b>%{y:.1f}%</b><extra>MM Breach</extra>',
    ))
    fig_ov.add_hrect(y0=90, y1=95, fillcolor='#FFF2CC', opacity=0.25, line_width=0)
    fig_ov.add_hline(y=90, line_dash='dash', line_color='#BDC3C7',
                     annotation_text='90% baseline', annotation_position='bottom right')
    fig_ov.add_hline(y=95, line_dash='dash', line_color='#70AD47',
                     annotation_text='95% target',   annotation_position='bottom right')
    fig_ov.update_layout(
        height=340, hovermode='x unified',
        legend=dict(orientation='h', yanchor='bottom', y=1.02),
        yaxis=dict(range=[max(0, hist_df['no_breach_pct'].min() - 5), 102], title='%'),
        margin=dict(t=40, b=10),
    )
    st.plotly_chart(fig_ov, use_container_width=True)

    # ── Per-service-type trend ────────────────────────────────────────────────
    svc_cols = [c for c in hist_df.columns if c.startswith('svc||')]
    if svc_cols:
        st.markdown('<div class="section-hdr">🚀 Performance by Service Type</div>', unsafe_allow_html=True)
        palette = ['#3498DB', '#9B59B6', '#1ABC9C', '#F39C12', '#E74C3C']

        fig_svc = go.Figure()
        for i, col in enumerate(svc_cols):
            label = col.replace('svc||', '')
            fig_svc.add_trace(go.Scatter(
                x=hist_df['date'], y=hist_df[col], name=label,
                mode='lines+markers',
                line=dict(color=palette[i % len(palette)], width=2),
                marker=dict(size=6),
                hovertemplate=f'<b>%{{y:.1f}}%</b><extra>{label}</extra>',
            ))
        fig_svc.add_hline(y=90, line_dash='dash', line_color='#BDC3C7')
        fig_svc.update_layout(
            height=320, hovermode='x unified',
            legend=dict(orientation='h', yanchor='bottom', y=1.02),
            yaxis=dict(title='No Breach %'),
            margin=dict(t=40, b=10),
        )
        st.plotly_chart(fig_svc, use_container_width=True)

    # ── Volume bar chart ──────────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">📦 Daily Volume</div>', unsafe_allow_html=True)
    fig_vol = px.bar(
        hist_df, x='date', y='total', text='total',
        color_discrete_sequence=['#4472C4'],
        labels={'total': 'Shipments', 'date': 'Date'},
    )
    fig_vol.update_traces(texttemplate='%{text:,}', textposition='outside')
    fig_vol.update_layout(height=250, margin=dict(t=30, b=10))
    st.plotly_chart(fig_vol, use_container_width=True)

    # ── Attribution trend (top 5 by average share) ────────────────────────────
    st.markdown('<div class="section-hdr">🏷️ Attribution Trend — Top 5 Buckets</div>', unsafe_allow_html=True)

    attr_rows = []
    for h in history:
        d = h.get('date')
        for attr, pct in h.get('attribution', {}).items():
            attr_rows.append({'date': d, 'Attribution': attr, '%': pct})

    if attr_rows:
        attr_trend = pd.DataFrame(attr_rows)
        attr_trend['date'] = pd.to_datetime(attr_trend['date'])

        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            attr_trend = attr_trend[
                (attr_trend['date'].dt.date >= date_range[0]) &
                (attr_trend['date'].dt.date <= date_range[1])
            ]

        top5 = attr_trend.groupby('Attribution')['%'].mean().nlargest(5).index
        fig_attr = px.line(
            attr_trend[attr_trend['Attribution'].isin(top5)],
            x='date', y='%', color='Attribution', markers=True,
            labels={'%': 'Share %', 'date': 'Date'},
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_attr.update_layout(
            height=300, hovermode='x unified',
            legend=dict(orientation='h', yanchor='bottom', y=1.02),
            margin=dict(t=40, b=10),
        )
        st.plotly_chart(fig_attr, use_container_width=True)

    # ── DC Performance Heatmap ────────────────────────────────────────────────
    st.markdown('<div class="section-hdr">🗺️ DC Performance Heatmap</div>', unsafe_allow_html=True)

    dc_rows = []
    for h in history:
        d = h.get('date')
        for dc, data in h.get('by_dc', {}).items():
            dc_rows.append({'date': d, 'DC': dc, 'Perf %': data.get('perf', 0)})

    if dc_rows:
        dc_df = pd.DataFrame(dc_rows)
        dc_df['date'] = pd.to_datetime(dc_df['date'])

        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            dc_df = dc_df[
                (dc_df['date'].dt.date >= date_range[0]) &
                (dc_df['date'].dt.date <= date_range[1])
            ]

        if not dc_df.empty:
            hmap = dc_df.pivot_table(
                index='DC', columns='date', values='Perf %', aggfunc='first'
            )
            hmap.columns = [pd.Timestamp(c).strftime('%d %b') for c in hmap.columns]

            # Sort: worst avg performance at top (makes problem DCs visible first)
            hmap = hmap.loc[hmap.mean(axis=1).sort_values().index]

            n_dcs = len(hmap)
            fig_hmap = px.imshow(
                hmap,
                color_continuous_scale='RdYlGn',
                zmin=85, zmax=100,
                text_auto='.0f',
                aspect='auto',
                labels=dict(color='No Breach %'),
            )
            fig_hmap.update_layout(
                height=max(350, n_dcs * 24),
                coloraxis_colorbar_title='%',
                margin=dict(t=10, b=10),
            )
            st.plotly_chart(fig_hmap, use_container_width=True)
            st.caption("Sorted: worst-performing DCs at top.  Colour scale: red = <85%, yellow = ~92%, green = ≥100%")

    # ── Raw history table ─────────────────────────────────────────────────────
    with st.expander("📋 Raw History Table"):
        disp_cols = {
            'date':          'Date',
            'total':         'Total',
            'no_breach_pct': 'No Breach %',
            'lm_breach_pct': 'LM Breach %',
            'mm_breach_pct': 'MM Breach %',
            'rto_pct':       'RTO %',
        }
        avail = {k: v for k, v in disp_cols.items() if k in hist_df.columns}
        st.dataframe(
            hist_df[list(avail)].rename(columns=avail),
            hide_index=True, use_container_width=True,
        )


# ════════════════════════════════════════════════════════════════════════════════
# MAIN — NAVIGATION
# ════════════════════════════════════════════════════════════════════════════════

def main():
    with st.sidebar:
        page = st.radio(
            "Navigation",
            options=["📊 Daily Dashboard", "📈 Historical Trends"],
            label_visibility="collapsed",
        )

    if page == "📊 Daily Dashboard":
        page_daily()
    else:
        page_history()


if __name__ == "__main__":
    main()
