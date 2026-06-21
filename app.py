"""
NCAA Transfer Portal Analytics — Streamlit Dashboard
Run: streamlit run app.py

Nine tabs:
  About            — methodology, role scale, metric glossary
  Major Takeaways  — broadcast-style summary: route report, NIL era split, top transfers
  Scope & Limits   — BPM caveats, data coverage gaps, what the model does and doesn't measure
  Transfer Overview — season KPIs, tier-to-tier flow heatmap, volume by season
  Individual Scores — player-level BPM verdicts, context scores, stat table
  League Trends     — OBPM/DBPM by tier, season-over-season success rates
  Recruit Profiles  — stat floors by destination tier, origin, and position
  Player Fit Finder — input a player profile → comparable portal transfers with projections
  Coach Search      — input program criteria → Transfer Pool + Expected Contribution tool

Data: PostgreSQL (local). DB connection via environment variables in db.py.
Sidebar filters (season, position, destination tier) apply to Transfer Overview,
Individual Scores, and League Trends. All other tabs have independent inputs.

BPM (Box Plus/Minus) from College Basketball Reference is the core metric.
It measures points added per 100 possessions above a replacement-level player,
adjusted for pace and strength of schedule.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from db import DB_CONFIG

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PivotHoops | Transfer Intelligence",
    page_icon="🏀",
    layout="wide",
)

# ── Password gate ─────────────────────────────────────────────────────────────
# Only active when APP_PASSWORD is set in st.secrets (i.e. on Streamlit Cloud).
# Locally there is no secret, so the gate is skipped automatically.
def _check_password():
    try:
        required = st.secrets["APP_PASSWORD"]
    except Exception:
        return   # no password configured — skip gate (local dev)

    if st.session_state.get("authenticated"):
        return

    st.html("""
    <div style="max-width:400px; margin:80px auto; text-align:center;">
      <div style="font-size:2.5rem; font-weight:900; color:#FF6B00;
                  letter-spacing:0.1em; margin-bottom:4px;">PIVOT HOOPS</div>
      <div style="color:#888; font-size:0.85rem; margin-bottom:32px;">
        Transfer Portal Intelligence
      </div>
    </div>
    """)
    col = st.columns([1, 2, 1])[1]
    with col:
        pw = st.text_input("Password", type="password", label_visibility="collapsed",
                           placeholder="Enter password")
        if st.button("Enter", use_container_width=True, type="primary"):
            if pw == required:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Wrong password.")
    st.stop()

_check_password()

# ── PivotHoops Brand CSS ──────────────────────────────────────────────────────
st.html("""
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Rajdhani:wght@600;700&display=swap" rel="stylesheet">
<style>
  /* ── Global reset ── */
  .main { background-color: #0D0D0D; }

  /* ── Branded header ── */
  .pivothoops-header {
    background: linear-gradient(135deg, #1A1A1A 0%, #0D0D0D 60%, #1a0a00 100%);
    border-bottom: 2px solid #FF6B00;
    padding: 18px 24px 14px 24px;
    margin-bottom: 24px;
    border-radius: 4px;
  }
  .pivothoops-header .brand-name {
    font-family: 'Bebas Neue', 'Rajdhani', sans-serif;
    font-size: 2.4rem;
    letter-spacing: 0.08em;
    color: #FF6B00;
    line-height: 1;
    margin: 0;
  }
  .pivothoops-header .brand-subtitle {
    font-family: 'Rajdhani', sans-serif;
    font-size: 0.85rem;
    letter-spacing: 0.18em;
    color: #888888;
    text-transform: uppercase;
    margin-top: 3px;
  }

  /* ── Metric cards — orange left border ── */
  [data-testid="metric-container"] {
    background: #1A1A1A;
    border-left: 3px solid #FF6B00;
    border-radius: 4px;
    padding: 12px 16px !important;
  }
  [data-testid="metric-container"] label {
    color: #888888 !important;
    font-size: 0.78rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  [data-testid="metric-container"] [data-testid="metric-value"] {
    color: #FFFFFF !important;
    font-family: 'Rajdhani', sans-serif;
    font-size: 1.6rem;
    font-weight: 700;
  }

  /* ── Tabs — bolder, larger, orange active glow ── */
  .stTabs [data-baseweb="tab-list"] {
    background: #1A1A1A;
    border-radius: 4px;
    padding: 4px;
    gap: 4px;
  }
  .stTabs [data-baseweb="tab"] {
    font-family: 'Rajdhani', sans-serif;
    font-size: 0.95rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: #888888;
    padding: 8px 18px;
    border-radius: 3px;
    border: none;
    background: transparent;
  }
  .stTabs [aria-selected="true"] {
    color: #FF6B00 !important;
    background: #0D0D0D !important;
    box-shadow: 0 0 10px rgba(255, 107, 0, 0.35), 0 2px 0 #FF6B00 !important;
  }
  .stTabs [data-baseweb="tab"]:hover {
    color: #FF8C38 !important;
  }

  /* ── Dataframe headers orange ── */
  [data-testid="stDataFrame"] th,
  .dvn-scroller .col_heading {
    color: #FF6B00 !important;
    font-weight: 700 !important;
    font-family: 'Rajdhani', sans-serif !important;
    letter-spacing: 0.04em;
    border-bottom: 1px solid #FF6B00 !important;
  }

  /* ── Sidebar orange gradient ── */
  [data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1a0a00 0%, #1A1A1A 40%, #0D0D0D 100%) !important;
    border-right: 1px solid #FF6B00;
  }
  [data-testid="stSidebar"] .sidebar-content {
    background: transparent !important;
  }

  /* ── Buttons orange ── */
  .stButton > button {
    background: #FF6B00 !important;
    color: #FFFFFF !important;
    font-family: 'Rajdhani', sans-serif;
    font-weight: 700;
    letter-spacing: 0.06em;
    border: none !important;
    border-radius: 3px !important;
    padding: 8px 24px !important;
    transition: background 0.2s ease;
  }
  .stButton > button:hover {
    background: #FF8C38 !important;
    color: #FFFFFF !important;
  }
  .stButton > button:active {
    background: #e05e00 !important;
  }

  /* ── Remove Streamlit footer branding ── */
  #MainMenu { visibility: hidden; }
  footer { visibility: hidden; }
  footer:after { visibility: hidden; }
  [data-testid="stToolbar"] { display: none; }
  .viewerBadge_container__1QSob { display: none !important; }
</style>
""")

# D1 basketball splits into four competitive tiers. sub_d1 (JUCO/D2/D3) and
# international are tracked as origin only — BPM data doesn't exist for
# non-D1 destinations, so those routes can't be scored.
TIER_ORDER = ["high_major", "high_mid_major", "mid_major", "low_major"]
ALL_TIERS  = TIER_ORDER + ["sub_d1", "international"]  # origin-side only
TIER_LABELS = {
    "high_major":     "High Major",
    "high_mid_major": "High Mid Major",
    "mid_major":      "Mid Major",
    "low_major":      "Low Major",
    "sub_d1":         "Sub-D1 (JUCO/D2/D3)",
    "international":  "International",
}
# Lower number = harder conference. Used to classify portal moves as up, lateral, or down.
TIER_RANK = {"high_major": 1, "high_mid_major": 2, "mid_major": 3, "low_major": 4}
TIER_COLORS = {
    "High Major":           "#FF6B00",
    "High Mid Major":       "#FF8C38",
    "Mid Major":            "#FFB347",
    "Low Major":            "#FFCC80",
    "Sub-D1 (JUCO/D2/D3)": "#555555",
    "International":        "#888888",
}

# Chart colors for verdict labels (bright, for plotly rendering).
# Table cell backgrounds are intentionally different — see _color_verdict_cell().
VERDICT_COLORS = {
    "Above Projection":       "#FF6B00",
    "High-Impact Acquisition":"#FF8C38",
    "Positive Acquisition":   "#FFB347",
    "Lateral Move":           "#888888",
    "Below Projection":       "#2D2D2D",
}

# ── BPM floors and caps (applied consistently in SQL and Python) ──────────────
BPM_CAP       = 15   # clip at ±15 to prevent outlier seasons from skewing peer group baselines
MIN_GAMES     = 12   # below 12 games, BPM variance is too high to be meaningful
MIN_PEER_SIZE = 5    # transfer premium requires at least 5 peers on the same route

# ── Player Fit Finder search tolerances ──────────────────────────────────────
# These windows are wide enough to return enough comps but tight enough to stay positionally relevant.
HEIGHT_TOL = 2     # ±2 inches — roughly one position tier of natural height variance
WEIGHT_TOL = 15    # ±15 lbs — captures in-season body composition range
BPM_TOL    = 2.0   # ±2.0 BPM — same production tier; fallback search expands to ±3.0
BIRTH_TOL  = 2     # ±2 years — keeps comparisons within the same recruiting generation

# ── Route success thresholds for "Works / Mixed / Risky" labels ──────────────
# Calibrated against the full dataset: >55% High-Impact = consistently productive,
# 38–55% = route-dependent, <38% = historically poor outcomes.
ROUTE_WORKS_PCT  = 55
ROUTE_VIABLE_PCT = 38

# Position expansion: G/F and F/C are hybrid positions — include them when either base is selected
POS_EXPAND = {
    "G": ["G", "G/F"],
    "F": ["G/F", "F", "F/C"],
    "C": ["F/C", "C"],
}
def fmt_season(s: str) -> str:
    """Convert DB season '2023-24' to display label '2023 Transfer Class'."""
    if s and "-" in s:
        return s.split("-")[0] + " Transfer Class"
    return s

def expand_positions(selected: list) -> list:
    """Given a list like ['G','F'], return all DB position values to match."""
    if not selected:
        return []
    out = []
    for p in selected:
        out.extend(POS_EXPAND.get(p, [p]))
    return list(dict.fromkeys(out))  # dedupe, preserve order

VERDICT_ORDER = ["Exceeded Expectations", "High Value", "Solid Addition", "Neutral", "Didn't Fit"]
VERDICT_DISPLAY_ORDER = ["Above Projection", "High-Impact Acquisition", "Positive Acquisition", "Lateral Move", "Below Projection"]

# Front-office display labels — maps internal DB values to scouting-report language.
# SQL queries and DB storage keep the original strings; only UI display changes.
VERDICT_DISPLAY = {
    "Exceeded Expectations": "Above Projection",
    "High Value":            "High-Impact Acquisition",
    "Solid Addition":        "Positive Acquisition",
    "Neutral":               "Lateral Move",
    "Didn't Fit":            "Below Projection",
}

def fmt_verdict(v: str) -> str:
    """Translate internal verdict to front-office display language."""
    return VERDICT_DISPLAY.get(v, v) if v else v

# Position-relative physical profile buckets for Coach Search match scoring.
# "Short/Average/Tall" and "Lean/Average/Heavy" are relative to what's normal at each position,
# not absolute measurements — a 6'4" C is Short; a 6'4" G is Tall.
HEIGHT_BUCKETS = {
    "G":   {"Short": (0, 73),  "Average": (74, 76), "Tall": (77, 999)},
    "G/F": {"Short": (0, 75),  "Average": (76, 78), "Tall": (79, 999)},
    "F":   {"Short": (0, 77),  "Average": (78, 80), "Tall": (81, 999)},
    "F/C": {"Short": (0, 79),  "Average": (80, 82), "Tall": (83, 999)},
    "C":   {"Short": (0, 81),  "Average": (82, 84), "Tall": (85, 999)},
}
WEIGHT_BUCKETS = {
    "G":   {"Lean": (0, 189), "Average": (190, 210), "Heavy": (211, 999)},
    "G/F": {"Lean": (0, 199), "Average": (200, 220), "Heavy": (221, 999)},
    "F":   {"Lean": (0, 209), "Average": (210, 230), "Heavy": (231, 999)},
    "F/C": {"Lean": (0, 219), "Average": (220, 240), "Heavy": (241, 999)},
    "C":   {"Lean": (0, 234), "Average": (235, 260), "Heavy": (261, 999)},
}


def _phys_cat(val, position, buckets):
    """Return the physical category (Short/Average/Tall or Lean/Average/Heavy) for a player."""
    if pd.isna(val) or not position or position not in buckets:
        return None
    for cat, (lo, hi) in buckets[position].items():
        if lo <= int(val) <= hi:
            return cat
    return list(buckets[position])[-1]   # above max = top bucket

# ── DB connection ─────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(**DB_CONFIG)


@st.cache_data(ttl=300)
def query(sql: str, params=None) -> pd.DataFrame:
    conn = get_conn()
    try:
        return pd.read_sql(sql, conn, params=params)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.html("""
<div style="
  padding: 12px 8px 16px 8px;
  border-bottom: 1px solid #FF6B00;
  margin-bottom: 12px;
">
  <div style="
    font-family: 'Bebas Neue', 'Rajdhani', sans-serif;
    font-size: 1.6rem;
    letter-spacing: 0.1em;
    color: #FF6B00;
    line-height: 1;
  ">🏀 PIVOT HOOPS</div>
  <div style="
    font-size: 0.7rem;
    letter-spacing: 0.14em;
    color: #555555;
    text-transform: uppercase;
    margin-top: 4px;
  ">Transfer Intelligence</div>
</div>
""")
st.sidebar.markdown("**Filters**")

seasons = query("SELECT DISTINCT season FROM transfers ORDER BY season")["season"].tolist()
nil_era_seasons = [s for s in seasons if s >= "2022-23"]
st.sidebar.markdown("**Season**")
era_mode = st.sidebar.radio("Era", ["NIL Era (2022-23+)", "All Seasons"], horizontal=True)
default_seasons = nil_era_seasons if era_mode == "NIL Era (2022-23+)" else seasons
selected_seasons = st.sidebar.multiselect("Seasons", seasons, default=default_seasons, format_func=fmt_season)

positions = ["All", "G", "G/F", "F", "F/C", "C"]
selected_pos = st.sidebar.selectbox("Position", positions)

dest_tiers = ["All"] + TIER_ORDER
selected_dest = st.sidebar.selectbox("Destination Tier", dest_tiers, format_func=lambda x: TIER_LABELS.get(x, x))

st.sidebar.markdown("---")
st.sidebar.caption("Data: CBB Reference · Kaggle · On3 · 2021–25")


def season_filter(col="tr.season"):
    """Return a (sql_clause, params) pair for the season sidebar filter."""
    if not selected_seasons:
        return "1=1", []
    placeholders = ",".join(["%s"] * len(selected_seasons))
    return f"{col} IN ({placeholders})", selected_seasons


def pos_filter(col="p.position"):
    """Return a (sql_clause, params) pair for the position sidebar filter."""
    if selected_pos == "All":
        return "1=1", []
    return f"{col} = %s", [selected_pos]


def dest_filter(col="c_to.tier"):
    """Return a (sql_clause, params) pair for the destination tier sidebar filter."""
    if selected_dest == "All":
        return "1=1", []
    return f"{col} = %s", [selected_dest]


def build_where(*filters):
    """Combine multiple (clause, params) filter pairs into a single WHERE clause."""
    clauses, params = [], []
    for clause, p in filters:
        clauses.append(clause)
        params.extend(p)
    return "WHERE " + " AND ".join(clauses), params


# ── Page tabs ─────────────────────────────────────────────────────────────────
tab0, tabX, tabL, tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🏠 About",
    "📣 Major Takeaways",
    "⚠️ Scope & Limits",
    "📊 Transfer Overview",
    "👤 Individual Scores",
    "📈 League Trends",
    "🎯 Recruit Profiles",
    "🔍 Player Fit Finder",
    "🏀 Coach Search",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 0 — About / Intro
# ═══════════════════════════════════════════════════════════════════════════════
with tab0:
    st.html("""
    <div style="max-width:860px; margin:0 auto; padding: 8px 0 32px 0;">

      <div style="display:flex; align-items:center; gap:16px; margin-bottom:28px;">
        <div style="font-family:'Bebas Neue','Rajdhani',sans-serif; font-size:3.2rem;
                    color:#FF6B00; letter-spacing:0.06em; line-height:1;">🏀 PIVOT HOOPS</div>
        <div style="border-left:2px solid #FF6B00; padding-left:16px;">
          <div style="color:#FFFFFF; font-size:1.1rem; font-weight:700; letter-spacing:0.04em;">
            Transfer Portal Intelligence</div>
          <div style="color:#888; font-size:0.82rem; margin-top:2px;">
            NCAA Men's Basketball &nbsp;·&nbsp; 2021–2026 &nbsp;·&nbsp; NIL Era</div>
        </div>
      </div>

      <p style="color:#CCCCCC; font-size:1.0rem; line-height:1.7; max-width:720px; margin-bottom:28px;">
        Pivot Hoops is a data platform built for <strong style="color:#FF6B00;">coaches</strong> and
        <strong style="color:#FF6B00;">players</strong> navigating the transfer portal.
        We track every D1 portal movement — over <strong>9,000 transfers</strong> across
        <strong>350 schools</strong> — and score each one using real Box Plus/Minus, usage rate,
        and shooting efficiency data from College Basketball Reference. No projections. No
        estimates. Real stats only.
      </p>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:32px;">

        <div style="background:#1A1A1A; border-left:3px solid #FF6B00; border-radius:4px; padding:18px 20px;">
          <div style="color:#FF6B00; font-weight:700; font-size:0.9rem; letter-spacing:0.08em;
                      text-transform:uppercase; margin-bottom:10px;">For Coaches</div>
          <p style="color:#CCCCCC; font-size:0.88rem; line-height:1.6; margin:0;">
            Use the <strong>Coach Search</strong> tab to find your next portal target.
            Filter by the role you need — a starter, a key rotation piece, a backup — and
            see every player who historically delivered that at your tier.
            The <strong>Transfer Pool</strong> shows all 9,000+ portal movements with
            🟢 <em>In Your Range</em> flags so you know who's realistically attainable.
          </p>
        </div>

        <div style="background:#1A1A1A; border-left:3px solid #FF8C38; border-radius:4px; padding:18px 20px;">
          <div style="color:#FF8C38; font-weight:700; font-size:0.9rem; letter-spacing:0.08em;
                      text-transform:uppercase; margin-bottom:10px;">For Players</div>
          <p style="color:#CCCCCC; font-size:0.88rem; line-height:1.6; margin:0;">
            Use the <strong>Player Fit Finder</strong> tab. Enter your position, current tier,
            BPM, usage, and physical profile. We'll show you which tiers and
            <em>specific programs</em> players like you have thrived at after transferring —
            not just tier cards, but actual school names ranked by success rate.
          </p>
        </div>
      </div>

      <div style="color:#FF6B00; font-weight:700; font-size:0.9rem; letter-spacing:0.08em;
                  text-transform:uppercase; margin-bottom:12px;">Understanding the Metrics</div>

      <div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:12px; margin-bottom:28px;">
        <div style="background:#1A1A1A; border-radius:4px; padding:14px 16px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.9rem; margin-bottom:6px;">BPM</div>
          <div style="color:#888; font-size:0.82rem; line-height:1.5;">
            Box Plus/Minus — points above average your team scores vs allows per 100 possessions
            with you on the floor. The closest thing to a single "how good is this player" number
            in college basketball.
          </div>
        </div>
        <div style="background:#1A1A1A; border-radius:4px; padding:14px 16px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.9rem; margin-bottom:6px;">Skill Index</div>
          <div style="color:#888; font-size:0.82rem; line-height:1.5;">
            What drives every verdict. A blend of BPM (50%), usage rate (25%), and true-shooting%
            (25%), so a reduced-role player who's still efficient doesn't grade out the same as one
            who just wasn't productive. Computed both before and after the transfer on the same
            scale, so "did this move work" is a direct comparison, not just a raw BPM number.
          </div>
        </div>
        <div style="background:#1A1A1A; border-radius:4px; padding:14px 16px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.9rem; margin-bottom:6px;">Context Score</div>
          <div style="color:#888; font-size:0.82rem; line-height:1.5;">
            Our internal ranking metric: BPM × Tier Difficulty × Role Adjustment.
            A +3 BPM at Kentucky means more than +3 at a low-major — Context Score
            captures that. Used for sorting and comparison, not shown to coaches as-is.
          </div>
        </div>
        <div style="background:#1A1A1A; border-radius:4px; padding:14px 16px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.9rem; margin-bottom:6px;">Transfer Premium</div>
          <div style="color:#888; font-size:0.82rem; line-height:1.5;">
            How much a player beat (or missed) the expected BPM for their type of move.
            Positive = exceeded what similar transfers delivered.
            Negative = underperformed relative to comparable portal peers.
          </div>
        </div>
      </div>

      <div style="color:#FF6B00; font-weight:700; font-size:0.9rem; letter-spacing:0.08em;
                  text-transform:uppercase; margin-bottom:12px;">Player Role Scale</div>

      <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:28px;">
        <div style="background:#1A1A1A; border-top:2px solid #FF6B00; border-radius:4px;
                    padding:10px 16px; min-width:130px; text-align:center;">
          <div style="font-size:1.2rem;">🏆</div>
          <div style="color:#FF6B00; font-weight:700; font-size:0.85rem;">Franchise Asset</div>
          <div style="color:#888; font-size:0.75rem; margin-top:2px;">BPM ≥ 6.0</div>
          <div style="color:#555; font-size:0.72rem; margin-top:2px;">All-conference caliber</div>
        </div>
        <div style="background:#1A1A1A; border-top:2px solid #FF8C38; border-radius:4px;
                    padding:10px 16px; min-width:130px; text-align:center;">
          <div style="font-size:1.2rem;">🌟</div>
          <div style="color:#FF8C38; font-weight:700; font-size:0.85rem;">Primary Contributor</div>
          <div style="color:#888; font-size:0.75rem; margin-top:2px;">BPM 3.0 – 5.9</div>
          <div style="color:#555; font-size:0.72rem; margin-top:2px;">Starting-caliber at any level</div>
        </div>
        <div style="background:#1A1A1A; border-top:2px solid #FFB347; border-radius:4px;
                    padding:10px 16px; min-width:130px; text-align:center;">
          <div style="font-size:1.2rem;">💪</div>
          <div style="color:#FFB347; font-weight:700; font-size:0.85rem;">Rotation Contributor</div>
          <div style="color:#888; font-size:0.75rem; margin-top:2px;">BPM 1.0 – 2.9</div>
          <div style="color:#555; font-size:0.72rem; margin-top:2px;">Reliable rotation piece</div>
        </div>
        <div style="background:#1A1A1A; border-top:2px solid #888; border-radius:4px;
                    padding:10px 16px; min-width:130px; text-align:center;">
          <div style="font-size:1.2rem;">✅</div>
          <div style="color:#888; font-weight:700; font-size:0.85rem;">Depth Piece</div>
          <div style="color:#888; font-size:0.75rem; margin-top:2px;">BPM -0.5 – 0.9</div>
          <div style="color:#555; font-size:0.72rem; margin-top:2px;">Quality depth</div>
        </div>
        <div style="background:#1A1A1A; border-top:2px solid #444; border-radius:4px;
                    padding:10px 16px; min-width:130px; text-align:center;">
          <div style="font-size:1.2rem;">📋</div>
          <div style="color:#666; font-weight:700; font-size:0.85rem;">Developmental</div>
          <div style="color:#888; font-size:0.75rem; margin-top:2px;">BPM &lt; -0.5</div>
          <div style="color:#555; font-size:0.72rem; margin-top:2px;">Projection player</div>
        </div>
      </div>

      <div style="color:#FF6B00; font-weight:700; font-size:0.9rem; letter-spacing:0.08em;
                  text-transform:uppercase; margin-bottom:12px;">Tab Guide</div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
        <div style="background:#1A1A1A; border-radius:4px; padding:12px 16px; display:flex; gap:10px;">
          <span style="font-size:1.1rem;">📊</span>
          <div>
            <div style="color:#FFFFFF; font-weight:700; font-size:0.85rem;">Transfer Overview</div>
            <div style="color:#666; font-size:0.78rem; margin-top:3px;">Volume by season, tier flow heatmap, transfer trends at a glance.</div>
          </div>
        </div>
        <div style="background:#1A1A1A; border-radius:4px; padding:12px 16px; display:flex; gap:10px;">
          <span style="font-size:1.1rem;">👤</span>
          <div>
            <div style="color:#FFFFFF; font-weight:700; font-size:0.85rem;">Individual Scores</div>
            <div style="color:#666; font-size:0.78rem; margin-top:3px;">Search any player, school, or filter by role and verdict to see scored transfers.</div>
          </div>
        </div>
        <div style="background:#1A1A1A; border-radius:4px; padding:12px 16px; display:flex; gap:10px;">
          <span style="font-size:1.1rem;">📈</span>
          <div>
            <div style="color:#FFFFFF; font-weight:700; font-size:0.85rem;">League Trends</div>
            <div style="color:#666; font-size:0.78rem; margin-top:3px;">Success rates by tier route, offense vs defense contribution, physical profiles.</div>
          </div>
        </div>
        <div style="background:#1A1A1A; border-radius:4px; padding:12px 16px; display:flex; gap:10px;">
          <span style="font-size:1.1rem;">🎯</span>
          <div>
            <div style="color:#FFFFFF; font-weight:700; font-size:0.85rem;">Recruit Profiles</div>
            <div style="color:#666; font-size:0.78rem; margin-top:3px;">What the average successful recruit looks like for each tier-to-tier move.</div>
          </div>
        </div>
        <div style="background:#1A1A1A; border-radius:4px; padding:12px 16px; display:flex; gap:10px;">
          <span style="font-size:1.1rem;">🔍</span>
          <div>
            <div style="color:#FFFFFF; font-weight:700; font-size:0.85rem;">Player Fit Finder</div>
            <div style="color:#666; font-size:0.78rem; margin-top:3px;">Players: enter your stats and see exactly which schools have had success with your profile.</div>
          </div>
        </div>
        <div style="background:#1A1A1A; border-radius:4px; padding:12px 16px; display:flex; gap:10px;">
          <span style="font-size:1.1rem;">🏀</span>
          <div>
            <div style="color:#FFFFFF; font-weight:700; font-size:0.85rem;">Coach Search</div>
            <div style="color:#666; font-size:0.78rem; margin-top:3px;">Coaches: find every portal player who fits the role you need, ranked by historical fit at your tier.</div>
          </div>
        </div>
      </div>

    </div>
    """)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB X — Major Takeaways
# ═══════════════════════════════════════════════════════════════════════════════
with tabX:
    st.title("📣 Major Takeaways")
    st.caption("Five years of portal data. Here's what it says.")

    try:
        # ── Hero numbers ──────────────────────────────────────────────────────
        hero = query("""
            SELECT
                COUNT(*)                                                        AS total_scored,
                ROUND(100.0 * COUNT(CASE WHEN transfer_verdict IN
                    ('High Value','Exceeded Expectations','Solid Addition') THEN 1 END)
                    / COUNT(*), 1)                                              AS hv_pct,
                ROUND(AVG(bpm_after)::numeric, 2)                              AS avg_bpm,
                MAX(bpm_after)                                                  AS best_bpm,
                (SELECT full_name FROM individual_transfer_scores
                 ORDER BY bpm_after DESC NULLS LAST LIMIT 1)                   AS best_player
            FROM individual_transfer_scores
            WHERE to_tier NOT IN ('sub_d1','international')
              AND from_tier NOT IN ('sub_d1','international')
        """)
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Transfers Analyzed", f"{int(hero['total_scored'][0]):,}")
        h2.metric("Positive Outcome Rate", f"{hero['hv_pct'][0]}%")
        h3.metric("Avg BPM After Transfer", f"{hero['avg_bpm'][0]:+.2f}")
        h4.metric("Best Transfer Ever", f"+{hero['best_bpm'][0]:.1f} BPM")
        h4.caption(f"🐐 {hero['best_player'][0]}")

        st.markdown("---")

        # ── Section 1: What works / doesn't ───────────────────────────────────
        st.subheader("Route Report")
        st.caption("Tier-to-tier routes ranked by High Value rate. Moving up is the play. Moving down rarely delivers.")

        route_df = query("""
            SELECT from_tier, to_tier, COUNT(*) as n,
                ROUND(100.0 * COUNT(CASE WHEN transfer_verdict IN
                    ('High Value','Exceeded Expectations','Solid Addition') THEN 1 END) / COUNT(*), 1) AS hv_pct,
                ROUND(AVG(bpm_after)::numeric,2) AS avg_bpm
            FROM individual_transfer_scores
            WHERE to_tier NOT IN ('sub_d1','international')
              AND from_tier NOT IN ('sub_d1','international')
            GROUP BY from_tier, to_tier HAVING COUNT(*) >= 15  -- 15 players minimum for a stable HV%
            ORDER BY hv_pct DESC
        """)
        route_df["route"]      = route_df.apply(
            lambda r: f"{TIER_LABELS.get(r['from_tier'], r['from_tier'])} to {TIER_LABELS.get(r['to_tier'], r['to_tier'])}",
            axis=1,
        )
        route_df["verdict"]    = route_df["hv_pct"].apply(
            lambda p: "✅ Works" if p >= ROUTE_WORKS_PCT else ("⚠️ Mixed" if p >= ROUTE_VIABLE_PCT else "❌ Avoid")
        )
        route_df["direction"]  = route_df.apply(
            lambda r: "⬆️ Moving Up" if TIER_RANK.get(r["to_tier"],4) < TIER_RANK.get(r["from_tier"],4)
            else ("➡️ Lateral" if r["from_tier"] == r["to_tier"] else "⬇️ Moving Down"), axis=1
        )

        col_works, col_avoids = st.columns(2)
        with col_works:
            top = route_df[route_df["hv_pct"] >= 50].head(6)
            st.markdown("**🏆 Routes That Work (50%+ High Value)**")
            for _, r in top.iterrows():
                st.markdown(
                    f"- **{r['route']}** — {r['hv_pct']:.0f}% Positive · "
                    f"avg +{r['avg_bpm']:.1f} BPM · n={int(r['n'])}"
                )
        with col_avoids:
            bot = route_df[route_df["hv_pct"] < 20].tail(6)
            st.markdown("**Routes That Miss (under 20% Positive Outcome)**")
            for _, r in bot.iterrows():
                st.markdown(
                    f"- **{r['route']}** — {r['hv_pct']:.0f}% Positive · "
                    f"avg {r['avg_bpm']:+.1f} BPM · n={int(r['n'])}"
                )

        fig_route = px.bar(
            route_df.sort_values("hv_pct"),
            x="hv_pct", y="route", color="direction",
            orientation="h",
            color_discrete_map={
                "⬆️ Moving Up": "#FF6B00",
                "➡️ Lateral":   "#FF8C38",
                "⬇️ Moving Down": "#444444",
            },
            labels={"hv_pct": "Positive Outcome %", "route": "", "direction": "Move"},
            text="hv_pct",
        )
        fig_route.update_traces(texttemplate="%{text:.0f}%", textposition="outside")
        fig_route.add_vline(x=50, line_dash="dot", line_color="#FF6B00", opacity=0.5,
                            annotation_text="50% line", annotation_position="top right")
        fig_route.update_layout(margin=dict(t=10, b=10), height=max(350, len(route_df) * 28))
        st.plotly_chart(fig_route, use_container_width=True)

        st.markdown("---")

        # ── Section 2: Schools that produce ────────────────────────────────────
        col_prod, col_dev = st.columns(2)

        with col_prod:
            st.subheader("🏫 Schools That Produce")
            st.caption("Programs players left. Ranked by median BPM at their next school — more resistant to one outlier season than the average.")
            origin_df = query("""
                SELECT from_school, from_tier, COUNT(*) as n,
                    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY bpm_after)::numeric,2) as median_bpm,
                    ROUND(100.0*COUNT(CASE WHEN transfer_verdict IN
                        ('High Value','Exceeded Expectations','Solid Addition') THEN 1 END)/COUNT(*),1) as hv_pct
                FROM individual_transfer_scores
                WHERE to_tier NOT IN ('sub_d1','international')
                  AND from_tier NOT IN ('sub_d1','international')
                GROUP BY from_school, from_tier HAVING COUNT(*) >= 8  -- 8 transfers minimum per school
                ORDER BY median_bpm DESC LIMIT 15
            """)
            origin_df["tier_label"] = origin_df["from_tier"].map(TIER_LABELS)
            fig_orig = px.bar(
                origin_df.sort_values("median_bpm"),
                x="median_bpm", y="from_school",
                color="hv_pct",
                color_continuous_scale=["#333333","#FF8C38","#FF6B00"],
                range_color=[40, 100],
                orientation="h",
                text="median_bpm",
                labels={"median_bpm": "Median BPM After Transfer", "from_school": "", "hv_pct": "HV%"},
            )
            fig_orig.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.3)
            fig_orig.update_traces(texttemplate="%{text:+.1f}", textposition="outside")
            fig_orig.update_layout(margin=dict(t=5, b=5), height=420, coloraxis_showscale=False)
            st.plotly_chart(fig_orig, use_container_width=True)

            worst_origin_df = query("""
                SELECT from_school, COUNT(*) as n,
                    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY bpm_after)::numeric,2) as median_bpm,
                    ROUND(100.0*COUNT(CASE WHEN transfer_verdict IN
                        ('High Value','Exceeded Expectations','Solid Addition') THEN 1 END)/COUNT(*),1) as hv_pct,
                    ROUND(100.0*COUNT(CASE WHEN transfer_verdict = 'Didn''t Fit'
                        THEN 1 END)/COUNT(*),1) as fail_pct
                FROM individual_transfer_scores
                WHERE to_tier NOT IN ('sub_d1','international')
                  AND from_tier NOT IN ('sub_d1','international')
                GROUP BY from_school HAVING COUNT(*) >= 8  -- 8 minimum so one bad season doesn't tank a school
                ORDER BY median_bpm ASC LIMIT 10
            """)
            with st.expander("Worst Starter Schools"):
                st.caption("Players left these programs and struggled at their next stop.")
                for _, r in worst_origin_df.iterrows():
                    st.markdown(
                        f"- **{r['from_school']}** — {float(r['median_bpm']):+.1f} median BPM · "
                        f"{r['hv_pct']}% Positive · {r['fail_pct']}% Below Proj. · n={int(r['n'])}"
                    )

        with col_dev:
            st.subheader("🧲 Schools That Develop")
            st.caption("Programs where transfers land and produce. Ranked by median BPM — one standout season doesn't carry the whole school.")
            dest_df = query("""
                SELECT to_school, to_tier, COUNT(*) as n,
                    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY bpm_after)::numeric,2) as median_bpm,
                    ROUND(100.0*COUNT(CASE WHEN transfer_verdict IN
                        ('High Value','Exceeded Expectations','Solid Addition') THEN 1 END)/COUNT(*),1) as hv_pct
                FROM individual_transfer_scores
                WHERE to_tier NOT IN ('sub_d1','international')
                GROUP BY to_school, to_tier HAVING COUNT(*) >= 8  -- 8 minimum per destination school
                ORDER BY median_bpm DESC LIMIT 15
            """)
            fig_dest = px.bar(
                dest_df.sort_values("median_bpm"),
                x="median_bpm", y="to_school",
                color="hv_pct",
                color_continuous_scale=["#333333","#FF8C38","#FF6B00"],
                range_color=[60, 100],
                orientation="h",
                text="median_bpm",
                labels={"median_bpm": "Median BPM After Arriving", "to_school": "", "hv_pct": "HV%"},
            )
            fig_dest.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.3)
            fig_dest.update_traces(texttemplate="%{text:+.1f}", textposition="outside")
            fig_dest.update_layout(margin=dict(t=5, b=5), height=420, coloraxis_showscale=False)
            st.plotly_chart(fig_dest, use_container_width=True)

            worst_dest_df = query("""
                SELECT to_school, COUNT(*) as n,
                    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY bpm_after)::numeric,2) as median_bpm,
                    ROUND(100.0*COUNT(CASE WHEN transfer_verdict IN
                        ('High Value','Exceeded Expectations','Solid Addition') THEN 1 END)/COUNT(*),1) as hv_pct,
                    ROUND(100.0*COUNT(CASE WHEN transfer_verdict = 'Didn''t Fit'
                        THEN 1 END)/COUNT(*),1) as fail_pct
                FROM individual_transfer_scores
                WHERE to_tier NOT IN ('sub_d1','international')
                GROUP BY to_school HAVING COUNT(*) >= 8  -- same floor as origin side
                ORDER BY median_bpm ASC LIMIT 10
            """)
            with st.expander("Worst Transfer Destinations"):
                st.caption("Transfers go here and underperform. Repeatedly.")
                for _, r in worst_dest_df.iterrows():
                    st.markdown(
                        f"- **{r['to_school']}** — {float(r['median_bpm']):+.1f} median BPM · "
                        f"{r['hv_pct']}% Positive · {r['fail_pct']}% Below Proj. · n={int(r['n'])}"
                    )

        st.markdown("---")

        # ── Section 3: Position breakdown ──────────────────────────────────────
        st.subheader("By Position")
        st.caption("Guards, forwards, and bigs don't transfer equally.")
        pos_df = query("""
            SELECT position, COUNT(*) as n,
                ROUND(AVG(bpm_after)::numeric,2) as avg_bpm,
                ROUND(100.0*COUNT(CASE WHEN transfer_verdict IN
                    ('High Value','Exceeded Expectations','Solid Addition') THEN 1 END)/COUNT(*),1) as hv_pct,
                ROUND(100.0*COUNT(CASE WHEN transfer_verdict = 'Didn''t Fit' THEN 1 END)/COUNT(*),1) as fail_pct
            FROM individual_transfer_scores
            WHERE position IN ('G','F','C')
              AND to_tier NOT IN ('sub_d1','international')
            GROUP BY position ORDER BY avg_bpm DESC
        """)
        p1, p2, p3 = st.columns(3)
        for col, (_, row) in zip([p1, p2, p3], pos_df.iterrows()):
            with col:
                icon = {"G": "🏃", "F": "💪", "C": "🏆"}.get(row["position"], "")
                st.metric(f"{icon} {row['position']} — Avg BPM", f"{row['avg_bpm']:+.2f}")
                st.caption(
                    f"{int(row['n'])} transfers · {row['hv_pct']}% Positive · "
                    f"{row['fail_pct']}% Below Proj."
                )

        st.markdown("---")

        # ── Section 4: NIL Era Effect ──────────────────────────────────────────
        st.subheader("📅 NIL Changed Everything. Did It Help?")
        st.caption(
            "NIL took effect June 2021. Portal volume tripled by 2023. "
            "Player outcomes are a different question."
        )

        nil_df = query("""
            SELECT
                CASE WHEN CAST(SPLIT_PART(season, '-', 1) AS INT) >= 2022
                     THEN 'NIL Era (2022-23 +)' ELSE 'Pre-NIL (≤ 2021-22)' END  AS era,
                COUNT(*)                                                          AS n,
                ROUND(AVG(bpm_after)::numeric, 2)                                AS avg_bpm,
                ROUND(100.0 * COUNT(CASE WHEN transfer_verdict IN
                    ('High Value','Exceeded Expectations','Solid Addition') THEN 1 END)
                    / COUNT(*), 1)                                                AS hv_pct,
                ROUND(100.0 * COUNT(CASE WHEN transfer_verdict = 'Didn''t Fit'
                    THEN 1 END) / COUNT(*), 1)                                   AS fail_pct,
                ROUND(AVG(ABS(bpm_after - bpm_before))::numeric, 2)             AS avg_swing
            FROM individual_transfer_scores
            WHERE to_tier   NOT IN ('sub_d1','international')
              AND from_tier NOT IN ('sub_d1','international')
              AND bpm_before IS NOT NULL AND bpm_after IS NOT NULL
            GROUP BY 1 ORDER BY 1
        """)

        nil_cols = st.columns(len(nil_df))
        era_colors = {"Pre-NIL (≤ 2021-22)": "#888888", "NIL Era (2022-23 +)": "#FF6B00"}
        for col, (_, row) in zip(nil_cols, nil_df.iterrows()):
            with col:
                color = era_colors.get(row["era"], "#FF6B00")
                st.markdown(
                    f"<div style='border-left:4px solid {color};padding-left:12px'>"
                    f"<b style='font-size:1.1rem'>{row['era']}</b><br>"
                    f"<span style='font-size:2rem;font-weight:700'>{row['avg_bpm']:+.2f}</span>"
                    f"<span style='color:#aaa;font-size:.85rem'> avg BPM after</span><br>"
                    f"<span style='color:{color}'>{row['hv_pct']}% High-Impact</span> · "
                    f"<span style='color:#888'>{row['fail_pct']}% Below Proj.</span><br>"
                    f"<span style='color:#aaa;font-size:.85rem'>{int(row['n']):,} transfers · "
                    f"avg swing ±{row['avg_swing']:.1f} BPM</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        # Season-by-season HV% trendline
        nil_trend = query("""
            SELECT season,
                COUNT(*) AS n,
                ROUND(100.0 * COUNT(CASE WHEN transfer_verdict IN
                    ('High Value','Exceeded Expectations','Solid Addition') THEN 1 END)
                    / COUNT(*), 1) AS hv_pct,
                ROUND(AVG(bpm_after)::numeric, 2) AS avg_bpm
            FROM individual_transfer_scores
            WHERE to_tier   NOT IN ('sub_d1','international')
              AND from_tier NOT IN ('sub_d1','international')
              AND bpm_after IS NOT NULL
            GROUP BY season HAVING COUNT(*) >= 10
            ORDER BY season
        """)
        if not nil_trend.empty:
            nil_trend["season"] = nil_trend["season"].apply(fmt_season)
            import plotly.graph_objects as go
            fig_nil = go.Figure()
            fig_nil.add_vline(x=fmt_season("2021-22"), line_dash="dot", line_color="#FF6B00", opacity=0.7)
            fig_nil.add_annotation(
                x=fmt_season("2021-22"), y=1, yref="paper",
                text="NIL begins", showarrow=False,
                xanchor="left", yanchor="top",
                font=dict(color="#FF6B00", size=11),
            )
            fig_nil.add_trace(go.Scatter(
                x=nil_trend["season"], y=nil_trend["hv_pct"],
                mode="lines+markers+text",
                name="Positive Outcome %",
                line=dict(color="#FF6B00", width=2),
                marker=dict(size=8),
                text=nil_trend["hv_pct"].apply(lambda v: f"{v:.0f}%"),
                textposition="top center",
            ))
            fig_nil.add_trace(go.Bar(
                x=nil_trend["season"], y=nil_trend["n"],
                name="# Transfers",
                yaxis="y2",
                opacity=0.2,
                marker_color="#FF8C38",
            ))
            fig_nil.update_layout(
                yaxis=dict(title="Positive Outcome %", range=[0, 100]),
                yaxis2=dict(title="# Transfers", overlaying="y", side="right"),
                legend=dict(orientation="h", y=1.1),
                margin=dict(t=20, b=10),
                height=300,
            )
            st.plotly_chart(fig_nil, use_container_width=True)

        st.markdown("---")

        # ── Section 5: Greatest Transfers Ever ────────────────────────────────
        st.subheader("🐐 Best Individual Transfers")
        st.caption("Sorted by BPM at their new school. Top 5 listed, full top-20 in the table below.")

        goat_df = query("""
            SELECT its.full_name, its.from_school, its.to_school,
                   its.from_tier, its.to_tier, its.season,
                   its.position, its.bpm_before, its.bpm_after,
                   its.transfer_premium, its.transfer_verdict
            FROM individual_transfer_scores its
            WHERE its.to_tier   NOT IN ('sub_d1','international')
              AND its.from_tier NOT IN ('sub_d1','international')
              AND its.bpm_after IS NOT NULL
            ORDER BY its.bpm_after DESC NULLS LAST
            LIMIT 20
        """)

        if not goat_df.empty:
            goat_df["rank"]    = range(1, len(goat_df) + 1)
            goat_df["move"]    = goat_df.apply(
                lambda r: "⬆️ Up" if TIER_RANK.get(r["to_tier"], 4) < TIER_RANK.get(r["from_tier"], 4)
                else ("➡️ Lateral" if r["from_tier"] == r["to_tier"] else "⬇️ Down"), axis=1,
            )
            goat_df["route"] = goat_df.apply(
                lambda r: f"{TIER_LABELS.get(r['from_tier'], r['from_tier'])} to {TIER_LABELS.get(r['to_tier'], r['to_tier'])}",
                axis=1,
            )

            for _, row in goat_df.head(5).iterrows():
                bpm_delta = row["bpm_after"] - row["bpm_before"] if row["bpm_before"] else None
                delta_str = f", up {bpm_delta:+.1f} from before" if bpm_delta and bpm_delta > 0 else ""
                st.markdown(
                    f"**#{int(row['rank'])}. {row['full_name']}** "
                    f"({row['from_school']} to {row['to_school']}, {row['season']}) "
                    f"**+{row['bpm_after']:.1f} BPM**{delta_str} · "
                    f"{row['route']} · {row['move']}"
                )

            with st.expander("Show full top-20 table"):
                goat_df["season"] = goat_df["season"].apply(fmt_season)
                if "transfer_verdict" in goat_df.columns:
                    goat_df["transfer_verdict"] = goat_df["transfer_verdict"].apply(fmt_verdict)
                st.dataframe(
                    goat_df[[
                        "rank","full_name","position","from_school","to_school",
                        "season","bpm_before","bpm_after","transfer_premium",
                        "transfer_verdict","move",
                    ]].rename(columns={
                        "rank":"#","full_name":"Player","position":"Pos",
                        "from_school":"From","to_school":"To","season":"Season",
                        "bpm_before":"BPM Before","bpm_after":"BPM After",
                        "transfer_premium":"Premium","transfer_verdict":"Verdict",
                        "move":"Move",
                    }),
                    use_container_width=True, hide_index=True,
                )

    except Exception as e:
        st.error(f"Takeaways error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB L — Scope & Limits
# ═══════════════════════════════════════════════════════════════════════════════
with tabL:
    st.html("""
    <div style="max-width:860px; margin:0 auto; padding:8px 0 40px 0;">

      <div style="margin-bottom:28px;">
        <div style="color:#FF6B00; font-weight:700; font-size:0.9rem; letter-spacing:0.08em;
                    text-transform:uppercase; margin-bottom:8px;">What This Is</div>
        <p style="color:#CCCCCC; font-size:1.0rem; line-height:1.7; max-width:720px; margin:0;">
          PivotHoops scores every transfer with <b>skill index</b> — a blend of Box Plus/Minus (50%),
          usage rate (25%), and true-shooting% (25%) from College Basketball Reference. BPM alone is
          the best publicly available single-number impact metric, but using it by itself rewards
          high-usage volume scorers over efficient role players — skill index corrects for that.
          It still inherits BPM's real limits. Read this before drawing hard conclusions from any
          number on this dashboard.
        </p>
      </div>

      <div style="background:#1A1A1A; border-left:3px solid #FF8C38; border-radius:4px;
                  padding:18px 24px; margin-bottom:28px;">
        <div style="color:#FFFFFF; font-weight:700; font-size:0.95rem; margin-bottom:8px;">
          High BPM at a new school does not mean the transfer was the right call
        </div>
        <p style="color:#CCCCCC; font-size:0.88rem; line-height:1.6; margin:0;">
          This dashboard measures how well a player performed at their new school — not whether
          the move was the best decision available to them. A player who posted +4 BPM at a mid-major
          might have posted +6 if they stayed put or chose a different program. "Transfer worked"
          here means they contributed; it says nothing about opportunity cost.
          <br><br>
          To get closer to that question, look at <b>BPM change</b> (how much they improved or declined
          vs. their previous season) and <b>transfer premium</b> (BPM after vs. what similar players
          typically produce on that route). Those metrics measure relative improvement — not just output.
        </p>
      </div>

      <div style="color:#FF6B00; font-weight:700; font-size:0.9rem; letter-spacing:0.08em;
                  text-transform:uppercase; margin-bottom:12px;">Known Limitations</div>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:28px;">

        <div style="background:#1A1A1A; border-left:3px solid #FF6B00; border-radius:4px; padding:18px 20px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.95rem; margin-bottom:8px;">
            BPM Skews High Major
          </div>
          <p style="color:#CCCCCC; font-size:0.88rem; line-height:1.6; margin:0;">
            High major teams play stronger schedules, which strength-of-schedule adjustments help but don't fully fix.
            Non-conference blowouts against weak opponents inflate individual BPM.
            A Big 12 player at +4.0 and a Sun Belt player at +4.0 are not the same.
            Cross-tier BPM comparisons should be treated as directional, not precise.
          </p>
        </div>

        <div style="background:#1A1A1A; border-left:3px solid #FF6B00; border-radius:4px; padding:18px 20px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.95rem; margin-bottom:8px;">
            Only 37% of Transfers Are Scored
          </div>
          <p style="color:#CCCCCC; font-size:0.88rem; line-height:1.6; margin:0;">
            3,025 of 8,208 portal entries have a score. To be scored, a player needs BPM data
            at both their origin school and their destination — meaning meaningful minutes at both stops.
            Players who transferred mid-development, went pro, or sat out a year are invisible here.
          </p>
        </div>

        <div style="background:#1A1A1A; border-left:3px solid #FF8C38; border-radius:4px; padding:18px 20px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.95rem; margin-bottom:8px;">
            High Major Data Is More Complete
          </div>
          <p style="color:#CCCCCC; font-size:0.88rem; line-height:1.6; margin:0;">
            CBB Reference play-by-play coverage is strongest for Power 5 programs.
            Low major and mid-major box scores are spottier — missing games affect BPM accuracy.
            High major verdicts are the most reliable. Low major verdicts should be read with more skepticism.
          </p>
        </div>

        <div style="background:#1A1A1A; border-left:3px solid #FF8C38; border-radius:4px; padding:18px 20px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.95rem; margin-bottom:8px;">
            Small Sample Floors
          </div>
          <p style="color:#CCCCCC; font-size:0.88rem; line-height:1.6; margin:0;">
            Players with fewer than 12 games get no BPM score.
            Transfer premium peer groups require at least 5 comparable players —
            thin conferences can produce noisy baselines.
            Single-season results in small samples are directional signals, not definitive grades.
          </p>
        </div>

        <div style="background:#1A1A1A; border-left:3px solid #555555; border-radius:4px; padding:18px 20px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.95rem; margin-bottom:8px;">
            International Players Are Mostly Unscored
          </div>
          <p style="color:#CCCCCC; font-size:0.88rem; line-height:1.6; margin:0;">
            Pre-D1 stats for international players are unavailable — the primary overseas data source
            blocked automated scraping. International transfers can only be scored after they log
            D1 minutes. Their pre-college performance is not reflected anywhere in this model.
          </p>
        </div>

        <div style="background:#1A1A1A; border-left:3px solid #555555; border-radius:4px; padding:18px 20px;">
          <div style="color:#FFFFFF; font-weight:700; font-size:0.95rem; margin-bottom:8px;">
            Verdict Thresholds Are Flat
          </div>
          <p style="color:#CCCCCC; font-size:0.88rem; line-height:1.6; margin:0;">
            High Value requires a skill index more than 1 standard deviation above the population
            average, regardless of conference. The bar recalculates automatically against the full
            dataset as more transfers get scored — but it's still one universal bar, not adjusted
            per tier, so it slightly undervalues success in lower-resource environments where that
            bar is genuinely harder to clear. Use verdicts as a starting point, not a final answer.
          </p>
        </div>

      </div>

      <div style="color:#FF6B00; font-weight:700; font-size:0.9rem; letter-spacing:0.08em;
                  text-transform:uppercase; margin-bottom:12px;">What the Data Does Well</div>

      <div style="background:#1A1A1A; border-left:3px solid #FF6B00; border-radius:4px;
                  padding:20px 24px; display:grid; grid-template-columns:1fr 1fr; gap:12px 32px;">
        <div style="color:#CCCCCC; font-size:0.88rem; line-height:1.6;">
          ✅ &nbsp;8,208 transfers tracked across 350 of 364 D1 schools
        </div>
        <div style="color:#CCCCCC; font-size:0.88rem; line-height:1.6;">
          ✅ &nbsp;2020-21 through 2025-26 — five full portal-era seasons
        </div>
        <div style="color:#CCCCCC; font-size:0.88rem; line-height:1.6;">
          ✅ &nbsp;Deduplicated with a database-level UNIQUE constraint
        </div>
        <div style="color:#CCCCCC; font-size:0.88rem; line-height:1.6;">
          ✅ &nbsp;BPM capped at ±15 to limit outlier contamination
        </div>
        <div style="color:#CCCCCC; font-size:0.88rem; line-height:1.6;">
          ✅ &nbsp;Peer baselines account for tier and recruiting composite
        </div>
        <div style="color:#CCCCCC; font-size:0.88rem; line-height:1.6;">
          ✅ &nbsp;Context score applies tier weights so high major production ranks higher
        </div>
      </div>

    </div>
    """)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Transfer Overview
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.title("📊 Transfer Overview")
    st.caption("Men's basketball · 2021–2026 · Post-NIL era")

    sf, sp = season_filter()
    where, params = build_where((sf, sp))

    # KPI row
    kpi_sql = f"""
        SELECT
            COUNT(*)                                          AS total_transfers,
            COUNT(DISTINCT t.player_id)                      AS unique_players,
            COUNT(DISTINCT t.to_team_id)                     AS destination_teams,
            ROUND(AVG(ps.usg_pct)::numeric, 1)               AS avg_usg,
            ROUND(AVG(ps.bpm)::numeric, 2)                   AS avg_bpm
        FROM transfers t
        JOIN players p       ON t.player_id = p.player_id
        JOIN teams t_to      ON t.to_team_id = t_to.team_id
        JOIN conferences c_to ON t_to.conference_id = c_to.conference_id
        LEFT JOIN player_seasons ps ON t.player_id = ps.player_id
            AND ps.season = t.season AND ps.team_id = t.to_team_id
        {where.replace("tr.season", "t.season").replace("c_to.tier", "c_to.tier")}
    """
    try:
        kpi = query(kpi_sql, params)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total Transfers", f"{int(kpi['total_transfers'][0]):,}")
        c2.metric("Unique Players", f"{int(kpi['unique_players'][0]):,}")
        c3.metric("Destination Schools", f"{int(kpi['destination_teams'][0]):,}")
        c4.metric("Avg USG% (post-transfer)", f"{kpi['avg_usg'][0] or '—'}%")
        c5.metric("Avg BPM (post-transfer)", kpi['avg_bpm'][0] or "—")
    except Exception:
        st.info("Load data to see KPIs.")

    st.markdown("---")

    col_left, col_right = st.columns(2)

    # Transfer flow heatmap
    with col_left:
        st.subheader("Transfer Flow by Tier")
        flow_sql = f"""
            SELECT
                c_from.tier::text AS from_tier,
                c_to.tier::text   AS to_tier,
                COUNT(*)                                    AS transfers
            FROM transfers tr
            JOIN players p       ON tr.player_id    = p.player_id
            JOIN teams t_from    ON tr.from_team_id = t_from.team_id
            JOIN teams t_to      ON tr.to_team_id   = t_to.team_id
            JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
            JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
            {build_where((season_filter("tr.season")[0], season_filter("tr.season")[1]),
                         (pos_filter()[0], pos_filter()[1]))[0]}
              AND c_to.tier NOT IN ('sub_d1', 'international')
            GROUP BY 1, 2
        """
        try:
            flow_params = season_filter("tr.season")[1] + pos_filter()[1]
            flow = query(flow_sql, flow_params)
            flow["from_label"] = flow["from_tier"].map(TIER_LABELS).fillna(flow["from_tier"])
            flow["to_label"]   = flow["to_tier"].map(TIER_LABELS).fillna(flow["to_tier"])
            pivot = flow.pivot(index="from_label", columns="to_label", values="transfers").fillna(0)
            fig = px.imshow(
                pivot,
                text_auto=True,
                color_continuous_scale="Blues",
                labels={"x": "Destination Tier", "y": "Origin Tier", "color": "Transfers"},
            )
            fig.update_layout(margin=dict(t=20, b=20))
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.info(f"No data yet. ({e})")

    # Transfers per season bar
    with col_right:
        st.subheader("Transfer Volume by Season")
        vol_sql = f"""
            SELECT tr.season, COUNT(*) AS transfers
            FROM transfers tr
            JOIN players p ON tr.player_id = p.player_id
            JOIN teams t_to ON tr.to_team_id = t_to.team_id
            JOIN conferences c_to ON t_to.conference_id = c_to.conference_id
            {build_where((pos_filter()[0], pos_filter()[1]),
                         (dest_filter()[0], dest_filter()[1]))[0]}
            GROUP BY 1 ORDER BY 1
        """
        try:
            vol_params = pos_filter()[1] + dest_filter()[1]
            vol = query(vol_sql, vol_params)
            vol["season"] = vol["season"].apply(fmt_season)
            fig = px.bar(vol, x="season", y="transfers", color_discrete_sequence=["#2e75b6"])
            fig.update_layout(margin=dict(t=20, b=20), xaxis_title="", yaxis_title="# Transfers")
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.info(f"No data yet. ({e})")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Individual Transfer Scores
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.title("📊 Player Scores & Verdicts")

    # ── Search row ────────────────────────────────────────────────────────────
    search_col1, search_col2, search_col3, search_col4 = st.columns([2, 1, 1, 1])
    with search_col1:
        tab2_search = st.text_input(
            "Search player or school", placeholder="e.g. Taurus Samuels, Kentucky, Dartmouth …",
            key="tab2_search", label_visibility="collapsed"
        )
    with search_col2:
        tab2_verdict = st.multiselect(
            "Evaluation", VERDICT_ORDER, default=[], key="tab2_verdict",
            format_func=fmt_verdict,
            placeholder="All verdicts",
        )
    with search_col3:
        tab2_role = st.selectbox(
            "Role", ["Any Role", "🏆 Star", "🌟 Starter", "💪 Key Guy", "✅ Solid Backup", "📋 Project"],
            key="tab2_role", label_visibility="collapsed",
        )
    with search_col4:
        tab2_origin = st.selectbox(
            "Origin Tier", ["Any"] + ALL_TIERS,
            format_func=lambda x: TIER_LABELS.get(x, x), key="tab2_origin",
            label_visibility="collapsed",
        )

    its_sql = """
        SELECT
            full_name, position, from_school, from_tier,
            to_school, to_tier, season,
            bpm_before, bpm_after, bpm_change,
            usage_before, usage_after, usg_change,
            projected_bpm, transfer_premium,
            context_score, efficiency_after,
            mpg_after, ppg_after, transfer_verdict, peer_group_size
        FROM individual_transfer_scores
        WHERE to_tier NOT IN ('sub_d1', 'international')
    """
    params_its = []

    if selected_seasons:
        its_sql += f" AND season IN ({','.join(['%s']*len(selected_seasons))})"
        params_its.extend(selected_seasons)
    if selected_pos != "All":
        its_sql += " AND position = %s"
        params_its.append(selected_pos)
    if selected_dest != "All":
        its_sql += " AND to_tier = %s"
        params_its.append(selected_dest)
    if tab2_verdict:
        its_sql += f" AND transfer_verdict IN ({','.join(['%s']*len(tab2_verdict))})"
        params_its.extend(tab2_verdict)
    if tab2_origin != "Any":
        its_sql += " AND from_tier = %s"
        params_its.append(tab2_origin)

    its_sql += " ORDER BY transfer_premium DESC NULLS LAST"

    try:
        its = query(its_sql, params_its or None)
        its["from_tier_label"] = its["from_tier"].map(TIER_LABELS)
        its["to_tier_label"]   = its["to_tier"].map(TIER_LABELS)
        its["role"] = its["bpm_after"].apply(
            lambda b: ("🏆 Star" if b >= 6.0 else "🌟 Starter" if b >= 3.0 else "💪 Key Guy" if b >= 1.0
                       else "✅ Solid Backup" if b >= -0.5 else "📋 Project")
            if pd.notna(b) else "—"
        )

        # Apply in-memory filters (search text + role)
        if tab2_search:
            q = tab2_search.strip().lower()
            its = its[
                its["full_name"].str.lower().str.contains(q, na=False)
                | its["from_school"].str.lower().str.contains(q, na=False)
                | its["to_school"].str.lower().str.contains(q, na=False)
            ]
        if tab2_role != "Any Role":
            its = its[its["role"] == tab2_role]

        # Translate to display labels now, before anything reads transfer_verdict —
        # summary metrics and both charts below key off the display labels
        # (VERDICT_COLORS / VERDICT_DISPLAY_ORDER), not the raw DB values.
        its["transfer_verdict"] = its["transfer_verdict"].apply(fmt_verdict)

        # Summary strip
        c1, c2, c3, c4, c5 = st.columns(5)
        vc = its["transfer_verdict"].value_counts()
        c1.metric("High-Impact",        vc.get("High-Impact Acquisition", 0) + vc.get("Above Projection", 0))
        c2.metric("Positive Acq.",      vc.get("Positive Acquisition", 0))
        c3.metric("Lateral Move",       vc.get("Lateral Move", 0))
        c4.metric("Below Projection",   vc.get("Below Projection", 0))
        scored = its["transfer_premium"].notna().sum()
        c5.metric("With Peer Baseline", f"{scored:,}")

        st.markdown("---")
        col_l, col_r = st.columns([3, 2])

        with col_l:
            st.subheader("Transfer Premium — Top & Bottom 15")
            st.caption(
                "**Transfer Premium** = player BPM minus the avg BPM of peers who made "
                "the same tier jump with a similar recruiting composite. Positive = beat projection."
            )
            prem_df = its[its["transfer_premium"].notna()].copy()
            if len(prem_df) >= 2:
                top_n    = min(15, len(prem_df) // 2)
                display  = pd.concat([prem_df.head(top_n), prem_df.tail(top_n)]).drop_duplicates()
                display  = display.sort_values("transfer_premium")
                fig = px.bar(
                    display,
                    x="transfer_premium",
                    y="full_name",
                    color="transfer_verdict",
                    color_discrete_map=VERDICT_COLORS,
                    orientation="h",
                    hover_data=["from_school", "to_school", "season", "bpm_before", "bpm_after"],
                    labels={"transfer_premium": "Transfer Premium (BPM vs Peer Avg)", "full_name": ""},
                )
                fig.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.5)
                fig.update_layout(margin=dict(t=10, b=10), legend_title="Verdict",
                                  height=max(400, len(display) * 22))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Not enough players with peer baselines to display. Run ETL first.")

        with col_r:
            st.subheader("Verdict Breakdown by Destination Tier")
            tier_order_labels = [TIER_LABELS[t] for t in TIER_ORDER]
            tier_verdict = (
                its.groupby(["to_tier_label", "transfer_verdict"])
                .size().reset_index(name="count")
            )
            tier_verdict["to_tier_label"] = pd.Categorical(
                tier_verdict["to_tier_label"], categories=tier_order_labels, ordered=True
            )
            fig2 = px.bar(
                tier_verdict.sort_values("to_tier_label"),
                x="to_tier_label",
                y="count",
                color="transfer_verdict",
                color_discrete_map=VERDICT_COLORS,
                category_orders={"transfer_verdict": VERDICT_DISPLAY_ORDER},
                barmode="stack",
                labels={"to_tier_label": "Destination Tier", "count": "Players",
                        "transfer_verdict": "Verdict"},
            )
            fig2.update_layout(margin=dict(t=10, b=10), legend_title="")
            st.plotly_chart(fig2, use_container_width=True)

        st.markdown("---")
        st.subheader("Full Transfer Table")

        with st.expander("What do these columns mean?", expanded=False):
            st.markdown("""
