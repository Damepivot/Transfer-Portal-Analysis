"""
NCAA Transfer Market — Streamlit Dashboard
Run: streamlit run app.py
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
    page_title="NCAA Transfer Market",
    page_icon="🏀",
    layout="wide",
)

TIER_ORDER = ["high_major", "high_mid_major", "mid_major", "low_major"]
TIER_LABELS = {
    "high_major":     "High Major",
    "high_mid_major": "High Mid Major",
    "mid_major":      "Mid Major",
    "low_major":      "Low Major",
}
TIER_COLORS = {
    "High Major":     "#1f4e79",
    "High Mid Major": "#2e75b6",
    "Mid Major":      "#9dc3e6",
    "Low Major":      "#bdd7ee",
}

VERDICT_COLORS = {
    "High Value":      "#2ecc71",
    "Solid Addition":  "#27ae60",
    "Neutral":         "#f39c12",
    "Didn't Fit":      "#e74c3c",
}

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
st.sidebar.image("https://upload.wikimedia.org/wikipedia/commons/thumb/2/28/March_Madness_logo.svg/320px-March_Madness_logo.svg.png", width=120)
st.sidebar.title("Filters")

seasons = query("SELECT DISTINCT season FROM transfers ORDER BY season")["season"].tolist()
nil_era_seasons = [s for s in seasons if s >= "2022-23"]
st.sidebar.markdown("**Season**")
era_mode = st.sidebar.radio("Era", ["NIL Era (2022-23+)", "All Seasons"], horizontal=True)
default_seasons = nil_era_seasons if era_mode == "NIL Era (2022-23+)" else seasons
selected_seasons = st.sidebar.multiselect("Seasons", seasons, default=default_seasons)

positions = ["All", "G", "G/F", "F", "F/C", "C"]
selected_pos = st.sidebar.selectbox("Position", positions)

dest_tiers = ["All"] + TIER_ORDER
selected_dest = st.sidebar.selectbox("Destination Tier", dest_tiers, format_func=lambda x: TIER_LABELS.get(x, x))

st.sidebar.markdown("---")
st.sidebar.caption("Data: BartTorvik · Kaggle Transfer Portal · 2021–25")


def season_filter(col="tr.season"):
    if not selected_seasons:
        return "1=1", []
    placeholders = ",".join(["%s"] * len(selected_seasons))
    return f"{col} IN ({placeholders})", selected_seasons


def pos_filter(col="p.position"):
    if selected_pos == "All":
        return "1=1", []
    return f"{col} = %s", [selected_pos]


def dest_filter(col="c_to.tier"):
    if selected_dest == "All":
        return "1=1", []
    return f"{col} = %s", [selected_dest]


def build_where(*filters):
    clauses, params = [], []
    for clause, p in filters:
        clauses.append(clause)
        params.extend(p)
    return "WHERE " + " AND ".join(clauses), params


# ── Page tabs ─────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Transfer Overview",
    "👤 Individual Scores",
    "📈 League Trends",
    "🎯 Recruit Profiles",
    "🔍 Player Fit Finder",
    "🏀 Coach Search",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Transfer Overview
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.title("NCAA Transfer Market Overview")
    st.caption("Men's basketball · 2021–2025 · Post-NIL era")

    sf, sp = season_filter()
    where, params = build_where((sf, sp))

    # KPI row
    kpi_sql = f"""
        SELECT
            COUNT(*)                                          AS total_transfers,
            COUNT(DISTINCT t.player_id)                      AS unique_players,
            COUNT(DISTINCT t.to_team_id)                     AS destination_teams,
            ROUND(AVG(ps.ppg)::numeric, 1)                   AS avg_ppg,
            ROUND(AVG(ps.bpm)::numeric, 2)                   AS avg_bpm
        FROM transfers t
        JOIN players p       ON t.player_id = p.player_id
        JOIN player_seasons ps ON t.player_id = ps.player_id AND ps.season = t.season
        JOIN teams t_to      ON t.to_team_id = t_to.team_id
        JOIN conferences c_to ON t_to.conference_id = c_to.conference_id
        {where.replace("tr.season", "t.season").replace("p.position", "p.position").replace("c_to.tier", "c_to.tier")}
    """
    try:
        kpi = query(kpi_sql, params)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total Transfers", f"{int(kpi['total_transfers'][0]):,}")
        c2.metric("Unique Players", f"{int(kpi['unique_players'][0]):,}")
        c3.metric("Destination Schools", f"{int(kpi['destination_teams'][0]):,}")
        c4.metric("Avg PPG (post-transfer)", kpi['avg_ppg'][0])
        c5.metric("Avg BPM (post-transfer)", kpi['avg_bpm'][0])
    except Exception:
        st.info("Load data to see KPIs.")

    st.markdown("---")

    col_left, col_right = st.columns(2)

    # Transfer flow heatmap
    with col_left:
        st.subheader("Transfer Flow by Tier")
        flow_sql = f"""
            SELECT
                COALESCE(NULLIF(c_from.tier,''), 'unknown') AS from_tier,
                c_to.tier                                   AS to_tier,
                COUNT(*)                                    AS transfers
            FROM transfers tr
            JOIN players p       ON tr.player_id    = p.player_id
            JOIN teams t_from    ON tr.from_team_id = t_from.team_id
            JOIN teams t_to      ON tr.to_team_id   = t_to.team_id
            JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
            JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
            {build_where((season_filter("tr.season")[0], season_filter("tr.season")[1]),
                         (pos_filter()[0], pos_filter()[1]))[0]}
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
            fig = px.bar(vol, x="season", y="transfers", color_discrete_sequence=["#2e75b6"])
            fig.update_layout(margin=dict(t=20, b=20), xaxis_title="", yaxis_title="# Transfers")
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.info(f"No data yet. ({e})")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Individual Transfer Scores
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.title("Individual Transfer Performance")
    st.markdown("**Transfer Premium** = actual BPM minus the avg BPM of peers who made the same tier-to-tier jump. Positive = beat the projection.")

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
        WHERE 1=1
    """
    filters_its = []
    params_its  = []

    if selected_seasons:
        its_sql += f" AND season IN ({','.join(['%s']*len(selected_seasons))})"
        params_its.extend(selected_seasons)
    if selected_pos != "All":
        its_sql += " AND position = %s"
        params_its.append(selected_pos)
    if selected_dest != "All":
        its_sql += " AND to_tier = %s"
        params_its.append(selected_dest)

    its_sql += " ORDER BY transfer_premium DESC"

    try:
        its = query(its_sql, params_its or None)
        its["from_tier_label"] = its["from_tier"].map(TIER_LABELS)
        its["to_tier_label"]   = its["to_tier"].map(TIER_LABELS)

        # Summary strip
        c1, c2, c3, c4 = st.columns(4)
        vc = its["transfer_verdict"].value_counts()
        c1.metric("High Value",     vc.get("High Value", 0))
        c2.metric("Solid Addition", vc.get("Solid Addition", 0))
        c3.metric("Neutral",        vc.get("Neutral", 0))
        c4.metric("Didn't Fit",     vc.get("Didn't Fit", 0))

        col_l, col_r = st.columns([3, 2])

        with col_l:
            st.subheader("Transfer Premium — Top & Bottom 20")
            display = pd.concat([its.head(10), its.tail(10)]).drop_duplicates()
            fig = px.bar(
                display.sort_values("transfer_premium"),
                x="transfer_premium",
                y="full_name",
                color="transfer_verdict",
                color_discrete_map=VERDICT_COLORS,
                orientation="h",
                hover_data=["from_school", "to_school", "season", "bpm_before", "bpm_after"],
                labels={"transfer_premium": "Transfer Premium (BPM)", "full_name": ""},
            )
            fig.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.4)
            fig.update_layout(margin=dict(t=20, b=20), legend_title="")
            st.plotly_chart(fig, use_container_width=True)

        with col_r:
            st.subheader("Verdict Breakdown by Tier Jump")
            tier_verdict = (
                its.groupby(["to_tier_label", "transfer_verdict"])
                .size().reset_index(name="count")
            )
            fig2 = px.bar(
                tier_verdict,
                x="to_tier_label",
                y="count",
                color="transfer_verdict",
                color_discrete_map=VERDICT_COLORS,
                barmode="stack",
                labels={"to_tier_label": "Destination Tier", "count": "Players"},
            )
            fig2.update_layout(margin=dict(t=20, b=20), legend_title="")
            st.plotly_chart(fig2, use_container_width=True)

        st.subheader("Full Transfer Table")
        show_cols = ["full_name", "position", "season", "from_school", "from_tier_label",
                     "to_school", "to_tier_label", "bpm_before", "bpm_after", "bpm_change",
                     "usage_before", "usage_after", "usg_change",
                     "context_score", "transfer_premium", "transfer_verdict"]
        st.dataframe(
            its[show_cols].rename(columns={
                "full_name":        "Player",
                "position":         "Pos",
                "season":           "Season",
                "from_school":      "From",
                "from_tier_label":  "From Tier",
                "to_school":        "To",
                "to_tier_label":    "To Tier",
                "bpm_before":       "BPM Before",
                "bpm_after":        "BPM After",
                "bpm_change":       "BPM Δ",
                "usage_before":     "USG Before",
                "usage_after":      "USG After",
                "usg_change":       "USG Δ",
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
    st.title("League Transfer Trends")
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
        heat = (
            ltt.groupby(["origin_label", "dest_label"])
            .agg(total=("transfer_count","sum"), hv=("high_value","sum"), sa=("solid_addition","sum"))
            .reset_index()
        )
        heat["success_pct"] = ((heat["hv"] + heat["sa"]) / heat["total"] * 100).round(1)
        pivot_heat = heat.pivot(index="origin_label", columns="dest_label", values="success_pct").fillna(0)
        tier_order_labels = [TIER_LABELS[t] for t in TIER_ORDER if TIER_LABELS[t] in pivot_heat.index]
        pivot_heat = pivot_heat.reindex(index=tier_order_labels, columns=tier_order_labels, fill_value=0)
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
                ltt.groupby(["dest_label", "position"])
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
                ltt.groupby(["origin_label", "dest_label"])
                .agg(avg_ctx=("avg_context_score","mean"), n=("transfer_count","sum"))
                .reset_index()
            )
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
    st.title("Recruitment Profile Recommendations")
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
                "high_value_count":     "High Value",
                "solid_addition_count": "Solid Addition",
                "neutral_count":        "Neutral",
                "didnt_fit_count":      "Didn't Fit",
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
    st.title("Player Fit Finder")
    st.markdown(
        "Enter your profile below. The model finds players with similar size, age, "
        "position, and production — then shows which destination tiers they thrived in."
    )

    st.markdown("### Your Profile")
    col1, col2, col3 = st.columns(3)

    with col1:
        fit_position = st.selectbox("Position", ["G", "G/F", "F", "F/C", "C"], key="fit_pos")
        fit_tier     = st.selectbox(
            "Current Program Tier", TIER_ORDER,
            format_func=lambda x: TIER_LABELS[x], key="fit_tier"
        )

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
        HEIGHT_TOL = 1
        WEIGHT_TOL = 10
        BPM_TOL    = 2.0
        BIRTH_TOL  = 2

        comp_sql = """
            SELECT
                its.transfer_id,
                its.full_name,
                its.position,
                its.from_school,
                its.from_tier,
                its.to_school,
                its.to_tier,
                its.season,
                its.bpm_before,
                its.bpm_after,
                its.transfer_premium,
                its.usage_after,
                its.efficiency_after,
                its.ppg_after,
                its.transfer_verdict,
                p.height_in,
                p.weight_lbs,
                p.birth_year,
                p.recruiting_composite
            FROM individual_transfer_scores its
            JOIN players p ON its.player_id = p.player_id
            WHERE its.position = %s
              AND its.from_tier = %s
              AND its.bpm_before BETWEEN %s AND %s
              AND (p.height_in IS NULL OR p.height_in BETWEEN %s AND %s)
              AND (p.weight_lbs IS NULL OR p.weight_lbs BETWEEN %s AND %s)
              AND (p.birth_year IS NULL OR p.birth_year BETWEEN %s AND %s)
            ORDER BY
                ABS(its.bpm_before - %s) +
                ABS(COALESCE(p.height_in, %s) - %s) * 0.1
            LIMIT 50
        """
        comp_params = [
            fit_position, fit_tier,
            fit_bpm - BPM_TOL, fit_bpm + BPM_TOL,
            fit_height - HEIGHT_TOL, fit_height + HEIGHT_TOL,
            fit_weight - WEIGHT_TOL, fit_weight + WEIGHT_TOL,
            fit_birth - BIRTH_TOL, fit_birth + BIRTH_TOL,
            fit_bpm, fit_height, fit_height,
        ]

        try:
            comp = query(comp_sql, comp_params)

            if comp.empty:
                comp_sql_relaxed = """
                    SELECT
                        its.transfer_id, its.full_name, its.position,
                        its.from_school, its.from_tier, its.to_school, its.to_tier,
                        its.season, its.bpm_before, its.bpm_after, its.transfer_premium,
                        its.usage_after, its.efficiency_after, its.ppg_after,
                        its.transfer_verdict,
                        p.height_in, p.weight_lbs, p.birth_year, p.recruiting_composite
                    FROM individual_transfer_scores its
                    JOIN players p ON its.player_id = p.player_id
                    WHERE its.position = %s
                      AND its.from_tier = %s
                      AND its.bpm_before BETWEEN %s AND %s
                    ORDER BY ABS(its.bpm_before - %s)
                    LIMIT 50
                """
                comp = query(comp_sql_relaxed, [
                    fit_position, fit_tier,
                    fit_bpm - BPM_TOL * 1.5, fit_bpm + BPM_TOL * 1.5,
                    fit_bpm,
                ])
                st.caption("No close physical matches found — showing position/BPM matches only.")

            if comp.empty:
                st.warning("Not enough comparable transfers in the dataset yet. Try adjusting your BPM or position.")
            else:
                st.subheader(f"Best Destination Tiers ({len(comp)} comparable transfers found)")

                tier_summary = (
                    comp.groupby("to_tier")
                    .agg(
                        avg_premium=("transfer_premium", "mean"),
                        avg_bpm_after=("bpm_after", "mean"),
                        count=("transfer_id", "count"),
                        pct_outperformed=("transfer_verdict", lambda x: round((x == "Outperformed").mean() * 100, 1)),
                    )
                    .reset_index()
                    .sort_values("avg_premium", ascending=False)
                )
                tier_summary["to_tier_label"] = tier_summary["to_tier"].map(TIER_LABELS)
                tier_summary["avg_premium"]    = tier_summary["avg_premium"].round(2)
                tier_summary["avg_bpm_after"]  = tier_summary["avg_bpm_after"].round(2)

                col_chart, col_table = st.columns([3, 2])

                with col_chart:
                    fig = px.bar(
                        tier_summary.sort_values("avg_premium"),
                        x="avg_premium",
                        y="to_tier_label",
                        color="avg_premium",
                        color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
                        orientation="h",
                        labels={"avg_premium": "Avg Transfer Premium", "to_tier_label": ""},
                        text="avg_premium",
                    )
                    fig.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.5)
                    fig.update_traces(texttemplate="%{text:+.2f}", textposition="outside")
                    fig.update_layout(margin=dict(t=10, b=10), showlegend=False, coloraxis_showscale=False)
                    st.plotly_chart(fig, use_container_width=True)

                with col_table:
                    st.dataframe(
                        tier_summary[["to_tier_label", "avg_premium", "avg_bpm_after", "count", "pct_outperformed"]]
                        .rename(columns={
                            "to_tier_label":    "Destination",
                            "avg_premium":      "Avg Premium",
                            "avg_bpm_after":    "Avg BPM After",
                            "count":            "Sample (n)",
                            "pct_outperformed": "% Outperformed",
                        }),
                        use_container_width=True,
                        hide_index=True,
                    )

                st.subheader("Comparable Players")
                comp["to_tier_label"] = comp["to_tier"].map(TIER_LABELS)
                comp["height_str"]    = comp["height_in"].apply(
                    lambda x: f"{int(x)//12}'{int(x)%12}\"" if pd.notna(x) else "—"
                )
                show = comp[[
                    "full_name", "position", "height_str", "weight_lbs", "birth_year",
                    "season", "from_school", "to_school", "to_tier_label",
                    "bpm_before", "bpm_after", "transfer_premium", "transfer_verdict"
                ]].rename(columns={
                    "full_name":        "Player",
                    "position":         "Pos",
                    "height_str":       "Height",
                    "weight_lbs":       "Wt (lbs)",
                    "birth_year":       "Born",
                    "season":           "Season",
                    "from_school":      "From",
                    "to_school":        "To",
                    "to_tier_label":    "Dest Tier",
                    "bpm_before":       "BPM Before",
                    "bpm_after":        "BPM After",
                    "transfer_premium": "Premium",
                    "transfer_verdict": "Verdict",
                })

                def color_verdict(val):
                    colors = {
                        "High Value":      "#1a4731",
                        "Solid Addition":  "#1a3d20",
                        "Neutral":         "#3d3000",
                        "Didn't Fit":      "#4a1010",
                    }
                    return f"background-color: {colors.get(val, '')}"

                st.dataframe(
                    show.style.applymap(color_verdict, subset=["Verdict"]),
                    use_container_width=True,
                    hide_index=True,
                )

        except Exception as e:
            st.error(f"Query error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Coach Search
# ═══════════════════════════════════════════════════════════════════════════════
with tab6:
    st.title("Coach Search Tool")
    st.markdown(
        "You know what your program needs. Filter by position, tier, physical profile, "
        "and production — see which historical transfers fit and what to expect."
    )

    # ── Section 1: Your Program ───────────────────────────────────────────────
    st.markdown("### Your Program")
    c1, c2, c3 = st.columns(3)
    with c1:
        coach_dest_tier = st.selectbox(
            "Your Program Tier", TIER_ORDER,
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
            "Your Conference",
            ["Any"] + tier_confs,
            key="coach_conf",
            help="Narrows results to transfers that succeeded specifically within your conference — same tier, different cultures",
        )
    with c2:
        coach_season = st.selectbox("Season Focus", ["Any", "2023-24", "2024-25", "2022-23", "2021-22"], key="coach_season")
        coach_verdict = st.multiselect(
            "Minimum Verdict",
            ["High Value", "Solid Addition", "Neutral", "Didn't Fit"],
            default=["High Value", "Solid Addition"],
            key="coach_verdict"
        )
    with c3:
        coach_origin_tier = st.selectbox(
            "Recruit From Tier", ["Any"] + TIER_ORDER,
            format_func=lambda x: TIER_LABELS.get(x, x), key="coach_origin"
        )

    st.markdown("---")

    # ── Section 2: What Did They Do Last Season? ──────────────────────────────
    st.markdown("### What Did They Do Last Season?")
    st.caption("Filter by the player's actual production and role at their previous school.")
    cp1, cp2, cp3 = st.columns(3)
    with cp1:
        coach_prior_position = st.selectbox(
            "Their Position Last Season",
            ["Any", "G", "G/F", "F", "F/C", "C"],
            key="coach_prior_pos",
            help="Position they were listed at before transferring",
        )
        coach_bpm_min = st.slider("Min BPM Last Season", -5.0, 8.0, 0.0, 0.5, key="coach_bpm")
    with cp2:
        coach_usg_min = st.slider("Min USG% Last Season", 10.0, 35.0, 14.0, 1.0, key="coach_usg")
        coach_h_min, coach_h_max = st.slider(
            "Height Range (inches)", 68, 88, (70, 84), key="coach_height",
            help="72=6'0\", 76=6'4\", 80=6'8\""
        )
    with cp3:
        coach_w_min, coach_w_max = st.slider("Weight Range (lbs)", 150, 290, (170, 260), key="coach_weight")
        coach_birth_min = st.number_input("Born After (year)", 1998, 2007, 2000, key="coach_birth_min")
        coach_birth_max = st.number_input("Born Before (year)", 1998, 2008, 2006, key="coach_birth_max")

    st.markdown("---")

    # ── Section 3: What Do You Need From Them This Year? ─────────────────────
    st.markdown("### What Do You Need From Them This Year?")
    st.caption("Set the role and position you'll play them in your system. May differ from what they did before.")
    cr1, cr2, cr3 = st.columns(3)
    with cr1:
        coach_target_position = st.selectbox(
            "Position in Your System",
            ["Any", "G", "G/F", "F", "F/C", "C"],
            key="coach_target_pos",
            help="The position you'll play them — can differ from their prior position (e.g. converting a G/F into a F)",
        )
    with cr2:
        # Roles defined by projected USG% at new tier — MPG data not available
        role_label_map = {
            "Any Role":                  (0,   100),
            "Featured (22%+ USG)":       (22,  100),
            "Rotation (17–22% USG)":     (17,  22),
            "Role Player (12–17% USG)":  (12,  17),
        }
        coach_role_preset  = st.selectbox("Projected Role", list(role_label_map.keys()), key="coach_role_preset")
        coach_proj_usg_min = st.slider("Min Projected USG%", 0.0, 35.0, 0.0, 1.0, key="coach_proj_usg",
                                       help="Projected usage rate at your program based on historical tier averages")
    with cr3:
        if coach_target_position != "Any" and coach_prior_position != "Any" and coach_target_position != coach_prior_position:
            st.info(f"Converting **{coach_prior_position} → {coach_target_position}**: showing players with the physical and production profile to make that switch.")

    st.markdown("---")

    if st.button("Search Players", type="primary", key="coach_search_btn"):
        # Pull league avg USG change for each origin_tier → dest_tier → position move
        proj_sql = """
            SELECT origin_tier, position,
                   COALESCE(avg_usg_change, 0) AS avg_usg_change
            FROM league_transfer_trends
            WHERE dest_tier = %s
            GROUP BY origin_tier, position, avg_usg_change
        """
        try:
            proj_df = query(proj_sql, [coach_dest_tier])
            proj_lookup = {
                (row["origin_tier"], row["position"]): float(row["avg_usg_change"] or 0)
                for _, row in proj_df.iterrows()
            }
        except Exception:
            proj_lookup = {}

        usg_preset_min, usg_preset_max = role_label_map[coach_role_preset]

        coach_sql = """
            SELECT
                its.full_name,
                its.position,
                its.height_in,
                its.weight_lbs,
                its.birth_year,
                its.recruiting_composite,
                its.from_school,
                its.from_tier,
                its.to_school,
                its.to_tier,
                c_dest.name AS to_conference,
                its.season,
                its.bpm_before,
                its.bpm_after,
                its.bpm_change,
                its.usage_before,
                its.usage_after,
                its.usg_change,
                its.obpm_after,
                its.dbpm_after,
                its.context_score,
                its.transfer_premium,
                its.transfer_verdict
            FROM individual_transfer_scores its
            JOIN transfers tr       ON its.transfer_id    = tr.transfer_id
            JOIN teams t_dest       ON tr.to_team_id      = t_dest.team_id
            JOIN conferences c_dest ON t_dest.conference_id = c_dest.conference_id
            WHERE its.to_tier = %s
              AND its.transfer_verdict = ANY(%s)
        """
        coach_params = [
            coach_dest_tier,
            coach_verdict,
        ]

        if coach_conference != "Any":
            coach_sql += " AND c_dest.name = %s"
            coach_params.append(coach_conference)
        if coach_origin_tier != "Any":
            coach_sql += " AND its.from_tier = %s"
            coach_params.append(coach_origin_tier)
        if coach_prior_position != "Any":
            coach_sql += " AND its.position = %s"
            coach_params.append(coach_prior_position)
        if coach_season != "Any":
            coach_sql += " AND its.season = %s"
            coach_params.append(coach_season)
        # If coach specified a target position different from prior, broaden to include
        # adjacent positions that commonly convert (e.g. G/F if looking for F)
        if coach_target_position != "Any" and coach_prior_position == "Any":
            adjacent = {
                "G": ["G", "G/F"],
                "G/F": ["G", "G/F", "F"],
                "F": ["G/F", "F", "F/C"],
                "F/C": ["F", "F/C", "C"],
                "C": ["F/C", "C"],
            }
            target_pool = adjacent.get(coach_target_position, [coach_target_position])
            coach_sql += f" AND its.position IN ({','.join(['%s']*len(target_pool))})"
            coach_params.extend(target_pool)

        coach_sql += " AND (its.height_in IS NULL OR its.height_in BETWEEN %s AND %s)"
        coach_sql += " AND (its.weight_lbs IS NULL OR its.weight_lbs BETWEEN %s AND %s)"
        coach_sql += " AND (its.birth_year IS NULL OR its.birth_year BETWEEN %s AND %s)"
        coach_sql += " ORDER BY its.context_score DESC NULLS LAST"
        coach_params += [coach_h_min, coach_h_max, coach_w_min, coach_w_max, coach_birth_min, coach_birth_max]

        try:
            results = query(coach_sql, coach_params)

            if results.empty:
                st.warning("No players match those filters. Try loosening position, BPM, or physical range.")
            else:
                # ── Project role at coach's tier ──────────────────────────────
                def project_role(row):
                    key = (row["from_tier"], row["position"])
                    usg_change = proj_lookup.get(key, 0)
                    proj_usg = round(max(0, float(row["usage_before"] or 18) + usg_change), 1)
                    if proj_usg >= 22:
                        role = "Featured"
                    elif proj_usg >= 17:
                        role = "Rotation"
                    elif proj_usg >= 12:
                        role = "Role Player"
                    else:
                        role = "Depth"
                    return pd.Series({"proj_usg": proj_usg, "proj_role": role})

                proj_cols = results.apply(project_role, axis=1)
                results = pd.concat([results, proj_cols], axis=1)

                # Classify: Strong Match (all criteria met) vs Match (within tolerance)
                def classify_match(row):
                    bpm_ok   = float(row["bpm_before"]   or 0) >= coach_bpm_min
                    usg_ok   = float(row["usage_before"] or 0) >= coach_usg_min
                    role_ok  = (row["proj_usg"] >= usg_preset_min) and (usg_preset_max >= 100 or row["proj_usg"] <= usg_preset_max)
                    proj_ok  = row["proj_usg"] >= coach_proj_usg_min

                    bpm_close  = float(row["bpm_before"]   or 0) >= coach_bpm_min  - 1.5
                    usg_close  = float(row["usage_before"] or 0) >= coach_usg_min  - 3.0
                    role_close = (row["proj_usg"] >= usg_preset_min - 3.0) and (usg_preset_max >= 100 or row["proj_usg"] <= usg_preset_max + 3.0)
                    proj_close = row["proj_usg"] >= coach_proj_usg_min - 3.0

                    if bpm_ok and usg_ok and role_ok and proj_ok:
                        return "Strong Match"
                    elif bpm_close and usg_close and role_close and proj_close:
                        return "Match"
                    return None

                results["match_quality"] = results.apply(classify_match, axis=1)
                results = results[results["match_quality"].notna()].copy()
                results["_sort"] = results["match_quality"].map({"Strong Match": 0, "Match": 1})
                results = results.sort_values(["_sort", "context_score"], ascending=[True, False]).drop(columns=["_sort"])

                if results.empty:
                    st.warning("No players match those filters. Try loosening BPM, USG%, or role preset.")
                else:
                    label = TIER_LABELS.get(coach_dest_tier, coach_dest_tier)
                    conf_str = f" ({coach_conference})" if coach_conference != "Any" else ""
                    pos_str = f" — targeting {coach_prior_position} → {coach_target_position}" if (
                        coach_target_position != "Any" and coach_prior_position != "Any"
                        and coach_target_position != coach_prior_position
                    ) else (f" — {coach_target_position}s" if coach_target_position != "Any" else "")
                    strong_n = (results["match_quality"] == "Strong Match").sum()
                    match_n  = (results["match_quality"] == "Match").sum()
                    st.success(f"**{strong_n} strong matches · {match_n} matches** at {label}{conf_str}{pos_str}")

                    # Role distribution
                    role_colors = {
                        "Featured":    "#2ecc71",
                        "Rotation":    "#f39c12",
                        "Role Player": "#2e75b6",
                        "Depth":       "#7f8c8d",
                    }
                    role_vc = results["proj_role"].value_counts().reset_index()
                    role_vc.columns = ["Role", "Count"]
                    col_role, col_verdict = st.columns(2)
                    with col_role:
                        st.markdown("**Projected Role Breakdown**")
                        fig_role = px.bar(
                            role_vc, x="Role", y="Count",
                            color="Role", color_discrete_map=role_colors,
                        )
                        fig_role.update_layout(margin=dict(t=10, b=10), showlegend=False)
                        st.plotly_chart(fig_role, use_container_width=True)
                    with col_verdict:
                        st.markdown("**Transfer Verdict Breakdown**")
                        vc2 = results["transfer_verdict"].value_counts().reset_index()
                        vc2.columns = ["Verdict", "Count"]
                        fig_v = px.bar(
                            vc2, x="Verdict", y="Count",
                            color="Verdict", color_discrete_map=VERDICT_COLORS,
                        )
                        fig_v.update_layout(margin=dict(t=10, b=10), showlegend=False)
                        st.plotly_chart(fig_v, use_container_width=True)

                    # Summary metrics
                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.metric("Avg Context Score",  f"{results['context_score'].mean():.2f}")
                    mc2.metric("Avg Proj USG%",       f"{results['proj_usg'].mean():.1f}%")
                    mc3.metric("Avg BPM Before",      f"{results['bpm_before'].mean():.2f}")
                    mc4.metric("Avg BPM After",       f"{results['bpm_after'].mean():.2f}")

                    # OBPM vs DBPM
                    if results["obpm_after"].notna().any():
                        st.subheader("Offensive vs Defensive Contribution")
                        obpm_avg = pd.DataFrame({
                            "Component": ["OBPM (Offense)", "DBPM (Defense)"],
                            "Value": [results["obpm_after"].mean(), results["dbpm_after"].mean()],
                        })
                        fig_ob = px.bar(
                            obpm_avg, x="Component", y="Value", color="Component",
                            color_discrete_map={"OBPM (Offense)": "#2e75b6", "DBPM (Defense)": "#c00000"},
                        )
                        fig_ob.add_hline(y=0, line_dash="dash", line_color="white", opacity=0.4)
                        fig_ob.update_layout(margin=dict(t=10, b=10), showlegend=False)
                        st.plotly_chart(fig_ob, use_container_width=True)

                    # Full results table
                    st.subheader("Matching Players — Strong Matches First")
                    st.caption("Proj USG% shows what this player projects to at YOUR tier. CBB Ref links open on Sports Reference.")
                    results["height_str"] = results["height_in"].apply(
                        lambda x: f"{int(x)//12}'{int(x)%12}\"" if pd.notna(x) else "—"
                    )
                    results["cbb_ref"] = results["full_name"].apply(
                        lambda n: f"https://www.sports-reference.com/cbb/search/search.fcgi?search={n.replace(' ', '+')}"
                    )
                    display_cols = {
                        "match_quality":    "Match",
                        "full_name":        "Player",
                        "cbb_ref":          "CBB Ref",
                        "position":         "Pos (Last Season)",
                        "height_str":       "Height",
                        "weight_lbs":       "Wt",
                        "birth_year":       "Born",
                        "from_school":      "From School",
                        "from_tier":        "From Tier",
                        "to_school":        "To School",
                        "to_conference":    "Conference",
                        "season":           "Season",
                        "proj_role":        "Proj Role (Your Tier)",
                        "proj_usg":         "Proj USG%",
                        "usage_before":     "USG Before",
                        "bpm_before":       "BPM Before",
                        "obpm_after":       "OBPM",
                        "dbpm_after":       "DBPM",
                        "context_score":    "Context Score",
                        "transfer_verdict": "Verdict",
                    }
                    display_df = (
                        results[[c for c in display_cols if c in results.columns]]
                        .rename(columns=display_cols)
                    )
                    st.dataframe(
                        display_df,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "CBB Ref": st.column_config.LinkColumn("CBB Ref", display_text="🔗 Profile"),
                            "Match": st.column_config.TextColumn("Match", width="small"),
                        },
                    )

        except Exception as e:
            st.error(f"Query error: {e}")
