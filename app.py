import streamlit as st
import tempfile
import os
import math
import pandas as pd
from datetime import datetime
from collections import defaultdict
from emi_engine import process_pdf, generate_excel, ParseLogger, parse_date

# ═══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="EMI Statement Analyzer | Manappuram Finance",
    page_icon="📊",
    layout="wide",
)

# ═══════════════════════════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
[data-testid="stAppViewContainer"] {
    background-color: #0f1117;
    font-family: 'Inter', sans-serif;
}
[data-testid="stHeader"] { background-color: #0f1117; }
[data-testid="stSidebar"] { display: none; }
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 1rem; max-width: 1200px; }

.kpi-card {
    background: linear-gradient(135deg, #1e2130 0%, #1a1d28 100%);
    border: 1px solid #2a2d3a;
    border-radius: 12px; padding: 18px 22px;
    transition: transform 0.2s;
}
.kpi-card:hover { transform: translateY(-2px); }
.kpi-label {
    font-size: 10px; color: #6b7280; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 6px; font-weight: 600;
}
.kpi-value { font-size: 24px; font-weight: 800; line-height: 1.2; }
.kpi-sub { font-size: 11px; color: #6b7280; margin-top: 4px; }
.blue { color: #3b82f6; }
.green { color: #10b981; }
.red { color: #ef4444; }
.amber { color: #f59e0b; }
.purple { color: #8b5cf6; }
.cyan { color: #06b6d4; }
.white { color: #e5e7eb; }
.pink { color: #ec4899; }

.risk-badge {
    display: inline-block; padding: 4px 14px; border-radius: 20px;
    font-size: 12px; font-weight: 700; letter-spacing: 0.5px;
}
.risk-low { background: #052e16; color: #22c55e; border: 1px solid #16a34a; }
.risk-medium { background: #451a03; color: #fb923c; border: 1px solid #ea580c; }
.risk-high { background: #450a0a; color: #f87171; border: 1px solid #dc2626; }

.section-head {
    font-size: 16px; font-weight: 700; color: #d1d5db;
    margin: 28px 0 14px 0; padding-bottom: 8px;
    border-bottom: 1px solid #2a2d3a;
}

.top-bar {
    background: linear-gradient(90deg, #1e2130 0%, #16181f 100%);
    border: 1px solid #2a2d3a; border-radius: 14px;
    padding: 20px 28px; margin-bottom: 20px;
}
.top-bar h1 { margin: 0; font-size: 20px; color: #fff; font-weight: 700; }
.top-bar .sub { font-size: 13px; color: #8b8f96; margin-top: 4px; }
.top-bar .acct { font-size: 11px; color: #6b7280; margin-top: 2px; }

.log-box {
    background: #0c0e14; border: 1px solid #1e2028;
    border-radius: 10px; padding: 14px;
    max-height: 300px; overflow-y: auto;
    font-family: 'JetBrains Mono', 'Courier New', monospace; font-size: 11px;
}
.log-row { padding: 3px 0; border-bottom: 1px solid #14161e; display: flex; gap: 14px; }
.log-row:last-child { border-bottom: none; }
.log-ts { color: #4b5563; min-width: 55px; }
.log-msg { color: #9ca3af; }
.log-msg.bounce { color: #f87171; }
.log-msg.success { color: #34d399; }

.stDownloadButton > button {
    background-color: #059669 !important; color: white !important;
    border: none !important; border-radius: 8px !important;
    font-weight: 600 !important;
}

.insight-box {
    background: #1a1c25; border-left: 3px solid #3b82f6;
    border-radius: 0 8px 8px 0; padding: 12px 16px; margin: 6px 0;
    font-size: 13px; color: #d1d5db;
}
.insight-box.warn { border-left-color: #f59e0b; }
.insight-box.danger { border-left-color: #ef4444; }
.insight-box.ok { border-left-color: #10b981; }

.stat-formula {
    font-size: 10px; color: #4b5563; font-family: 'Courier New', monospace;
    margin-top: 2px;
}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════
if "result" not in st.session_state:
    st.session_state.result = None
if "filename" not in st.session_state:
    st.session_state.filename = None
if "show_log" not in st.session_state:
    st.session_state.show_log = False

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════
def fmt_inr(amount):
    if amount >= 10000000:
        return f"₹{amount/10000000:.2f} Cr"
    if amount >= 100000:
        return f"₹{amount/100000:.2f} L"
    if amount >= 1000:
        return f"₹{amount:,.0f}"
    return f"₹{amount:.0f}"


def compute_insights(result):
    info = result["page1_info"]
    final_data = result["final_data"]
    bounces = result["bounces"]
    clean_receipts = result["clean_receipts"]

    total_emis = len(final_data)
    loan_amt = info["loan_amount"]
    emi_amt = info["emi_amount"]
    tenure = info["tenure"] if info["tenure"] > 0 else total_emis

    # Status counts
    paid = sum(1 for r in final_data if r.get("Status") == "Paid")
    late = sum(1 for r in final_data if r.get("Status") == "Late")
    bounced_status = sum(1 for r in final_data if r.get("Status") == "Bounced")
    unpaid = sum(1 for r in final_data if r.get("Status") in ("Unpaid", "Partial"))

    # DPD analysis
    dpds = [r.get("DPD", 0) for r in final_data]
    peak_dpd = max(dpds) if dpds else 0
    avg_dpd = sum(dpds) / len(dpds) if dpds else 0
    zero_dpd_count = sum(1 for d in dpds if d == 0)
    dpd_30_plus = sum(1 for d in dpds if d > 30)
    dpd_60_plus = sum(1 for d in dpds if d > 60)
    dpd_90_plus = sum(1 for d in dpds if d > 90)

    # === STATISTICAL CALCULATIONS ===

    # Standard Deviation of DPD
    if len(dpds) > 1:
        mean_dpd = avg_dpd
        variance_dpd = sum((d - mean_dpd) ** 2 for d in dpds) / (len(dpds) - 1)
        std_dev_dpd = math.sqrt(variance_dpd)
    else:
        variance_dpd = 0
        std_dev_dpd = 0

    # Coefficient of Variation (CV) — payment consistency
    cv_dpd = (std_dev_dpd / avg_dpd * 100) if avg_dpd > 0 else 0

    # Payment Regularity Index (PRI) = % of EMIs paid within 5 days of due
    within_5_days = sum(1 for d in dpds if d <= 5)
    pri = (within_5_days / total_emis * 100) if total_emis > 0 else 0

    # Delinquency Ratio = EMIs with DPD>0 / Total EMIs
    delinquent_count = sum(1 for d in dpds if d > 0)
    delinquency_ratio = (delinquent_count / total_emis * 100) if total_emis > 0 else 0

    # Weighted Average DPD (recent months get higher weight)
    if dpds:
        weights = list(range(1, len(dpds) + 1))
        weighted_sum = sum(d * w for d, w in zip(dpds, weights))
        total_weight = sum(weights)
        weighted_avg_dpd = weighted_sum / total_weight
    else:
        weighted_avg_dpd = 0

    # Median DPD
    sorted_dpds = sorted(dpds)
    if sorted_dpds:
        mid = len(sorted_dpds) // 2
        median_dpd = sorted_dpds[mid] if len(sorted_dpds) % 2 == 1 else (sorted_dpds[mid - 1] + sorted_dpds[mid]) / 2
    else:
        median_dpd = 0

    # 90th Percentile DPD (P90)
    if sorted_dpds:
        p90_idx = int(math.ceil(0.9 * len(sorted_dpds))) - 1
        p90_dpd = sorted_dpds[min(p90_idx, len(sorted_dpds) - 1)]
    else:
        p90_dpd = 0

    # Deposit Coverage Ratio = Total Deposited / Total EMI Due
    total_deposit = sum(r["Dep. Amt"] for r in final_data)
    total_emi_due = sum(r["EMI"] for r in final_data)
    deposit_coverage = (total_deposit / total_emi_due) if total_emi_due > 0 else 0

    # Interest Burden Ratio = (Total Paid - Loan Amount) / Loan Amount
    interest_burden = ((total_deposit - loan_amt) / loan_amt * 100) if loan_amt > 0 else 0

    # Repayment Progress = Total Deposited / (EMI * Tenure)
    total_expected = emi_amt * tenure if emi_amt > 0 else total_emi_due
    repayment_progress = (total_deposit / total_expected * 100) if total_expected > 0 else 0

    # EMI Strain Index = Avg(Deposit/EMI) deviation
    emi_ratios = []
    for r in final_data:
        if r["EMI"] > 0:
            emi_ratios.append(r["Dep. Amt"] / r["EMI"])
    avg_emi_ratio = sum(emi_ratios) / len(emi_ratios) if emi_ratios else 0

    # Last 12 / 6 months
    last_12 = dpds[-12:] if len(dpds) >= 12 else dpds
    l12_peak = max(last_12) if last_12 else 0
    l12_avg = sum(last_12) / len(last_12) if last_12 else 0
    l12_zero = sum(1 for d in last_12 if d == 0)

    last_6 = dpds[-6:] if len(dpds) >= 6 else dpds
    l6_peak = max(last_6) if last_6 else 0
    l6_avg = sum(last_6) / len(last_6) if last_6 else 0

    # Total amounts
    total_receipts_amt = result["total_receipts_amount"]
    total_bounces = result["total_bounces"]

    # Remaining
    remaining_emis = max(0, tenure - total_emis)

    # Collection efficiency
    coll_efficiency = (total_deposit / total_emi_due * 100) if total_emi_due > 0 else 0

    # On-time payment rate
    on_time_rate = (zero_dpd_count / total_emis * 100) if total_emis > 0 else 0

    # Bounce rate
    bounce_rate = (total_bounces / total_emis * 100) if total_emis > 0 else 0

    # Track status
    if peak_dpd > 60 or dpd_90_plus > 0:
        track_status = "PTR"
        track_label = "Poor Track Record"
        track_color = "red"
    elif peak_dpd > 30:
        track_status = "GTR"
        track_label = "Good Track Record"
        track_color = "amber"
    else:
        track_status = "ETR"
        track_label = "Excellent Track Record"
        track_color = "green"

    # Risk score
    risk_score = 0
    if peak_dpd > 90: risk_score += 40
    elif peak_dpd > 60: risk_score += 30
    elif peak_dpd > 30: risk_score += 15
    risk_score += min(total_bounces * 8, 30)
    if coll_efficiency < 90: risk_score += 15
    elif coll_efficiency < 95: risk_score += 8
    if l6_avg > 15: risk_score += 15
    elif l6_avg > 5: risk_score += 8
    risk_score = min(risk_score, 100)

    if risk_score <= 25:
        risk_level = "LOW"; risk_css = "risk-low"
    elif risk_score <= 55:
        risk_level = "MEDIUM"; risk_css = "risk-medium"
    else:
        risk_level = "HIGH"; risk_css = "risk-high"

    # Payment trend
    improving = False
    deteriorating = False
    if len(last_6) >= 4:
        first_half = sum(last_6[:3]) / 3
        second_half = sum(last_6[3:]) / max(len(last_6[3:]), 1)
        if second_half < first_half * 0.7:
            improving = True
        elif second_half > first_half * 1.5 and first_half > 0:
            deteriorating = True

    # Consecutive streaks
    current_streak = 0
    for d in reversed(dpds):
        if d <= 5:
            current_streak += 1
        else:
            break

    max_consecutive_late = 0
    current_late = 0
    for d in dpds:
        if d > 0:
            current_late += 1
            max_consecutive_late = max(max_consecutive_late, current_late)
        else:
            current_late = 0

    # Outstanding from page 2
    total_outstanding = info.get("total_outstanding", 0)

    return {
        "total_emis": total_emis,
        "paid": paid, "late": late, "bounced_status": bounced_status, "unpaid": unpaid,
        "dpds": dpds, "peak_dpd": peak_dpd, "avg_dpd": avg_dpd,
        "zero_dpd_count": zero_dpd_count,
        "dpd_30_plus": dpd_30_plus, "dpd_60_plus": dpd_60_plus, "dpd_90_plus": dpd_90_plus,
        "l12_peak": l12_peak, "l12_avg": l12_avg, "l12_zero": l12_zero,
        "l6_peak": l6_peak, "l6_avg": l6_avg,
        "total_deposit": total_deposit, "total_emi_due": total_emi_due,
        "total_receipts_amt": total_receipts_amt,
        "total_bounces": total_bounces,
        "remaining_emis": remaining_emis,
        "coll_efficiency": coll_efficiency,
        "on_time_rate": on_time_rate,
        "bounce_rate": bounce_rate,
        "track_status": track_status, "track_label": track_label, "track_color": track_color,
        "risk_score": risk_score, "risk_level": risk_level, "risk_css": risk_css,
        "improving": improving, "deteriorating": deteriorating,
        "current_streak": current_streak,
        "max_consecutive_late": max_consecutive_late,
        "tenure": tenure,
        "loan_amt": info["loan_amount"],
        "emi_amt": info["emi_amount"],
        "total_outstanding": total_outstanding,
        # Statistical
        "std_dev_dpd": std_dev_dpd,
        "variance_dpd": variance_dpd,
        "cv_dpd": cv_dpd,
        "pri": pri,
        "delinquency_ratio": delinquency_ratio,
        "weighted_avg_dpd": weighted_avg_dpd,
        "median_dpd": median_dpd,
        "p90_dpd": p90_dpd,
        "deposit_coverage": deposit_coverage,
        "interest_burden": interest_burden,
        "repayment_progress": repayment_progress,
        "avg_emi_ratio": avg_emi_ratio,
    }


def build_emi_dataframe(final_data):
    rows = []
    for row in final_data:
        due = row["Due Date"].strftime("%d-%b-%Y") if row["Due Date"] else "—"
        dep = row["Dep. Date"].strftime("%d-%b-%Y") if row["Dep. Date"] else "—"
        emi = row["EMI"]
        dep_amt = row["Dep. Amt"]
        pct = (dep_amt / emi * 100) if emi > 0 else 0
        short_excess = emi - dep_amt
        rows.append({
            "Sr": row["Sr No"],
            "Due Date": due,
            "Dep. Date": dep,
            "Dep. Amt (₹)": dep_amt,
            "% of EMI": pct,
            "Short/Excess (₹)": short_excess,
            "DPD": row.get("DPD", 0),
        })
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════
# TITLE + UPLOAD
# ═══════════════════════════════════════════════════════════════
st.markdown("""
<div style="text-align:center; padding: 10px 0 5px 0;">
    <span style="font-size:28px; font-weight:800; color:#fff;">📊 EMI Statement Analyzer</span>
    <span style="font-size:13px; color:#6b7280; display:block; margin-top:4px;">
        Manappuram Finance Ltd — Loan Account Analysis Engine
    </span>
</div>
""", unsafe_allow_html=True)

uploaded_file = st.file_uploader("Upload PDF Statement", type=["pdf"], key="pdf_upload")

# ═══════════════════════════════════════════════════════════════
# PROCESS
# ═══════════════════════════════════════════════════════════════
if uploaded_file is not None:
    fname = uploaded_file.name
    if st.session_state.filename != fname:
        with st.spinner(f"⏳ Analyzing {fname}..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = tmp.name
            try:
                logger = ParseLogger()
                result = process_pdf(tmp_path, logger)
                st.session_state.result = result
                st.session_state.filename = fname
                st.session_state.show_log = False
            except Exception as e:
                st.error(f"❌ Error: {e}")
                st.session_state.result = None
                st.session_state.filename = None
            finally:
                try:
                    os.unlink(tmp_path)
                except:
                    pass

# ═══════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════
if st.session_state.result is not None:
    result = st.session_state.result
    info = result["page1_info"]
    final_data = result["final_data"]
    logger = result["logger"]
    ins = compute_insights(result)

    client = info["client_name"] if info["client_name"] else "Unknown"
    account = info["loan_account"] if info["loan_account"] else "N/A"
    product = info["product"] if info["product"] != "NA" else "Loan"
    loan_status = info.get("loan_status", "Active")

    # ─── Account Header ───
    hc1, hc2, hc3 = st.columns([5, 1, 1])
    with hc1:
        outstanding_text = f" &nbsp;·&nbsp; Outstanding: {fmt_inr(ins['total_outstanding'])}" if ins['total_outstanding'] > 0 else ""
        st.markdown(f"""
        <div class="top-bar">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <h1>👤 {client}</h1>
                    <div class="sub">Account: {account} &nbsp;·&nbsp; Product: {product} &nbsp;·&nbsp; EMI: {fmt_inr(ins['emi_amt'])}{outstanding_text}</div>
                    <div class="acct">Tenure: {ins['tenure']}mo &nbsp;·&nbsp; Sanction: {fmt_inr(ins['loan_amt'])} &nbsp;·&nbsp; Paid: {ins['total_emis']}/{ins['tenure']} EMIs</div>
                </div>
                <div style="text-align:right;">
                    <span class="risk-badge {ins['risk_css']}">RISK: {ins['risk_level']} ({ins['risk_score']}/100)</span>
                    <div style="margin-top:6px;">
                        <span class="risk-badge {'risk-low' if ins['track_color']=='green' else 'risk-medium' if ins['track_color']=='amber' else 'risk-high'}">{ins['track_status']} — {ins['track_label']}</span>
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
    with hc2:
        if st.button("📋 Parse Log", use_container_width=True):
            st.session_state.show_log = not st.session_state.show_log
            st.rerun()
    with hc3:
        if final_data:
            excel_bytes = generate_excel(result)
            st.download_button(
                "📥 Export Excel",
                data=excel_bytes,
                file_name=f"{os.path.splitext(st.session_state.filename)[0]}_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    # ═══════════ ROW 1: PRIMARY KPIs ═══════════
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    with k1:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Loan Amount</div>
            <div class="kpi-value blue">{fmt_inr(ins['loan_amt'])}</div>
            <div class="kpi-sub">Sanctioned</div>
        </div>""", unsafe_allow_html=True)
    with k2:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Total Outstanding</div>
            <div class="kpi-value pink">{fmt_inr(ins['total_outstanding'])}</div>
            <div class="kpi-sub">Current balance due</div>
        </div>""", unsafe_allow_html=True)
    with k3:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Total Collected</div>
            <div class="kpi-value green">{fmt_inr(ins['total_receipts_amt'])}</div>
            <div class="kpi-sub">{ins['total_emis']} receipts</div>
        </div>""", unsafe_allow_html=True)
    with k4:
        eff_color = 'green' if ins['coll_efficiency'] >= 95 else 'amber' if ins['coll_efficiency'] >= 85 else 'red'
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Collection Efficiency</div>
            <div class="kpi-value {eff_color}">{ins['coll_efficiency']:.1f}%</div>
            <div class="kpi-sub stat-formula">Σ Deposited / Σ EMI Due</div>
        </div>""", unsafe_allow_html=True)
    with k5:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Bounces</div>
            <div class="kpi-value red">{ins['total_bounces']}</div>
            <div class="kpi-sub">Rate: {ins['bounce_rate']:.1f}%</div>
        </div>""", unsafe_allow_html=True)
    with k6:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Remaining EMIs</div>
            <div class="kpi-value purple">{ins['remaining_emis']}</div>
            <div class="kpi-sub">of {ins['tenure']} total</div>
        </div>""", unsafe_allow_html=True)

    # ═══════════ ROW 2: DPD ANALYSIS ═══════════
    st.markdown('<div class="section-head">📈 Delay Analysis (DPD)</div>', unsafe_allow_html=True)

    d1, d2, d3, d4, d5, d6 = st.columns(6)
    with d1:
        c = 'red' if ins['peak_dpd'] > 30 else 'amber' if ins['peak_dpd'] > 0 else 'green'
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Peak DPD (All Time)</div>
            <div class="kpi-value {c}">{ins['peak_dpd']}d</div>
            <div class="kpi-sub stat-formula">MAX(DPD₁..DPDₙ)</div>
        </div>""", unsafe_allow_html=True)
    with d2:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Avg DPD (All Time)</div>
            <div class="kpi-value white">{ins['avg_dpd']:.1f}d</div>
            <div class="kpi-sub stat-formula">Σ DPD / n</div>
        </div>""", unsafe_allow_html=True)
    with d3:
        c = 'red' if ins['l12_peak'] > 30 else 'amber' if ins['l12_peak'] > 0 else 'green'
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Last 12M Peak</div>
            <div class="kpi-value {c}">{ins['l12_peak']}d</div>
            <div class="kpi-sub">Avg: {ins['l12_avg']:.1f}d</div>
        </div>""", unsafe_allow_html=True)
    with d4:
        c = 'red' if ins['l6_peak'] > 30 else 'amber' if ins['l6_peak'] > 0 else 'green'
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Last 6M Peak</div>
            <div class="kpi-value {c}">{ins['l6_peak']}d</div>
            <div class="kpi-sub">Avg: {ins['l6_avg']:.1f}d</div>
        </div>""", unsafe_allow_html=True)
    with d5:
        otr_color = 'green' if ins['on_time_rate'] >= 80 else 'amber' if ins['on_time_rate'] >= 60 else 'red'
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">On-Time Rate</div>
            <div class="kpi-value {otr_color}">{ins['on_time_rate']:.0f}%</div>
            <div class="kpi-sub">{ins['zero_dpd_count']}/{ins['total_emis']} DPD=0</div>
        </div>""", unsafe_allow_html=True)
    with d6:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">DPD > 30 Count</div>
            <div class="kpi-value {'red' if ins['dpd_30_plus'] > 3 else 'amber' if ins['dpd_30_plus'] > 0 else 'green'}">{ins['dpd_30_plus']}</div>
            <div class="kpi-sub">DPD>60: {ins['dpd_60_plus']} · DPD>90: {ins['dpd_90_plus']}</div>
        </div>""", unsafe_allow_html=True)

    # ═══════════ ROW 3: STATISTICAL METRICS ═══════════
    st.markdown('<div class="section-head">📐 Statistical Analysis</div>', unsafe_allow_html=True)

    s1, s2, s3, s4, s5, s6 = st.columns(6)
    with s1:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Std Deviation (DPD)</div>
            <div class="kpi-value cyan">{ins['std_dev_dpd']:.2f}</div>
            <div class="kpi-sub stat-formula">σ = √[Σ(x-μ)²/(n-1)]</div>
        </div>""", unsafe_allow_html=True)
    with s2:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Median DPD</div>
            <div class="kpi-value white">{ins['median_dpd']:.0f}d</div>
            <div class="kpi-sub stat-formula">P50 percentile</div>
        </div>""", unsafe_allow_html=True)
    with s3:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">P90 DPD</div>
            <div class="kpi-value {'red' if ins['p90_dpd'] > 15 else 'amber' if ins['p90_dpd'] > 0 else 'green'}">{ins['p90_dpd']}d</div>
            <div class="kpi-sub stat-formula">90th percentile</div>
        </div>""", unsafe_allow_html=True)
    with s4:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Weighted Avg DPD</div>
            <div class="kpi-value white">{ins['weighted_avg_dpd']:.1f}d</div>
            <div class="kpi-sub stat-formula">Σ(DPDᵢ×wᵢ)/Σwᵢ</div>
        </div>""", unsafe_allow_html=True)
    with s5:
        pri_color = 'green' if ins['pri'] >= 80 else 'amber' if ins['pri'] >= 60 else 'red'
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Payment Regularity</div>
            <div class="kpi-value {pri_color}">{ins['pri']:.0f}%</div>
            <div class="kpi-sub stat-formula">EMIs (DPD≤5) / Total</div>
        </div>""", unsafe_allow_html=True)
    with s6:
        dr_color = 'green' if ins['delinquency_ratio'] < 20 else 'amber' if ins['delinquency_ratio'] < 40 else 'red'
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Delinquency Ratio</div>
            <div class="kpi-value {dr_color}">{ins['delinquency_ratio']:.1f}%</div>
            <div class="kpi-sub stat-formula">EMIs (DPD>0) / Total</div>
        </div>""", unsafe_allow_html=True)

    # ═══════════ ROW 4: FINANCIAL RATIOS ═══════════
    st.markdown('<div class="section-head">💰 Financial Ratios</div>', unsafe_allow_html=True)

    f1, f2, f3, f4 = st.columns(4)
    with f1:
        dc_color = 'green' if ins['deposit_coverage'] >= 0.95 else 'amber' if ins['deposit_coverage'] >= 0.85 else 'red'
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Deposit Coverage Ratio</div>
            <div class="kpi-value {dc_color}">{ins['deposit_coverage']:.3f}</div>
            <div class="kpi-sub stat-formula">Σ Deposited / Σ EMI Due (1.0 = full)</div>
        </div>""", unsafe_allow_html=True)
    with f2:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Repayment Progress</div>
            <div class="kpi-value cyan">{ins['repayment_progress']:.1f}%</div>
            <div class="kpi-sub stat-formula">Total Paid / (EMI × Tenure)</div>
        </div>""", unsafe_allow_html=True)
    with f3:
        trend = "📈 Improving" if ins["improving"] else ("📉 Worsening" if ins["deteriorating"] else "➡️ Stable")
        trend_color = "green" if ins["improving"] else ("red" if ins["deteriorating"] else "white")
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">6M Payment Trend</div>
            <div class="kpi-value {trend_color}">{trend}</div>
            <div class="kpi-sub">Streak: {ins['current_streak']}mo on-time</div>
        </div>""", unsafe_allow_html=True)
    with f4:
        st.markdown(f"""<div class="kpi-card">
            <div class="kpi-label">Max Consecutive Late</div>
            <div class="kpi-value {'red' if ins['max_consecutive_late'] > 3 else 'amber' if ins['max_consecutive_late'] > 1 else 'green'}">{ins['max_consecutive_late']}mo</div>
            <div class="kpi-sub">Worst late streak</div>
        </div>""", unsafe_allow_html=True)

    # ═══════════ ANALYST OBSERVATIONS ═══════════
    st.markdown('<div class="section-head">💡 Analyst Observations</div>', unsafe_allow_html=True)

    observations = []

    if ins["coll_efficiency"] >= 98:
        observations.append(("ok", f"✅ Excellent collection at {ins['coll_efficiency']:.1f}% — near-full recovery"))
    elif ins["coll_efficiency"] >= 90:
        observations.append(("warn", f"⚠️ Collection efficiency {ins['coll_efficiency']:.1f}% — minor gaps"))
    else:
        observations.append(("danger", f"🚨 Low collection efficiency {ins['coll_efficiency']:.1f}% — significant shortfall"))

    if ins["total_bounces"] > 0:
        observations.append(("danger", f"🔴 {ins['total_bounces']} NACH bounce(s) — bounce rate {ins['bounce_rate']:.1f}% — NACH mandate may need renewal"))

    if ins["total_outstanding"] > 0:
        observations.append(("warn", f"📋 Total Outstanding: {fmt_inr(ins['total_outstanding'])} (includes principal + charges + interest)"))

    if ins["current_streak"] >= 6:
        observations.append(("ok", f"✅ {ins['current_streak']} consecutive on-time payments — strong recent discipline"))
    elif ins["current_streak"] >= 3:
        observations.append(("ok", f"✅ {ins['current_streak']} consecutive on-time — improving behavior"))

    if ins["max_consecutive_late"] >= 3:
        observations.append(("danger", f"🚨 Max {ins['max_consecutive_late']} consecutive late payments — historic stress"))

    if ins["dpd_90_plus"] > 0:
        observations.append(("danger", f"🚨 {ins['dpd_90_plus']} EMI(s) DPD > 90 days — NPA risk"))

    if ins["std_dev_dpd"] > 15:
        observations.append(("warn", f"📊 High DPD volatility (σ={ins['std_dev_dpd']:.1f}) — inconsistent payment behavior"))
    elif ins["std_dev_dpd"] < 3 and ins["avg_dpd"] < 5:
        observations.append(("ok", f"📊 Very consistent payments (σ={ins['std_dev_dpd']:.1f}) — reliable borrower"))

    if ins["weighted_avg_dpd"] < ins["avg_dpd"] * 0.7 and ins["avg_dpd"] > 5:
        observations.append(("ok", f"📈 Weighted DPD ({ins['weighted_avg_dpd']:.1f}d) < Simple Avg ({ins['avg_dpd']:.1f}d) — recent improvement"))
    elif ins["weighted_avg_dpd"] > ins["avg_dpd"] * 1.3 and ins["avg_dpd"] > 2:
        observations.append(("danger", f"📉 Weighted DPD ({ins['weighted_avg_dpd']:.1f}d) > Simple Avg ({ins['avg_dpd']:.1f}d) — recent deterioration"))

    if ins["improving"]:
        observations.append(("ok", "📈 Positive trajectory in last 6 months"))
    elif ins["deteriorating"]:
        observations.append(("danger", "📉 Deteriorating — needs collection attention"))

    if ins["remaining_emis"] <= 6 and ins["remaining_emis"] > 0:
        observations.append(("warn", f"⏰ Only {ins['remaining_emis']} EMIs remaining — nearing maturity"))

    for obs_type, obs_text in observations:
        st.markdown(f'<div class="insight-box {obs_type}">{obs_text}</div>', unsafe_allow_html=True)

    # ═══════════ EMI SCHEDULE TABLE ═══════════
    if final_data:
        st.markdown('<div class="section-head">📅 EMI Repayment Schedule</div>', unsafe_allow_html=True)

        df = build_emi_dataframe(final_data)

        def color_pct(val):
            try:
                v = float(val)
            except:
                return ""
            if v >= 100: return "color: #22c55e; font-weight: 600;"
            if v >= 50: return "color: #fbbf24;"
            if v > 0: return "color: #fb923c;"
            return "color: #ef4444;"

        def color_dpd(val):
            try:
                v = int(val)
            except:
                return ""
            if v == 0: return "color: #22c55e;"
            if v <= 7: return "color: #a3e635;"
            if v <= 30: return "color: #fbbf24;"
            if v <= 60: return "color: #fb923c;"
            return "color: #ef4444; font-weight: bold;"

        def color_short(val):
            try:
                v = float(val)
            except:
                return ""
            if v <= 0: return "color: #22c55e;"
            if v > 0: return "color: #ef4444;"
            return ""

        styled = (
            df.style
            .map(color_pct, subset=["% of EMI"])
            .map(color_dpd, subset=["DPD"])
            .map(color_short, subset=["Short/Excess (₹)"])
            .format({
                "Dep. Amt (₹)": "₹{:,.0f}",
                "% of EMI": "{:.0f}%",
                "Short/Excess (₹)": "₹{:,.0f}",
            })
        )

        st.dataframe(
            styled,
            use_container_width=True,
            height=min(len(df) * 37 + 42, 700),
            hide_index=True,
            column_config={
                "Sr": st.column_config.NumberColumn("Sr", width="small"),
                "Due Date": st.column_config.TextColumn("Due Date", width="medium"),
                "Dep. Date": st.column_config.TextColumn("Dep. Date", width="medium"),
                "Dep. Amt (₹)": st.column_config.NumberColumn("Deposited", width="small"),
                "% of EMI": st.column_config.NumberColumn("% of EMI", width="small"),
                "Short/Excess (₹)": st.column_config.NumberColumn("Short By", width="small"),
                "DPD": st.column_config.NumberColumn("DPD", width="small"),
            },
        )

    else:
        st.warning("⚠️ No EMI data could be extracted.")

    # ═══════════ PARSE LOG ═══════════
    if st.session_state.show_log:
        st.markdown('<div class="section-head">📝 Parse Log</div>', unsafe_allow_html=True)
        entries = logger.get_logs()
        if entries:
            html = '<div class="log-box">'
            for e in entries:
                cls = "bounce" if e["level"] == "bounce" else ("success" if e["level"] == "success" else "")
                html += f'<div class="log-row"><span class="log-ts">{e["timestamp"]}</span><span class="log-msg {cls}">{e["message"]}</span></div>'
            html += '</div>'
            st.markdown(html, unsafe_allow_html=True)

elif uploaded_file is None:
    st.markdown("")
    st.info("👆 Upload a Manappuram Finance PDF statement above to begin analysis")