| Column | Plain English |
|--------|---------------|
| **BPM Before** | How much above/below average this player was at their *old* school. What a scout would see in the portal. |
| **BPM After** | What they actually delivered after transferring. The result. |
| **BPM Δ** | How much they improved or declined. |
| **Context Score** | BPM × Tier Difficulty × Role Difficulty. Rewards impact in harder environments. Our internal rating. |
| **Premium** | How much they beat the expected BPM for their type of transfer. Positive = exceeded expectations. |
| **Verdict** | 🌟 Above Projection · ✅ High-Impact · 👍 Positive Acq. · ➖ Lateral Move · ❌ Below Projection |
            """)

        its["season"] = its["season"].apply(fmt_season)
        show_cols = ["role", "full_name", "position", "season", "from_school", "from_tier_label",
                     "to_school", "to_tier_label", "bpm_before", "bpm_after",
                     "context_score", "transfer_premium", "transfer_verdict"]
        st.dataframe(
            its[show_cols].rename(columns={
                "role":             "D1 Role",
                "full_name":        "Player",
                "position":         "Pos",
                "season":           "Season",
                "from_school":      "From",
                "from_tier_label":  "From Tier",
                "to_school":        "To",
                "to_tier_label":    "To Tier",
                "bpm_before":       "BPM Before",
                "bpm_after":        "BPM After",
                "context_score":    "Context Score",
                "transfer_premium": "Premium",
                "transfer_verdict": "Verdict",
            }),
            use_container_width=True,
            hide_index=True,
        )
    except Exception as e:
        st.info(f"No data yet — run ETL scripts first. ({e})")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — League Trends
# ═══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.title("📈 League Transfer Trends")
    st.markdown("How transfers perform at each tier level — success rates, context scores, OBPM vs DBPM, and size patterns.")

    try:
        ltt_sql = "SELECT * FROM league_transfer_trends"
        ltt_params = []
        if selected_seasons:
            ltt_sql += f" WHERE season IN ({','.join(['%s']*len(selected_seasons))})"
            ltt_params.extend(selected_seasons)
        ltt = query(ltt_sql, ltt_params or None)
        ltt["dest_label"]   = ltt["dest_tier"].map(TIER_LABELS)
        ltt["origin_label"] = ltt["origin_tier"].map(TIER_LABELS)

        # ── Section 1: Success rate heatmap by tier pair ───────────────────────
        st.subheader("Transfer Success Rate by Tier Move")
        st.caption("% of transfers rated High Value or Solid Addition")
        dest_labels   = [TIER_LABELS[t] for t in TIER_ORDER]
        origin_labels = [TIER_LABELS[t] for t in ALL_TIERS]
        ltt_dest = ltt[ltt["dest_label"].isin(dest_labels)].copy()
        heat = (
            ltt_dest.groupby(["origin_label", "dest_label"])
            .agg(total=("transfer_count","sum"), hv=("high_value","sum"), sa=("solid_addition","sum"))
            .reset_index()
        )
        heat["success_pct"] = ((heat["hv"] + heat["sa"]) / heat["total"] * 100).round(1)
        pivot_heat = heat.pivot(index="origin_label", columns="dest_label", values="success_pct").fillna(0)
        _excluded_origin_labels = {TIER_LABELS["sub_d1"], TIER_LABELS["international"]}
        row_order = [TIER_LABELS[t] for t in TIER_ORDER if TIER_LABELS[t] in pivot_heat.index]
        col_order = [TIER_LABELS[t] for t in TIER_ORDER if TIER_LABELS[t] in pivot_heat.columns]
        pivot_heat = pivot_heat.reindex(index=row_order, columns=col_order, fill_value=0)
        fig = px.imshow(
            pivot_heat, text_auto=".1f",
            color_continuous_scale="RdYlGn", range_color=[20, 70],
            labels={"x": "Destination Tier", "y": "Origin Tier", "color": "Success %"},
        )
        fig.update_layout(margin=dict(t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")

        # ── Section 2: Avg context score by position and destination ──────────
        col_l, col_r = st.columns(2)
        with col_l:
            st.subheader("Avg Context Score by Position & Destination")
            pos_ctx = (
                ltt_dest.groupby(["dest_label", "position"])
                .agg(avg_ctx=("avg_context_score","mean"), n=("transfer_count","sum"))
                .reset_index()
            )
            fig2 = px.bar(
                pos_ctx[pos_ctx["n"] >= 5],
                x="dest_label", y="avg_ctx", color="position",
                barmode="group",
                labels={"dest_label": "Destination Tier", "avg_ctx": "Avg Context Score", "position": "Position"},
            )
            fig2.update_layout(margin=dict(t=20, b=20))
            st.plotly_chart(fig2, use_container_width=True)

        with col_r:
            st.subheader("Expected Context Score by Tier Move")
            move_ctx = (
                ltt_dest.groupby(["origin_label", "dest_label"])
                .agg(avg_ctx=("avg_context_score","mean"), n=("transfer_count","sum"))
                .reset_index()
            )
            move_ctx = move_ctx[~move_ctx["origin_label"].isin(_excluded_origin_labels)]
            move_ctx["move"] = move_ctx["origin_label"] + " → " + move_ctx["dest_label"]
            fig3 = px.bar(
                move_ctx[move_ctx["n"] >= 5].sort_values("avg_ctx", ascending=False).head(12),
                x="avg_ctx", y="move", orientation="h",
                color="avg_ctx", color_continuous_scale="Blues",
                labels={"avg_ctx": "Avg Context Score", "move": ""},
            )
            fig3.update_layout(margin=dict(t=20, b=20), coloraxis_showscale=False)
            st.plotly_chart(fig3, use_container_width=True)

        st.markdown("---")

        # ── Section 3: OBPM vs DBPM split ─────────────────────────────────────
        st.subheader("Offensive vs Defensive Contribution After Transfer")
        st.caption("BPM = OBPM + DBPM. Shows whether transfers contribute on offense, defense, or both.")
        obpm_sql = """
            SELECT
                c_to.tier AS dest_tier,
                its.position,
                ROUND(AVG(its.obpm_after)::NUMERIC, 2) AS avg_obpm,
                ROUND(AVG(its.dbpm_after)::NUMERIC, 2) AS avg_dbpm,
                ROUND(AVG(its.bpm_after)::NUMERIC,  2) AS avg_bpm,
                COUNT(*) AS n
            FROM individual_transfer_scores its
            JOIN transfers tr ON its.transfer_id = tr.transfer_id
            JOIN teams t_to   ON tr.to_team_id   = t_to.team_id
            JOIN conferences c_to ON t_to.conference_id = c_to.conference_id
            WHERE its.obpm_after IS NOT NULL
              AND c_to.tier NOT IN ('sub_d1', 'international')
            GROUP BY 1, 2
            HAVING COUNT(*) >= 5
            ORDER BY 1, 2
        """
        obpm_df = query(obpm_sql)
        if not obpm_df.empty:
            obpm_df["dest_label"] = obpm_df["dest_tier"].map(TIER_LABELS)
            obpm_melt = obpm_df.melt(
                id_vars=["dest_label", "position"],
                value_vars=["avg_obpm", "avg_dbpm"],
                var_name="component", value_name="value"
            )
            obpm_melt["component"] = obpm_melt["component"].map({"avg_obpm": "OBPM (Offense)", "avg_dbpm": "DBPM (Defense)"})
            fig4 = px.bar(
                obpm_melt,
                x="dest_label", y="value", color="component",
                facet_col="position", barmode="group",
                color_discrete_map={"OBPM (Offense)": "#2e75b6", "DBPM (Defense)": "#c00000"},
                labels={"dest_label": "Destination Tier", "value": "Avg BPM Component", "component": ""},
            )
            fig4.update_layout(margin=dict(t=40, b=20))
            st.plotly_chart(fig4, use_container_width=True)
        else:
            st.info("OBPM/DBPM data loads after re-running ETL with CBB Reference stats.")

        st.markdown("---")

        # ── Section 4: Height/weight correlation ──────────────────────────────
        st.subheader("Size & Transfer Success")
        st.caption("Average height (inches) and weight (lbs) of transfers by verdict and position.")
        size_sql = """
            SELECT
                its.position,
                its.transfer_verdict,
                ROUND(AVG(its.height_in)::NUMERIC,  1) AS avg_height,
                ROUND(AVG(its.weight_lbs)::NUMERIC, 0) AS avg_weight,
                COUNT(*) AS n
            FROM individual_transfer_scores its
            WHERE its.height_in IS NOT NULL
            GROUP BY 1, 2
            HAVING COUNT(*) >= 5
        """
        size_df = query(size_sql)
        if not size_df.empty:
            col_s1, col_s2 = st.columns(2)
            with col_s1:
                fig5 = px.bar(
                    size_df, x="position", y="avg_height", color="transfer_verdict",
                    barmode="group", color_discrete_map=VERDICT_COLORS,
                    labels={"avg_height": "Avg Height (in)", "position": "Position", "transfer_verdict": "Verdict"},
                    title="Avg Height by Position & Verdict",
                )
                fig5.update_layout(margin=dict(t=40, b=20))
                st.plotly_chart(fig5, use_container_width=True)
            with col_s2:
                fig6 = px.bar(
                    size_df, x="position", y="avg_weight", color="transfer_verdict",
                    barmode="group", color_discrete_map=VERDICT_COLORS,
                    labels={"avg_weight": "Avg Weight (lbs)", "position": "Position", "transfer_verdict": "Verdict"},
                    title="Avg Weight by Position & Verdict",
                )
                fig6.update_layout(margin=dict(t=40, b=20))
                st.plotly_chart(fig6, use_container_width=True)

            # Scatter: height vs context score
            scatter_sql = """
                SELECT height_in, weight_lbs, context_score, position, transfer_verdict,
                       full_name, from_school, to_school
                FROM individual_transfer_scores
                WHERE height_in IS NOT NULL AND context_score IS NOT NULL
            """
            sc_df = query(scatter_sql)
            fig7 = px.scatter(
                sc_df, x="height_in", y="context_score",
                color="transfer_verdict", color_discrete_map=VERDICT_COLORS,
                facet_col="position",
                hover_data=["full_name", "from_school", "to_school", "weight_lbs"],
                labels={"height_in": "Height (inches)", "context_score": "Context Score", "transfer_verdict": "Verdict"},
                trendline="ols",
                title="Height vs Context Score by Position",
            )
            fig7.update_layout(margin=dict(t=40, b=20))
            st.plotly_chart(fig7, use_container_width=True)
        else:
            st.info("Height/weight data populates after re-running scrape_on3.py and load_real_data.py.")

    except Exception as e:
        st.info(f"No data yet — run ETL scripts first. ({e})")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Recruit Profiles
# ═══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.title("🎯 Recruit Profile Intel")
    st.markdown(
        "Two-part view: **who these players are** (raw pre-transfer profile, no weighting) "
        "and **what to expect at the new level** (post-transfer production + contextual value)."
    )

    try:
        rec_sql = "SELECT * FROM recruitment_profiles ORDER BY dest_tier, origin_tier, position"
        rec = query(rec_sql)
        rec["dest_label"]   = rec["dest_tier"].map(TIER_LABELS)
        rec["origin_label"] = rec["origin_tier"].map(TIER_LABELS)

        dest_options = [TIER_LABELS[t] for t in TIER_ORDER if TIER_LABELS[t] in rec["dest_label"].values]
        selected_rec_dest = st.selectbox("Recruiting FOR this tier:", dest_options, key="rec_dest")
        filtered = rec[rec["dest_label"] == selected_rec_dest].copy()

        # ── Section 1: Player Profile (raw, no weighting) ──────────────────────
        st.markdown("---")
        st.subheader("Player Profile — Who They Are Coming In")
        st.caption("Raw pre-transfer stats of players who succeeded on this route (no tier weighting applied)")

        col_l, col_r = st.columns([2, 3])
        with col_l:
            profile_cols = {
                "origin_label":             "Recruit From",
                "position":                 "Pos",
                "sample_size":              "n",
                "success_rate_pct":         "Success %",
                "success_avg_bpm_before":   "Avg BPM (before)",
                "success_avg_usage_before": "Avg USG% (before)",
                "fail_avg_bpm_before":      "Fail BPM (before)",
                "avg_height_in":            "Avg Height (in)",
                "avg_weight_lbs":           "Avg Weight (lbs)",
            }
            st.dataframe(
                filtered[[c for c in profile_cols if c in filtered.columns]]
                .rename(columns=profile_cols),
                use_container_width=True, hide_index=True,
            )

        with col_r:
            # Side-by-side: success vs fail pre-BPM per origin
            fig_profile = px.bar(
                filtered.melt(
                    id_vars=["origin_label", "position"],
                    value_vars=["success_avg_bpm_before", "fail_avg_bpm_before"],
                    var_name="group", value_name="bpm_before"
                ).assign(group=lambda d: d["group"].map({
                    "success_avg_bpm_before": "Succeeded",
                    "fail_avg_bpm_before":    "Didn't Succeed",
                })),
                x="origin_label", y="bpm_before", color="group",
                facet_col="position", barmode="group",
                color_discrete_map={"Succeeded": "#2ecc71", "Didn't Succeed": "#e74c3c"},
                labels={"origin_label": "Recruit From", "bpm_before": "Avg BPM Before Transfer", "group": ""},
                title="Pre-Transfer BPM: Successful vs Unsuccessful Transfers",
            )
            fig_profile.update_layout(margin=dict(t=40, b=20))
            st.plotly_chart(fig_profile, use_container_width=True)

        # USG% profile
        col_usg_l, col_usg_r = st.columns(2)
        with col_usg_l:
            fig_usg = px.bar(
                filtered.melt(
                    id_vars=["origin_label", "position"],
                    value_vars=["success_avg_usage_before", "fail_avg_usage_before"],
                    var_name="group", value_name="usg"
                ).assign(group=lambda d: d["group"].map({
                    "success_avg_usage_before": "Succeeded",
                    "fail_avg_usage_before":    "Didn't Succeed",
                })),
                x="origin_label", y="usg", color="group", barmode="group",
                color_discrete_map={"Succeeded": "#2ecc71", "Didn't Succeed": "#e74c3c"},
                labels={"origin_label": "Recruit From", "usg": "Avg USG% Before Transfer", "group": ""},
                title="Pre-Transfer Usage Rate",
            )
            fig_usg.update_layout(margin=dict(t=40, b=20))
            st.plotly_chart(fig_usg, use_container_width=True)

        with col_usg_r:
            if filtered["avg_height_in"].notna().any():
                fig_size = px.scatter(
                    filtered[filtered["avg_height_in"].notna()],
                    x="avg_height_in", y="avg_weight_lbs",
                    color="position", size="sample_size", text="origin_label",
                    labels={"avg_height_in": "Avg Height (in)", "avg_weight_lbs": "Avg Weight (lbs)"},
                    title="Physical Profile of Players on This Route",
                )
                fig_size.update_traces(textposition="top center")
                fig_size.update_layout(margin=dict(t=40, b=20))
                st.plotly_chart(fig_size, use_container_width=True)

        # ── Section 2: What to Expect at the New Level ──────────────────────────
        st.markdown("---")
        st.subheader("What to Expect at the New Level")
        st.caption(
            "Post-transfer production and success rate. "
            "Context Score applies tier weight here — same raw BPM is worth more at a harder school."
        )

        col_e_l, col_e_r = st.columns([2, 3])
        with col_e_l:
            expect_cols = {
                "origin_label":         "Recruit From",
                "position":             "Pos",
                "success_rate_pct":     "Success %",
                "success_avg_ppg_after":"Avg PPG (after)",
                "success_avg_ts":       "Avg TS% (after)",
                "avg_context_score":    "Avg Context Score",
                "avg_transfer_premium": "Avg Transfer Premium",
                "high_value_count":     "High-Impact Acq.",
                "solid_addition_count": "Solid Addition",
                "neutral_count":        "Neutral",
                "didnt_fit_count":      "Below Projection",
            }
            st.dataframe(
                filtered[[c for c in expect_cols if c in filtered.columns]]
                .rename(columns=expect_cols),
                use_container_width=True, hide_index=True,
            )

        with col_e_r:
            fig_ctx = px.bar(
                filtered, x="origin_label", y="avg_context_score",
                color="position", barmode="group",
                labels={"origin_label": "Recruit From", "avg_context_score": "Avg Context Score", "position": "Pos"},
                title="Expected Context Score at Destination (tier-weighted value)",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig_ctx.add_hline(y=2.0, line_dash="dot", line_color="#2ecc71",
                              annotation_text="High Value threshold", annotation_position="right")
            fig_ctx.add_hline(y=0.5, line_dash="dot", line_color="#f39c12",
                              annotation_text="Solid Addition threshold", annotation_position="right")
            fig_ctx.update_layout(margin=dict(t=40, b=20))
            st.plotly_chart(fig_ctx, use_container_width=True)

        # Success rate heatmap by route
        st.markdown("---")
        st.subheader("Success Rate by Origin Tier & Position")
        pivot = filtered.pivot_table(
            index="origin_label", columns="position", values="success_rate_pct", aggfunc="first"
        )
        fig_heat = px.imshow(
            pivot, text_auto=True, color_continuous_scale="RdYlGn",
            labels={"color": "Success %"},
            title=f"% of transfers who became High Value or Solid Addition at {selected_rec_dest}",
        )
        fig_heat.update_layout(margin=dict(t=50, b=20))
        st.plotly_chart(fig_heat, use_container_width=True)

    except Exception as e:
        st.info(f"No data yet — run ETL scripts first. ({e})")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Player Fit Finder
# ═══════════════════════════════════════════════════════════════════════════════
with tab5:
    st.title("🔍 Player Fit Finder")
    st.markdown(
        "Enter your profile below. We'll predict which tiers and **specific conferences** "
        "you'd likely thrive in — based on players with similar stats, size, and origin."
    )
    st.info(
        "**BPM (Box Plus/Minus)** measures how many points above average you contribute per 100 possessions.  "
        "**+3.0+** = Starter-level · **+1.0 to 3.0** = Key rotation guy · **0 to +1.0** = Solid backup · **Below 0** = Role / project player"
    )

    st.markdown("### Your Profile")
    col1, col2, col3 = st.columns(3)

    with col1:
        fit_positions = st.multiselect(
            "Position(s)", ["G", "F", "C"],
            default=["G"], key="fit_pos",
            help="Select one or more. Guards include G/F, Forwards include F/C hybrids."
        )
        fit_position = fit_positions[0] if len(fit_positions) == 1 else fit_positions
        fit_tier      = st.selectbox(
            "Current Program Tier", ALL_TIERS,
            format_func=lambda x: TIER_LABELS[x], key="fit_tier"
        )
        fit_composite_on = st.checkbox(
            "I know my On3 Transfer Composite", value=False, key="fit_composite_on",
            help="Turn on if you know your On3/247 portal rating — refines the model projection by recruit tier."
        )
        if fit_composite_on:
            fit_composite = st.slider(
                "On3 Transfer Composite", 40, 100, 75, key="fit_composite",
                help="70–79 = mid tier, 80–89 = high, 90+ = elite"
            )
            if fit_composite >= 90:
                fit_recruit_tier = "elite"
            elif fit_composite >= 80:
                fit_recruit_tier = "high"
            elif fit_composite >= 70:
                fit_recruit_tier = "mid"
            else:
                fit_recruit_tier = "low"
        else:
            fit_recruit_tier = None  # use pooled fallback projection

    with col2:
        fit_height = st.slider("Height (inches)", 68, 88, 76, key="fit_height",
                               help="72 = 6'0\", 76 = 6'4\", 80 = 6'8\"")
        fit_weight = st.slider("Weight (lbs)", 160, 280, 210, key="fit_weight")

    with col3:
        fit_bpm   = st.slider("BPM (last season)", -5.0, 10.0, 2.0, step=0.1, key="fit_bpm")
        fit_usg   = st.slider("Usage Rate % (last season)", 10.0, 35.0, 20.0, step=0.5, key="fit_usg")
        fit_birth = st.number_input("Birth Year", min_value=1998, max_value=2008, value=2002, key="fit_birth")

    st.markdown("---")

    if st.button("Find My Fit", type="primary"):
        _fit_pos_list = expand_positions(fit_positions if fit_positions else ["G", "F", "C"])
        _pos_ph = ",".join(["%s"] * len(_fit_pos_list))
        # HEIGHT_TOL, WEIGHT_TOL, BPM_TOL, BIRTH_TOL defined at module level above
        # Main comparables query — includes conference info for league breakdown
        comp_sql = """
            SELECT
                its.transfer_id,
                its.full_name,
                its.position,
                its.from_school,
                its.from_tier,
                its.to_school,
                its.to_tier,
                c_dest.name           AS to_conference,
                c_dest.abbreviation   AS to_conf_abbr,
                its.season,
                its.bpm_before,
                its.bpm_before_adj,
                its.bpm_after,
                its.transfer_premium,
                its.usage_before,
                its.usage_after,
                its.efficiency_after,
                its.ppg_after,
                its.context_score,
                its.transfer_verdict,
                p.height_in,
                p.weight_lbs,
                p.birth_year,
                p.recruiting_composite
            FROM individual_transfer_scores its
            JOIN players p      ON its.player_id   = p.player_id
            JOIN transfers tr   ON its.transfer_id = tr.transfer_id
            JOIN teams t_to     ON tr.to_team_id   = t_to.team_id
            JOIN conferences c_dest ON t_to.conference_id = c_dest.conference_id
            WHERE its.position IN ({pos_ph})
              AND its.from_tier = %s
              AND its.bpm_before BETWEEN %s AND %s
              AND (p.height_in  IS NULL OR p.height_in  BETWEEN %s AND %s)
              AND (p.weight_lbs IS NULL OR p.weight_lbs BETWEEN %s AND %s)
              AND (p.birth_year IS NULL OR p.birth_year BETWEEN %s AND %s)
              AND its.to_tier NOT IN ('sub_d1', 'international')
            ORDER BY
                ABS(its.bpm_before - %s) +
                ABS(COALESCE(p.height_in, %s) - %s) * 0.1
            LIMIT 120
        """
        _pos_ph_str = ",".join(["%s"] * len(_fit_pos_list))
        comp_sql = comp_sql.replace("{pos_ph}", _pos_ph_str)
        comp_params = [
            *_fit_pos_list, fit_tier,
            fit_bpm - BPM_TOL, fit_bpm + BPM_TOL,
            fit_height - HEIGHT_TOL, fit_height + HEIGHT_TOL,
            fit_weight - WEIGHT_TOL, fit_weight + WEIGHT_TOL,
            fit_birth - BIRTH_TOL, fit_birth + BIRTH_TOL,
            fit_bpm, fit_height, fit_height,
        ]

        try:
            comp = query(comp_sql, comp_params)
            relaxed = False

            if comp.empty:
                # Fallback: drop physical/age filters, keep position + BPM
                comp_sql_relaxed = """
                    SELECT
                        its.transfer_id, its.full_name, its.position,
                        its.from_school, its.from_tier, its.to_school, its.to_tier,
                        c_dest.name         AS to_conference,
                        c_dest.abbreviation AS to_conf_abbr,
                        its.season,
                        its.bpm_before, its.bpm_before_adj, its.bpm_after,
                        its.transfer_premium, its.usage_before, its.usage_after,
                        its.efficiency_after, its.ppg_after, its.context_score,
                        its.transfer_verdict,
                        p.height_in, p.weight_lbs, p.birth_year, p.recruiting_composite
                    FROM individual_transfer_scores its
                    JOIN players p    ON its.player_id   = p.player_id
                    JOIN transfers tr ON its.transfer_id = tr.transfer_id
                    JOIN teams t_to   ON tr.to_team_id   = t_to.team_id
                    JOIN conferences c_dest ON t_to.conference_id = c_dest.conference_id
                    WHERE its.position = %s
                      AND its.from_tier = %s
                      AND its.bpm_before BETWEEN %s AND %s
                      AND its.to_tier NOT IN ('sub_d1', 'international')
                    ORDER BY ABS(its.bpm_before - %s)
                    LIMIT 120
                """
                comp = query(comp_sql_relaxed, [
                    fit_position, fit_tier,
                    fit_bpm - BPM_TOL * 1.5, fit_bpm + BPM_TOL * 1.5,
                    fit_bpm,
                ])
                relaxed = True

            if comp.empty:
                st.warning("Not enough comparable transfers in the dataset yet. Try adjusting your BPM or position.")
            else:
                if relaxed:
                    st.caption("No close physical matches found — showing position/BPM comparables only.")

                SUCCESS_VERDICTS = {"Exceeded Expectations", "High Value", "Solid Addition"}

                # ── SECTION A: Transfer Projection by Tier ────────────────────────
                st.subheader(f"Your Transfer Projection  ·  {len(comp)} comparables found")
                st.caption(
                    "Based on players with the same position, origin tier, and similar BPM "
                    "(±2 pts). Success = High Value or Solid Addition at that level."
                )

                tier_summary = (
                    comp.groupby("to_tier").agg(
                        avg_bpm_after   = ("bpm_after",        "mean"),
                        avg_premium     = ("transfer_premium",  "mean"),
                        success_rate    = ("transfer_verdict",
                                          lambda x: x.isin(SUCCESS_VERDICTS).mean() * 100),
                        n               = ("transfer_id", "count"),
                    )
                    .reset_index()
                )
                tier_summary["to_tier_label"] = tier_summary["to_tier"].map(TIER_LABELS)
                tier_summary["avg_bpm_after"] = tier_summary["avg_bpm_after"].round(2)
                tier_summary["avg_premium"]   = tier_summary["avg_premium"].round(2)
                tier_summary["success_rate"]  = tier_summary["success_rate"].round(1)

                # Model projection — recruit-specific if composite is known, else pooled fallback
                if fit_recruit_tier:
                    proj_df = query(
                        """
                        SELECT to_tier, ROUND(avg_bpm_after::numeric, 2) AS proj_bpm, sample_size
                        FROM tier_pair_expectations
                        WHERE from_tier = %s AND recruit_tier = %s AND sample_size >= 3
                        """,
                        [fit_tier, fit_recruit_tier],
                    )
                else:
                    proj_df = query(
                        """
                        SELECT to_tier, ROUND(avg_bpm_after::numeric, 2) AS proj_bpm, sample_size
                        FROM tier_pair_fallback
                        WHERE from_tier = %s AND sample_size >= 5
                        """,
                        [fit_tier],
                    )
                if not proj_df.empty:
                    tier_summary = tier_summary.merge(
                        proj_df[["to_tier", "proj_bpm"]],
                        on="to_tier", how="left"
                    )
                else:
                    tier_summary["proj_bpm"] = None

                # Sort: Best Fit first (by success rate then avg BPM)
                tier_summary = tier_summary.sort_values(
                    ["success_rate", "avg_bpm_after"], ascending=False
                )

                # Projection cards
                card_cols = st.columns(min(4, len(tier_summary)))
                for i, (_, row) in enumerate(tier_summary.iterrows()):
                    if i >= len(card_cols):
                        break
                    with card_cols[i]:
                        sr = row["success_rate"]
                        if sr >= 55:
                            badge, rating = "🟢", "Best Fit"
                        elif sr >= 38:
                            badge, rating = "🟡", "Viable"
                        else:
                            badge, rating = "🔴", "Risky"

                        st.markdown(f"**{row['to_tier_label']}**")
                        st.markdown(f"{badge} **{rating}**")
                        if pd.notna(row.get("proj_bpm")):
                            proj_help = (
                                "Expected BPM based on your route + recruit tier (NIL-era peer baseline)"
                                if fit_recruit_tier
                                else "Expected BPM for your origin → destination route (pooled, no composite filter)"
                            )
                            st.metric("Model Projection", f"{float(row['proj_bpm']):+.1f} BPM", help=proj_help)
                        st.metric("Success Rate", f"{sr:.0f}%")
                        st.metric("Avg BPM After", f"{row['avg_bpm_after']:+.1f}")
                        st.caption(f"n = {int(row['n'])} comparables")

                # Premium bar chart
                st.markdown("")
                fig_proj = px.bar(
                    tier_summary.sort_values("avg_bpm_after"),
                    x="avg_bpm_after", y="to_tier_label",
                    color="success_rate",
                    color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                    range_color=[20, 70],
                    orientation="h",
                    text="avg_bpm_after",
                    labels={
                        "avg_bpm_after": "Avg BPM After Transfer",
                        "to_tier_label": "",
                        "success_rate":  "Success %",
                    },
                )
                fig_proj.add_vline(x=0.5, line_dash="dot", line_color="#2ecc71", opacity=0.6)
                fig_proj.add_vline(x=0,   line_dash="dash", line_color="white", opacity=0.3)
                fig_proj.update_traces(texttemplate="%{text:+.2f}", textposition="outside")
                fig_proj.update_layout(
                    margin=dict(t=10, b=10), coloraxis_showscale=True,
                    coloraxis_colorbar=dict(title="Success %", thickness=12),
                )
                st.plotly_chart(fig_proj, use_container_width=True)

                # ── SECTION B: Best Leagues for Your Profile ──────────────────────
                st.markdown("---")
                st.subheader("Best Leagues for Your Profile")
                st.caption(
                    "Specific conferences where players like you historically performed best. "
                    "Sorted by avg BPM after transfer. Min 2 comparable players per conference."
                )

                conf_summary = (
                    comp.groupby(["to_tier", "to_conference", "to_conf_abbr"]).agg(
                        avg_bpm     = ("bpm_after",       "mean"),
                        avg_premium = ("transfer_premium", "mean"),
                        success_pct = ("transfer_verdict",
                                      lambda x: x.isin(SUCCESS_VERDICTS).mean() * 100),
                        n           = ("transfer_id", "count"),
                    )
                    .reset_index()
                )
                conf_summary = conf_summary[conf_summary["n"] >= 2].copy()
                conf_summary["avg_bpm"]     = conf_summary["avg_bpm"].round(2)
                conf_summary["avg_premium"] = conf_summary["avg_premium"].round(2)
                conf_summary["success_pct"] = conf_summary["success_pct"].round(1)
                conf_summary["to_tier_label"] = conf_summary["to_tier"].map(TIER_LABELS)

                if conf_summary.empty:
                    st.info("Not enough conference-level data — need at least 2 comparables per league. Try relaxing BPM or position filters.")
                else:
                    # One expander per tier, sorted by avg_bpm within tier
                    for tier_key in TIER_ORDER:
                        tier_label = TIER_LABELS[tier_key]
                        tc = conf_summary[conf_summary["to_tier"] == tier_key].sort_values("avg_bpm", ascending=False)
                        if tc.empty:
                            continue

                        best_conf  = tc.iloc[0]
                        best_badge = "🟢" if best_conf["success_pct"] >= 55 else ("🟡" if best_conf["success_pct"] >= 38 else "🔴")

                        with st.expander(
                            f"{tier_label}  ·  Best: **{best_conf['to_conference']}** "
                            f"({best_badge} {best_conf['avg_bpm']:+.1f} BPM · {best_conf['success_pct']:.0f}% success)",
                            expanded=(tier_key == tier_summary.iloc[0]["to_tier"]),
                        ):
                            display_conf = tc[["to_conference", "avg_bpm", "avg_premium", "success_pct", "n"]].rename(columns={
                                "to_conference": "Conference",
                                "avg_bpm":       "Avg BPM After",
                                "avg_premium":   "Avg Premium",
                                "success_pct":   "Success %",
                                "n":             "Comparables (n)",
                            })

                            # Color code success %
                            def color_conf_row(row):
                                pct = row["Success %"]
                                if pct >= 55:
                                    color = "#1a3d20"
                                elif pct >= 38:
                                    color = "#3d3000"
                                else:
                                    color = "#4a1010"
                                return [f"background-color: {color}"] * len(row)

                            col_conf_chart, col_conf_table = st.columns([3, 2])
                            with col_conf_chart:
                                fig_conf = px.bar(
                                    tc.sort_values("avg_bpm"),
                                    x="avg_bpm", y="to_conference",
                                    color="success_pct",
                                    color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                                    range_color=[0, 75],
                                    orientation="h",
                                    text="avg_bpm",
                                    labels={
                                        "avg_bpm":       "Avg BPM After",
                                        "to_conference": "",
                                        "success_pct":   "Success %",
                                    },
                                )
                                fig_conf.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.3)
                                fig_conf.update_traces(texttemplate="%{text:+.2f}", textposition="outside")
                                fig_conf.update_layout(
                                    margin=dict(t=5, b=5), coloraxis_showscale=False,
                                    height=max(180, len(tc) * 32),
                                )
                                st.plotly_chart(fig_conf, use_container_width=True)
                            with col_conf_table:
                                st.dataframe(
                                    display_conf.style.apply(color_conf_row, axis=1),
                                    use_container_width=True, hide_index=True,
                                )

                # ── SECTION C: Comparable Players — Strong / Athletic / Weak ──────
                st.markdown("---")
                st.subheader("👥 Players Like You — Who Are Your Comps?")
                st.caption(
                    "**Strong Match** = similar BPM *and* similar height/weight for your position. "
                    "**Athletic Profile** = physical fit but different production level. "
                    "**Stat Match** = similar BPM but different build. "
                    "Every player links to their CBB Reference page."
                )

                comp["to_tier_label"] = comp["to_tier"].map(TIER_LABELS)
                comp["height_str"] = comp["height_in"].apply(
                    lambda x: f"{int(x)//12}'{int(x)%12}\"" if pd.notna(x) else "—"
                )
                comp["role"] = comp["bpm_after"].apply(
                    lambda b: ("🏆 Star" if b >= 6.0 else "🌟 Starter" if b >= 3.0
                               else "💪 Key Guy" if b >= 1.0
                               else "✅ Solid Backup" if b >= -0.5 else "📋 Project")
                    if pd.notna(b) else "—"
                )
                comp["cbb_url"] = comp["full_name"].apply(
                    lambda n: f"https://www.sports-reference.com/cbb/search/search.fcgi?search={n.replace(' ', '+')}"
                )

                # Classify match quality
                def _stats_ok(row):
                    return abs(float(row["bpm_before"]) - fit_bpm) <= BPM_TOL if pd.notna(row["bpm_before"]) else False

                def _phys_ok(row):
                    h_ok = abs(int(row["height_in"]) - fit_height) <= HEIGHT_TOL if pd.notna(row["height_in"]) else None
                    w_ok = abs(int(row["weight_lbs"]) - fit_weight) <= WEIGHT_TOL if pd.notna(row["weight_lbs"]) else None
                    # If both known: require both. If only one known: require that one. If neither: no physical match.
                    if h_ok is None and w_ok is None:
                        return False
                    known = [x for x in [h_ok, w_ok] if x is not None]
                    return all(known)

                comp["_stats_ok"] = comp.apply(_stats_ok, axis=1)
                comp["_phys_ok"]  = comp.apply(_phys_ok, axis=1)
                comp["match_type"] = comp.apply(
                    lambda r: "💪 Strong Match" if r["_stats_ok"] and r["_phys_ok"]
                    else ("🏃 Athletic Profile" if r["_phys_ok"]
                    else "📊 Stat Match"), axis=1
                )
                comp["_rank"] = comp["match_type"].map(
                    {"💪 Strong Match": 0, "🏃 Athletic Profile": 1, "📊 Stat Match": 2}
                )
                comp = comp.sort_values(["_rank", "bpm_before"], ascending=[True, False])

                def _color_verdict_cell(val):
                    # Muted dark tones for table cell backgrounds (dark theme).
                    # Different from VERDICT_COLORS which uses bright hues for chart rendering.
                    colors = {
                        "Exceeded Expectations": "#2d1a4a",
                        "High Value":            "#1a4731",
                        "Solid Addition":        "#1a3d20",
                        "Neutral":               "#3d3000",
                        "Didn't Fit":            "#4a1010",
                    }
                    return f"background-color: {colors.get(val, '')}"

                # Summary counts
                sm1, sm2, sm3 = st.columns(3)
                sm1.metric("💪 Strong Match", int((comp["match_type"] == "💪 Strong Match").sum()))
                sm2.metric("🏃 Athletic Profile", int((comp["match_type"] == "🏃 Athletic Profile").sum()))
                sm3.metric("📊 Stat Match", int((comp["match_type"] == "📊 Stat Match").sum()))

                display_cols = {
                    "match_type":       "Match",
                    "role":             "D1 Role",
                    "full_name":        "Player",
                    "cbb_url":          "Profile",
                    "position":         "Pos",
                    "height_str":       "Height",
                    "weight_lbs":       "Wt",
                    "season":           "Season",
                    "from_school":      "From",
                    "to_school":        "To",
                    "to_tier_label":    "Tier",
                    "bpm_before":       "BPM (prev)",
                    "bpm_after":        "BPM (new school)",
                    "transfer_verdict": "Verdict",
                }
                comp["season"] = comp["season"].apply(fmt_season)
                if "transfer_verdict" in comp.columns:
                    comp["transfer_verdict"] = comp["transfer_verdict"].apply(fmt_verdict)
                show_comp = comp[[c for c in display_cols if c in comp.columns]].rename(columns=display_cols)

                def _color_match_row(row):
                    m = str(row.get("Match", ""))
                    if "Strong" in m:
                        return ["background-color: rgba(255,107,0,0.15)"] * len(row)
                    if "Athletic" in m:
                        return ["background-color: rgba(255,140,56,0.08)"] * len(row)
                    return [""] * len(row)

                st.dataframe(
                    show_comp.style
                        .apply(_color_match_row, axis=1)
                        .map(_color_verdict_cell, subset=["Verdict"]),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Profile": st.column_config.LinkColumn("Profile", display_text="🔗 CBB Ref"),
                        "Match":   st.column_config.TextColumn("Match", width="medium"),
                    },
                    height=450,
                )


        except Exception as e:
            st.error(f"Query error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Coach Search
# ═══════════════════════════════════════════════════════════════════════════════
with tab6:
    st.title("🏀 Coach Search — Portal Targeting")

    coach_mode = st.radio(
        "Search mode",
        ["🔍 By Pre-Transfer Profile", "🎯 By Expected Contribution"],
        horizontal=True,
        key="coach_mode",
        help=(
            "Pre-Transfer Profile: find players based on what they did at their previous school. "
            "Expected Contribution: tell us what you need — we show who historically delivered it."
        ),
    )

    # ── Shared: Your Program inputs (used by both modes) ─────────────────────
    st.markdown("---")
    prog_a, prog_b = st.columns(2)
    with prog_a:
        st.markdown("### Your Program")
        coach_dest_tier = st.selectbox(
            "Program Tier", TIER_ORDER,
            format_func=lambda x: TIER_LABELS[x], key="coach_dest"
        )
        try:
            tier_confs = query(
                "SELECT name FROM conferences WHERE tier = %s ORDER BY name",
                [coach_dest_tier]
            )["name"].tolist()
        except Exception:
            tier_confs = []
        coach_conference = st.selectbox(
            "Conference (optional)", ["Any"] + tier_confs, key="coach_conf",
            help="Narrows results to players who transferred into programs in your conference"
        )
    with prog_b:
        st.markdown("### Position & Origin")
        coach_prior_position = st.multiselect(
            "Position(s)", ["G", "F", "C"],
            default=[], key="coach_prior_pos",
            help="Select one or more positions. Forwards include F/C hybrids, Guards include G/F.",
            placeholder="Any position",
        )
        coach_origin_tier = st.selectbox(
            "Recruit From (Origin Tier)", ["Any"] + ALL_TIERS,
            format_func=lambda x: TIER_LABELS.get(x, x), key="coach_origin"
        )

    # ════════════════════════════════════════════════════════════════════════════
    # TRANSFER POOL — Role-based player finder
    # ════════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("### 🗂️ Transfer Pool — Who Can You Get?")
    st.caption(
        "Filter by the role you need. **BPM** = Box Plus/Minus — points above average per 100 possessions. "
        "Think of it as a player\'s impact score. Higher = more impact. "
        "**🟢 In Your Range** = this type of player has historically transferred to programs at your tier or below."
    )

    # BPM bands for each roster role — thresholds match the role scale used throughout the app.
    # Star (6+) = all-conference caliber. Starter (3-6) = reliable starter anywhere.
    # Key Guy (1-3) = core rotation. Solid Backup (-0.5 to 1) = quality depth.
    # Project (<-0.5) = below average but potentially developable.
    ROLE_OPTIONS = {
        "Any Role":        (-20, 20),
        "🏆 Star":         (6.0, 20),
        "🌟 Starter":      (3.0, 6.0),
        "💪 Key Guy":      (1.0, 3.0),
        "✅ Solid Backup": (-0.5, 1.0),
        "📋 Project":      (-20, -0.5),
    }

    def bpm_to_role(bpm):
        if pd.isna(bpm):
            return "—"
        bpm = float(bpm)
        if bpm >= 6.0:   return "🏆 Star"
        if bpm >= 3.0:   return "🌟 Starter"
        if bpm >= 1.0:   return "💪 Key Guy"
        if bpm >= -0.5:  return "✅ Solid Backup"
        return "📋 Project"

    pool_col1, pool_col2, pool_col3, pool_col4 = st.columns(4)
    with pool_col1:
        pool_role = st.selectbox(
            "Target Role",
            list(ROLE_OPTIONS.keys()),
            key="pool_role",
            help="What kind of player are you looking for? Based on their BPM at their previous school.",
        )
    with pool_col2:
        pool_season = st.selectbox("Season", ["All"] + sorted(seasons, reverse=True), key="pool_season", format_func=lambda x: x if x == "All" else fmt_season(x))
    with pool_col3:
        pool_pos = st.multiselect(
            "Position(s)", ["G", "F", "C"],
            default=[], key="pool_pos",
            placeholder="Any position",
        )
    with pool_col4:
        pool_show_all = st.checkbox(
            "Show all tiers (not just in-range)", value=False, key="pool_show_all"
        )

    bpm_lo, bpm_hi = ROLE_OPTIONS[pool_role]

    pool_sql = """
        SELECT
            its.full_name, its.position,
            its.from_school, its.from_tier,
            its.to_school, its.to_tier,
            its.season,
            its.bpm_before, its.bpm_after,
            its.usage_before, its.usage_after,
            its.transfer_verdict, its.recruiting_composite,
            p.height_in, p.weight_lbs
        FROM individual_transfer_scores its
        JOIN players p ON its.player_id = p.player_id
        WHERE its.bpm_before BETWEEN %s AND %s
          AND its.to_tier NOT IN ('sub_d1', 'international')
    """
    pool_params = [bpm_lo, bpm_hi]

    if pool_season != "All":
        pool_sql += " AND its.season = %s"
        pool_params.append(pool_season)
    if pool_pos:
        adjacent = {
            "G": ["G","G/F"], "G/F": ["G","G/F","F"],
            "F": ["G/F","F","F/C"], "F/C": ["F","F/C","C"], "C": ["F/C","C"],
        }
        pos_pool_set = set()
        for p in pool_pos:
            pos_pool_set.update(adjacent.get(p, [p]))
        pos_pool_list = list(pos_pool_set)
        pool_sql += f" AND its.position IN ({','.join(['%s']*len(pos_pool_list))})"
        pool_params.extend(pos_pool_list)

    pool_sql += " ORDER BY its.bpm_before DESC NULLS LAST"

    try:
        pool_df = query(pool_sql, pool_params)

        if pool_df.empty:
            st.info("No players match that role. Try broadening position or season filters.")
        else:
            coach_rank = TIER_RANK.get(coach_dest_tier, 4)
            pool_df["in_range"] = pool_df["to_tier"].apply(
                lambda t: TIER_RANK.get(t, 4) >= coach_rank
            )
            if not pool_show_all:
                pool_df = pool_df[pool_df["in_range"]].copy()

            pool_df["fit"]            = pool_df["in_range"].map({True: "🟢 In Your Range", False: "⚪ Higher Tier"})
            pool_df["projected_role"] = pool_df["bpm_before"].apply(bpm_to_role)
            pool_df["to_tier_label"]  = pool_df["to_tier"].map(TIER_LABELS)
            pool_df["from_tier_label"]= pool_df["from_tier"].map(TIER_LABELS)
            pool_df["height_str"]     = pool_df["height_in"].apply(
                lambda x: f"{int(x)//12}\'{int(x)%12}\"" if pd.notna(x) else "—"
            )

            starters_n = (pool_df["bpm_before"] >= 3.0).sum()
            key_guys_n = ((pool_df["bpm_before"] >= 1.0) & (pool_df["bpm_before"] < 3.0)).sum()
            in_range_n = pool_df["in_range"].sum()
            sv = {"High Value", "Solid Addition", "Exceeded Expectations"}
            success_n  = pool_df["transfer_verdict"].isin(sv).sum()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("In Pool", len(pool_df))
            m2.metric("🟢 In Your Range", int(in_range_n))
            m3.metric("🌟 Starters+", int(starters_n))
            m4.metric("💪 Key Guys", int(key_guys_n))

            pool_df["season"] = pool_df["season"].apply(fmt_season)
            if "transfer_verdict" in pool_df.columns:
                pool_df["transfer_verdict"] = pool_df["transfer_verdict"].apply(fmt_verdict)
            display_pool = pool_df[[
                "fit", "projected_role", "full_name", "position", "height_str", "weight_lbs",
                "season", "from_school", "from_tier_label",
                "to_school", "to_tier_label",
                "bpm_before", "bpm_after", "usage_before",
                "transfer_verdict",
            ]].rename(columns={
                "fit":              "Range",
                "projected_role":   "Role",
                "full_name":        "Player",
                "position":         "Pos",
                "height_str":       "Height",
                "weight_lbs":       "Wt",
                "season":           "Season",
                "from_school":      "From",
                "from_tier_label":  "From Tier",
                "to_school":        "To",
                "to_tier_label":    "To Tier",
                "bpm_before":       "BPM (prev school)",
                "bpm_after":        "BPM (after move)",
                "usage_before":     "USG% (prev)",
                "transfer_verdict": "Outcome",
            })

            def _color_pool_row(row):
                if "🟢" in str(row.get("Range", "")):
                    return ["background-color: rgba(255,107,0,0.12)"] * len(row)
                return [""] * len(row)

            st.dataframe(
                display_pool.style.apply(_color_pool_row, axis=1),
                use_container_width=True,
                hide_index=True,
                height=420,
            )
            st.caption(
                "**BPM (prev school)** = what you'd see scouting them in the portal. "
                "**Outcome** = what they actually delivered after transferring (historical context)."
            )

            # Also show unscored transfers (portal activity from all 364 schools, no BPM req)
            if pool_show_all or pool_role == "Any Role":
                try:
                    unscored_sql = """
                        SELECT DISTINCT
                            p.full_name, p.position,
                            t_from.name AS from_school, c_from.tier AS from_tier,
                            t_to.name AS to_school, c_to.tier AS to_tier,
                            tr.season
                        FROM transfers tr
                        JOIN players p ON tr.player_id = p.player_id
                        JOIN teams t_from ON tr.from_team_id = t_from.team_id
                        JOIN teams t_to ON tr.to_team_id = t_to.team_id
                        JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
                        JOIN conferences c_to ON t_to.conference_id = c_to.conference_id
                        LEFT JOIN individual_transfer_scores its ON tr.transfer_id = its.transfer_id
                        WHERE its.transfer_id IS NULL
                          AND c_to.tier NOT IN ('sub_d1','international')
                          AND c_from.tier NOT IN ('international')
                    """
                    u_params = []
                    if pool_season != "All":
                        unscored_sql += " AND tr.season = %s"
                        u_params.append(pool_season)
                    if pool_pos:
                        pos_list2 = expand_positions(pool_pos)
                        unscored_sql += f" AND p.position IN ({','.join(['%s']*len(pos_list2))})"
                        u_params.extend(pos_list2)
                    unscored_sql += " ORDER BY tr.season DESC, t_to.name LIMIT 500"

                    u_df = query(unscored_sql, u_params or None)
                    if not u_df.empty:
                        u_df["to_tier_label"]   = u_df["to_tier"].map(TIER_LABELS)
                        u_df["from_tier_label"] = u_df["from_tier"].map(TIER_LABELS)
                        with st.expander(f"📊 {len(u_df)} Additional Portal Entries (No BPM Yet)", expanded=False):
                            st.caption("These transfers are in the portal record but don't have CBB Reference BPM data yet. Name + school movement is confirmed.")
                            u_df["season"] = u_df["season"].apply(fmt_season)
                            st.dataframe(
                                u_df[["full_name","position","season","from_school","from_tier_label","to_school","to_tier_label"]].rename(columns={
                                    "full_name":"Player","position":"Pos","season":"Season",
                                    "from_school":"From","from_tier_label":"From Tier",
                                    "to_school":"To","to_tier_label":"To Tier",
                                }),
                                use_container_width=True, hide_index=True, height=300,
                            )
                except Exception:
                    pass
    except Exception as e:
        st.info(f"Pool query error: {e}")

    # ════════════════════════════════════════════════════════════════════════════
    # MODE 1 — Pre-Transfer Profile Search
    # ════════════════════════════════════════════════════════════════════════════
    if coach_mode == "🔍 By Pre-Transfer Profile":
        st.markdown("---")
        st.markdown("### Pre-Transfer Production & Physical Profile")
        st.caption(
            "Filters apply to stats at their **previous school**. "
            "Physical categories are position-relative. "
            "**Strong Match** = production AND physical fit. **Solid Match** = one matches."
        )

        fp1, fp2, fp3 = st.columns(3)
        with fp1:
            coach_bpm_min = st.slider("Min BPM (pre-transfer)", -5.0, 8.0, 0.0, 0.5, key="coach_bpm",
                                      help="0.0 = at least average production at previous school")
            coach_usg_min = st.slider("Min USG% (pre-transfer)", 10.0, 35.0, 14.0, 1.0, key="coach_usg",
                                      help="14% = floor for a meaningful role player")
        with fp2:
            coach_height_cat = st.selectbox(
                "Height Profile", ["Any", "Short", "Average", "Tall"], key="coach_height_cat",
                help="Relative to their position — 6'4\" C is Short; 6'4\" G is Tall"
            )
            coach_weight_cat = st.selectbox(
                "Build", ["Any", "Lean", "Average", "Heavy"], key="coach_weight_cat",
                help="Position-relative body type"
            )
        with fp3:
            coach_birth_min = st.number_input("Born After (year)", 1998, 2007, 2000, key="coach_birth_min")
            coach_birth_max = st.number_input("Born Before (year)", 1998, 2008, 2006, key="coach_birth_max")
            coach_season_p1 = st.selectbox(
                "Season (optional)", ["Any"] + sorted(seasons, reverse=True), key="coach_season_p1", format_func=lambda x: x if x == "Any" else fmt_season(x)
            )

        coach_show_all = st.checkbox(
            "Show transfers to all D1 tiers (not just your tier)",
            value=True, key="coach_show_all",
            help="Unchecked = only players who transferred into programs at your exact tier.",
        )

        if st.button("Search Players", type="primary", key="coach_search_btn_p1"):
            coach_sql = """
                SELECT
                    its.full_name, its.position, its.height_in, its.weight_lbs,
                    its.birth_year, its.recruiting_composite,
                    its.from_school, its.from_tier,
                    its.to_school, its.to_tier,
                    c_dest.name AS to_conference,
                    its.season,
                    its.bpm_before, its.bpm_before_adj, its.bpm_after, its.bpm_change,
                    its.usage_before, its.usage_after, its.usg_change,
                    its.context_score, its.transfer_premium, its.transfer_verdict
                FROM individual_transfer_scores its
                JOIN transfers tr       ON its.transfer_id      = tr.transfer_id
                JOIN teams t_dest       ON tr.to_team_id        = t_dest.team_id
                JOIN conferences c_dest ON t_dest.conference_id = c_dest.conference_id
                WHERE its.to_tier NOT IN ('sub_d1', 'international')
                  AND (its.birth_year IS NULL OR its.birth_year BETWEEN %s AND %s)
            """
            coach_params = [coach_birth_min, coach_birth_max]

            d1_origins = {"high_major", "high_mid_major", "mid_major", "low_major"}
            if coach_origin_tier in d1_origins:
                coach_sql += " AND its.bpm_before >= %s AND (its.usage_before IS NULL OR its.usage_before >= %s)"
                coach_params += [coach_bpm_min, coach_usg_min]
            elif coach_origin_tier == "Any":
                coach_sql += """
                  AND (its.from_tier IN ('sub_d1','international') OR its.bpm_before >= %s)
                  AND (its.from_tier IN ('sub_d1','international') OR its.usage_before IS NULL OR its.usage_before >= %s)
                """
                coach_params += [coach_bpm_min, coach_usg_min]

            if coach_origin_tier != "Any":
                coach_sql += " AND its.from_tier = %s"
                coach_params.append(coach_origin_tier)

            if coach_prior_position:
                pos_pool = expand_positions(coach_prior_position)
                coach_sql += f" AND its.position IN ({','.join(['%s']*len(pos_pool))})"
                coach_params.extend(pos_pool)

            if coach_conference != "Any":
                coach_sql += " AND c_dest.name = %s"
                coach_params.append(coach_conference)

            if coach_season_p1 != "Any":
                coach_sql += " AND its.season = %s"
                coach_params.append(coach_season_p1)

            if not coach_show_all:
                coach_sql += " AND its.to_tier = %s"
                coach_params.append(coach_dest_tier)

            coach_sql += " ORDER BY its.bpm_before DESC NULLS LAST"

            try:
                results = query(coach_sql, coach_params)

                if results.empty:
                    st.warning("No players match. Try broadening position, lowering BPM/USG floors, or changing origin tier.")
                else:
                    results["to_tier_label"]   = results["to_tier"].map(TIER_LABELS)
                    results["from_tier_label"] = results["from_tier"].map(TIER_LABELS)
                    my_tier_label = TIER_LABELS.get(coach_dest_tier, coach_dest_tier)

                    results["height_cat"] = results.apply(
                        lambda r: _phys_cat(r["height_in"], r["position"], HEIGHT_BUCKETS) or "—", axis=1)
                    results["weight_cat"] = results.apply(
                        lambda r: _phys_cat(r["weight_lbs"], r["position"], WEIGHT_BUCKETS) or "—", axis=1)

                    def _phys_ok_p1(row):
                        h = (coach_height_cat == "Any") or (row["height_cat"] == coach_height_cat)
                        w = (coach_weight_cat == "Any") or (row["weight_cat"] == coach_weight_cat)
                        return h and w

                    def _stats_ok_p1(row):
                        bpm = pd.isna(row["bpm_before"]) or float(row["bpm_before"]) >= coach_bpm_min
                        usg = pd.isna(row["usage_before"]) or float(row["usage_before"]) >= coach_usg_min
                        return bpm and usg

                    results["_phys_ok"]  = results.apply(_phys_ok_p1, axis=1)
                    results["_stats_ok"] = results.apply(_stats_ok_p1, axis=1)
                    results["match_quality"] = results.apply(
                        lambda r: (
                            "Strong Match" if r["_stats_ok"] and r["_phys_ok"]
                            else "Solid Match" if r["_stats_ok"] or r["_phys_ok"]
                            else None
                        ), axis=1,
                    )
                    results = results[results["match_quality"].notna()].copy()
                    results["_mrank"]     = results["match_quality"].map({"Strong Match": 0, "Solid Match": 1})
                    results["_tier_rank"] = (results["to_tier"] != coach_dest_tier).astype(int)
                    results = results.sort_values(
                        ["_mrank", "_tier_rank", "bpm_before"], ascending=[True, True, False]
                    ).drop(columns=["_mrank", "_tier_rank", "_phys_ok", "_stats_ok"])

                    if results.empty:
                        st.warning("No matches after physical filtering. Try 'Any' for height/build.")
                    else:
                        strong_n = (results["match_quality"] == "Strong Match").sum()
                        solid_n  = (results["match_quality"] == "Solid Match").sum()
                        same_tier = results[results["to_tier"] == coach_dest_tier]
                        SV = {"High Value", "Solid Addition", "Exceeded Expectations"}
                        conf_str = f" ({coach_conference})" if coach_conference != "Any" else ""

                        if len(same_tier) > 0:
                            sp = same_tier["transfer_verdict"].isin(SV).mean() * 100
                            st.success(
                                f"**{strong_n} Strong · {solid_n} Solid** · "
                                f"{len(same_tier)} went to {my_tier_label}{conf_str} — "
                                f"{sp:.0f}% High-Impact or Positive Acquisition"
                            )
                        else:
                            st.info(
                                f"**{strong_n} Strong · {solid_n} Solid** · "
                                f"None went to {my_tier_label} — enable 'Show all D1 tiers'."
                            )

                        if len(same_tier) >= 3:
                            col_v, col_bx = st.columns(2)
                            with col_v:
                                st.markdown(f"**Outcomes at {my_tier_label}**")
                                vc = same_tier["transfer_verdict"].value_counts().reset_index()
                                vc.columns = ["Verdict", "Count"]
                                fv = px.bar(vc, x="Verdict", y="Count", color="Verdict",
                                            color_discrete_map=VERDICT_COLORS,
                                            category_orders={"Verdict": VERDICT_DISPLAY_ORDER})
                                fv.update_layout(margin=dict(t=10, b=10), showlegend=False)
                                st.plotly_chart(fv, use_container_width=True)
                            with col_bx:
                                st.markdown("**BPM Before → After**")
                                bm = same_tier[["bpm_before","bpm_after"]].dropna().melt(
                                    var_name="When", value_name="BPM")
                                bm["When"] = bm["When"].map({"bpm_before":"Before","bpm_after":"After"})
                                fb = px.box(bm, x="When", y="BPM", color="When",
                                            color_discrete_map={"Before":"#2e75b6","After":"#2ecc71"})
                                fb.add_hline(y=0, line_dash="dash", line_color="white", opacity=0.4)
                                fb.update_layout(margin=dict(t=10, b=10), showlegend=False)
                                st.plotly_chart(fb, use_container_width=True)

                        mc1, mc2, mc3, mc4 = st.columns(4)
                        mc1.metric("Strong Matches", strong_n)
                        mc2.metric("Solid Matches", solid_n)
                        mc3.metric("Avg BPM Before",
                                   f"{results['bpm_before'].mean():.2f}" if results["bpm_before"].notna().any() else "—")
                        mc4.metric(f"Avg BPM After ({my_tier_label})",
                                   f"{same_tier['bpm_after'].mean():.2f}" if len(same_tier) > 0 else "—")

                        st.subheader("Matching Players")
                        st.caption(
                            "**Strong Match** = production + physical fit. "
                            "**Solid Match** = one but not both. "
                            f"Players who went to {my_tier_label} appear first."
                        )
                        results["height_str"] = results["height_in"].apply(
                            lambda x: f"{int(x)//12}'{int(x)%12}\"" if pd.notna(x) else "—")
                        results["cbb_ref"] = results["full_name"].apply(
                            lambda n: f"https://www.sports-reference.com/cbb/search/search.fcgi?search={n.replace(' ','+')}")
                        results["season"] = results["season"].apply(fmt_season)
                        if "transfer_verdict" in results.columns:
                            results["transfer_verdict"] = results["transfer_verdict"].apply(fmt_verdict)
                        p1_cols = {
                            "match_quality":"Match","full_name":"Player","cbb_ref":"CBB Ref",
                            "position":"Pos","height_str":"Height","height_cat":"Ht Profile",
                            "weight_lbs":"Wt","weight_cat":"Build","birth_year":"Born",
                            "season":"Season","from_school":"From","from_tier_label":"From Tier",
                            "bpm_before":"BPM Before","bpm_before_adj":"BPM Before (adj)",
                            "usage_before":"USG Before",
                            "to_school":"To School","to_tier_label":"To Tier",
                            "bpm_after":"BPM After","context_score":"Context Score",
                            "transfer_verdict":"Verdict",
                        }
                        st.dataframe(
                            results[[c for c in p1_cols if c in results.columns]].rename(columns=p1_cols),
                            use_container_width=True, hide_index=True,
                            column_config={
                                "CBB Ref": st.column_config.LinkColumn("CBB Ref", display_text="🔗 Profile"),
                                "Match": st.column_config.TextColumn("Match", width="small"),
                            },
                        )
            except Exception as e:
                st.error(f"Query error: {e}")

    # ════════════════════════════════════════════════════════════════════════════
    # MODE 2 — Expected Contribution Search
    # ════════════════════════════════════════════════════════════════════════════
    else:
        st.markdown("---")
        st.markdown("### What You Need from the Transfer")
        st.caption(
            "Define the contribution you expect at your level. We find every historical transfer "
            "who delivered that — then show you **what they looked like before transferring** "
            "so you know the pre-transfer profile to target in the portal."
        )

        ec1, ec2 = st.columns(2)
        with ec1:
            ec_bpm_min = st.slider(
                "Min BPM After Transfer", -3.0, 8.0, 1.0, 0.5, key="ec_bpm_min",
                help="Minimum acceptable BPM at your tier. 0.5 = Solid Addition floor. 2.0 = High Value."
            )
            ec_usg_after = st.slider(
                "Min Usage % After Transfer", 10.0, 35.0, 15.0, 1.0, key="ec_usg_after",
                help="Minimum usage rate you need from this player."
            )
            ec_usg_filter = st.checkbox("Apply usage filter", value=False, key="ec_usg_filter")
        with ec2:
            ec_verdict = st.multiselect(
                "Evaluation Filter",
                VERDICT_ORDER,
                default=["High Value", "Solid Addition", "Exceeded Expectations"],
                key="ec_verdict",
                format_func=fmt_verdict,
                help="Only show players who met or exceeded this evaluation threshold after transferring."
            )
            ec_season_p2 = st.selectbox(
                "Season (optional)", ["Any"] + sorted(seasons, reverse=True), key="ec_season_p2", format_func=lambda x: x if x == "Any" else fmt_season(x)
            )

        if st.button("Find the Profile", type="primary", key="coach_search_btn_p2"):
            ec_sql = """
                SELECT
                    its.full_name, its.position, its.height_in, its.weight_lbs,
                    its.birth_year, its.recruiting_composite,
                    its.from_school, its.from_tier,
                    its.to_school, its.to_tier,
                    c_dest.name AS to_conference,
                    its.season,
                    its.bpm_before, its.bpm_before_adj,
                    its.usage_before, its.usage_after,
                    its.bpm_after, its.context_score,
                    its.transfer_premium, its.transfer_verdict
                FROM individual_transfer_scores its
                JOIN transfers tr       ON its.transfer_id      = tr.transfer_id
                JOIN teams t_dest       ON tr.to_team_id        = t_dest.team_id
                JOIN conferences c_dest ON t_dest.conference_id = c_dest.conference_id
                WHERE its.to_tier = %s
                  AND its.bpm_after >= %s
            """
            ec_params = [coach_dest_tier, ec_bpm_min]

            if ec_verdict:
                ec_sql += f" AND its.transfer_verdict IN ({','.join(['%s']*len(ec_verdict))})"
                ec_params.extend(ec_verdict)

            if ec_usg_filter:
                ec_sql += " AND (its.usage_after IS NULL OR its.usage_after >= %s)"
                ec_params.append(ec_usg_after)

            if coach_origin_tier != "Any":
                ec_sql += " AND its.from_tier = %s"
                ec_params.append(coach_origin_tier)

            if coach_prior_position:
                pos_pool = expand_positions(coach_prior_position)
                ec_sql += f" AND its.position IN ({','.join(['%s']*len(pos_pool))})"
                ec_params.extend(pos_pool)

            if coach_conference != "Any":
                ec_sql += " AND c_dest.name = %s"
                ec_params.append(coach_conference)

            if ec_season_p2 != "Any":
                ec_sql += " AND its.season = %s"
                ec_params.append(ec_season_p2)

            ec_sql += " ORDER BY its.bpm_after DESC, its.context_score DESC"

            try:
                ec_results = query(ec_sql, ec_params)
                my_tier_label = TIER_LABELS.get(coach_dest_tier, coach_dest_tier)

                if ec_results.empty:
                    st.warning(
                        f"No transfers to {my_tier_label} matched that contribution level. "
                        "Try lowering the Min BPM or Context Score, or expanding position/origin."
                    )
                else:
                    ec_results["to_tier_label"]   = ec_results["to_tier"].map(TIER_LABELS)
                    ec_results["from_tier_label"] = ec_results["from_tier"].map(TIER_LABELS)
                    ec_results["height_str"]       = ec_results["height_in"].apply(
                        lambda x: f"{int(x)//12}'{int(x)%12}\"" if pd.notna(x) else "—")
                    ec_results["height_cat"] = ec_results.apply(
                        lambda r: _phys_cat(r["height_in"], r["position"], HEIGHT_BUCKETS) or "—", axis=1)
                    ec_results["weight_cat"] = ec_results.apply(
                        lambda r: _phys_cat(r["weight_lbs"], r["position"], WEIGHT_BUCKETS) or "—", axis=1)

                    # ── Summary: what is the pre-transfer profile of these players? ──
                    st.success(
                        f"**{len(ec_results)} transfers** to **{my_tier_label}** delivered "
                        f"≥ {ec_bpm_min:+.1f} BPM after transferring."
                    )

                    profile_cols = st.columns(4)
                    bpm_b_valid = ec_results["bpm_before"].dropna()
                    bpm_b_adj_valid = ec_results["bpm_before_adj"].dropna()
                    usg_b_valid = ec_results["usage_before"].dropna()
                    profile_cols[0].metric(
                        "Avg BPM Before (raw)",
                        f"{bpm_b_valid.mean():.2f}" if len(bpm_b_valid) else "—",
                        help="Average raw BPM at previous school — this is what to look for in the portal"
                    )
                    profile_cols[1].metric(
                        "Avg BPM Before (adj)",
                        f"{bpm_b_adj_valid.mean():.2f}" if len(bpm_b_adj_valid) else "—",
                        help="Competition-adjusted BPM — accounts for whether they played against high or low competition"
                    )
                    profile_cols[2].metric(
                        "Avg USG% Before",
                        f"{usg_b_valid.mean():.1f}%" if len(usg_b_valid) else "—",
                        help="Average usage rate at previous school"
                    )
                    profile_cols[3].metric(
                        "Avg BPM After",
                        f"{ec_results['bpm_after'].mean():.2f}",
                        help="Average BPM they delivered after arriving at this tier"
                    )

                    st.markdown("---")

                    # ── Profile breakdown: what did they look like before? ──────────
                    col_origin, col_phys = st.columns(2)
                    with col_origin:
                        st.markdown(f"**Where Did They Come From?**")
                        orig_counts = (
                            ec_results.groupby(["from_tier_label","position"])
                            .size().reset_index(name="n")
                        )
                        fig_orig = px.bar(
                            orig_counts, x="from_tier_label", y="n", color="position",
                            barmode="stack",
                            labels={"from_tier_label":"Origin Tier","n":"Players","position":"Pos"},
                            color_discrete_sequence=px.colors.qualitative.Set2,
                        )
                        fig_orig.update_layout(margin=dict(t=10, b=10))
                        st.plotly_chart(fig_orig, use_container_width=True)

                    with col_phys:
                        st.markdown(f"**Pre-Transfer BPM Distribution**")
                        bpm_hist = ec_results["bpm_before"].dropna()
                        if len(bpm_hist) >= 3:
                            fig_hist = px.histogram(
                                bpm_hist, nbins=12,
                                labels={"value":"BPM Before Transfer","count":"Players"},
                                color_discrete_sequence=["#2e75b6"],
                            )
                            fig_hist.add_vline(
                                x=bpm_hist.mean(), line_dash="dash", line_color="#2ecc71",
                                annotation_text=f"Avg: {bpm_hist.mean():.2f}",
                                annotation_position="top right",
                            )
                            fig_hist.update_layout(margin=dict(t=10, b=10), showlegend=False)
                            st.plotly_chart(fig_hist, use_container_width=True)
                        else:
                            st.info("Not enough D1-origin players to show BPM distribution.")

                    # ── Full results table ─────────────────────────────────────────
                    st.markdown("---")
                    st.subheader("Players Who Delivered — and Their Pre-Transfer Profile")
                    st.caption(
                        "These players transferred to your tier and met your contribution target. "
                        "Their **pre-transfer stats** (BPM Before, USG Before) are what you're looking "
                        "for in your next portal recruit."
                    )
                    ec_results["cbb_ref"] = ec_results["full_name"].apply(
                        lambda n: f"https://www.sports-reference.com/cbb/search/search.fcgi?search={n.replace(' ','+')}")

                    ec_results["season"] = ec_results["season"].apply(fmt_season)
                    if "transfer_verdict" in ec_results.columns:
                        ec_results["transfer_verdict"] = ec_results["transfer_verdict"].apply(fmt_verdict)
                    ec_display_cols = {
                        "full_name":"Player","cbb_ref":"CBB Ref",
                        "position":"Pos","height_str":"Height","height_cat":"Ht Profile",
                        "weight_lbs":"Wt","weight_cat":"Build","birth_year":"Born",
                        "from_school":"From","from_tier_label":"From Tier",
                        "bpm_before":"BPM Before","bpm_before_adj":"BPM Before (adj)",
                        "usage_before":"USG Before",
                        "season":"Season","to_school":"To School","to_conference":"Conference",
                        "bpm_after":"BPM After","context_score":"Context Score",
                        "transfer_premium":"Premium","transfer_verdict":"Verdict",
                    }
                    st.dataframe(
                        ec_results[[c for c in ec_display_cols if c in ec_results.columns]]
                        .rename(columns=ec_display_cols),
                        use_container_width=True, hide_index=True,
                        column_config={
                            "CBB Ref": st.column_config.LinkColumn("CBB Ref", display_text="🔗 Profile"),
                        },
                    )
            except Exception as e:
                st.error(f"Query error: {e}")
