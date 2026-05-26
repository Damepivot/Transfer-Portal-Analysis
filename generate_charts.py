"""
Generate static PNG charts for the GitHub README.
Run: python3 generate_charts.py
Outputs to: assets/
"""

import sys
import psycopg2
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import DB_CONFIG

ASSETS = Path(__file__).parent / "assets"
ASSETS.mkdir(exist_ok=True)

TIER_LABELS = {
    "high_major":     "High Major",
    "high_mid_major": "High Mid Major",
    "mid_major":      "Mid Major",
    "low_major":      "Low Major",
}
TIER_ORDER = ["high_major", "high_mid_major", "mid_major", "low_major"]
TIER_DISPLAY = [TIER_LABELS[t] for t in TIER_ORDER]

VERDICT_COLORS = {
    "High Value":     "#2ecc71",
    "Solid Addition": "#f39c12",
    "Neutral":        "#95a5a6",
    "Didn't Fit":     "#e74c3c",
}

BG      = "#0e1117"
GRID    = "#1e2530"
TEXT    = "#e0e0e0"
ACCENT  = "#2e75b6"

LAYOUT = dict(
    paper_bgcolor=BG,
    plot_bgcolor=BG,
    font=dict(color=TEXT, family="Inter, Arial, sans-serif", size=13),
    margin=dict(t=60, b=60, l=60, r=40),
)


def q(sql, params=None):
    from decimal import Decimal
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = [
        [float(v) if isinstance(v, Decimal) else v for v in row]
        for row in cur.fetchall()
    ]
    cur.close(); conn.close()
    return pd.DataFrame(rows, columns=cols)


def save(fig, name, w=900, h=520):
    path = ASSETS / f"{name}.png"
    fig.write_image(str(path), width=w, height=h, scale=2)
    print(f"  saved {path.name}")


# ── 1. Tier-to-tier success rate heatmap ─────────────────────────────────────
def chart_heatmap():
    df = q("""
        SELECT from_tier, to_tier,
               ROUND(100.0 * SUM(CASE WHEN transfer_verdict IN ('High Value','Solid Addition')
                                      THEN 1 ELSE 0 END) / COUNT(*), 1) AS success_pct,
               COUNT(*) AS n
        FROM individual_transfer_scores
        GROUP BY from_tier, to_tier
    """)

    matrix = pd.DataFrame(index=TIER_ORDER, columns=TIER_ORDER, dtype=float)
    label_matrix = pd.DataFrame(index=TIER_ORDER, columns=TIER_ORDER, dtype=str)

    for _, row in df.iterrows():
        f, t = row["from_tier"], row["to_tier"]
        if f in matrix.index and t in matrix.columns:
            matrix.loc[f, t] = float(row["success_pct"])
            label_matrix.loc[f, t] = f"{row['success_pct']}%<br>n={row['n']}"

    z = matrix.values.tolist()
    text = label_matrix.values.tolist()

    fig = go.Figure(go.Heatmap(
        z=z,
        x=TIER_DISPLAY,
        y=TIER_DISPLAY,
        text=text,
        texttemplate="%{text}",
        textfont=dict(size=13, color="white"),
        colorscale=[[0, "#1a0a0a"], [0.4, "#8b1a1a"], [0.7, "#2e75b6"], [1.0, "#2ecc71"]],
        zmin=0, zmax=100,
        showscale=True,
        colorbar=dict(title=dict(text="Success %", font=dict(color=TEXT)), tickfont=dict(color=TEXT)),
    ))
    fig.update_layout(
        **LAYOUT,
        title=dict(text="Transfer Success Rate by Tier Movement", font=dict(size=17)),
        xaxis=dict(title="Destination Tier", tickfont=dict(color=TEXT), gridcolor=GRID),
        yaxis=dict(title="Origin Tier", tickfont=dict(color=TEXT), gridcolor=GRID),
    )
    save(fig, "01_tier_heatmap")


# ── 2. Origin tier performance at high-major destinations ────────────────────
def chart_origin_tier():
    df = q("""
        SELECT from_tier,
               COUNT(*) AS n,
               ROUND(100.0 * SUM(CASE WHEN transfer_verdict IN ('High Value','Solid Addition')
                                      THEN 1 ELSE 0 END) / COUNT(*), 1) AS success_pct,
               ROUND(AVG(bpm_after), 2) AS avg_bpm_after,
               ROUND(AVG(context_score), 2) AS avg_ctx
        FROM individual_transfer_scores
        WHERE to_tier = 'high_major'
        GROUP BY from_tier
        ORDER BY success_pct DESC
    """)
    df["label"] = df["from_tier"].map(TIER_LABELS)
    df["bar_label"] = df.apply(lambda r: f"{r['success_pct']}%  (n={r['n']})", axis=1)

    fig = go.Figure()
    colors = [ACCENT if t != "high_major" else "#2ecc71" for t in df["from_tier"]]
    fig.add_trace(go.Bar(
        x=df["label"], y=df["success_pct"],
        marker_color=colors,
        text=df["bar_label"], textposition="outside",
        textfont=dict(color=TEXT),
    ))
    fig.update_layout(
        **LAYOUT,
        title=dict(text="High-Major Transfer Success Rate by Origin Tier", font=dict(size=17)),
        xaxis=dict(title="Origin Tier", tickfont=dict(color=TEXT), gridcolor=GRID),
        yaxis=dict(title="Success Rate (%)", range=[0, 100], tickfont=dict(color=TEXT), gridcolor=GRID),
        showlegend=False,
    )
    save(fig, "02_origin_tier_success")


# ── 3. Top 15 transfers by context score ─────────────────────────────────────
def chart_top_transfers():
    df = q("""
        SELECT full_name, from_school, to_school, season,
               bpm_before, bpm_after, context_score, transfer_verdict
        FROM individual_transfer_scores
        ORDER BY context_score DESC LIMIT 15
    """)
    df["label"] = df["full_name"] + " (" + df["from_school"] + " → " + df["to_school"] + ", " + df["season"] + ")"
    df = df.sort_values("context_score")
    df["color"] = df["transfer_verdict"].map(VERDICT_COLORS)

    fig = go.Figure(go.Bar(
        x=df["context_score"], y=df["label"],
        orientation="h",
        marker_color=df["color"],
        text=df["context_score"].apply(lambda v: f"{v:.1f}"),
        textposition="outside",
        textfont=dict(color=TEXT),
    ))
    layout = {**LAYOUT, "margin": dict(l=360, r=60, t=60, b=60)}
    fig.update_layout(
        **layout,
        title=dict(text="Top 15 Transfers by Context Score", font=dict(size=17)),
        xaxis=dict(title="Context Score", tickfont=dict(color=TEXT), gridcolor=GRID),
        yaxis=dict(tickfont=dict(color=TEXT, size=11), gridcolor=GRID),
        height=620,
    )
    save(fig, "03_top_transfers", h=620)


# ── 4. Recruiting composite vs success rate ───────────────────────────────────
def chart_recruit_vs_success():
    df = q("""
        SELECT
            CASE WHEN p.recruiting_composite >= 90 THEN 'Elite (90+)'
                 WHEN p.recruiting_composite >= 80 THEN 'High (80–90)'
                 WHEN p.recruiting_composite >= 70 THEN 'Mid (70–80)'
                 ELSE 'Low (<70)'
            END AS bucket,
            CASE WHEN p.recruiting_composite >= 90 THEN 1
                 WHEN p.recruiting_composite >= 80 THEN 2
                 WHEN p.recruiting_composite >= 70 THEN 3
                 ELSE 4
            END AS sort_order,
            COUNT(*) AS n,
            ROUND(100.0 * SUM(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition')
                                   THEN 1 ELSE 0 END) / COUNT(*), 1) AS success_pct,
            ROUND(AVG(its.bpm_after), 2) AS avg_bpm_after
        FROM individual_transfer_scores its
        JOIN players p ON its.player_id = p.player_id
        GROUP BY bucket, sort_order
        ORDER BY sort_order
    """)

    fig = go.Figure()
    bar_colors = ["#2ecc71", "#f39c12", "#2e75b6", "#95a5a6"]
    fig.add_trace(go.Bar(
        name="Success Rate",
        x=df["bucket"], y=df["success_pct"],
        marker_color=bar_colors,
        text=df.apply(lambda r: f"{r['success_pct']}%<br>n={r['n']}", axis=1),
        textposition="outside",
        textfont=dict(color=TEXT),
        yaxis="y1",
    ))
    fig.add_trace(go.Scatter(
        name="Avg BPM After",
        x=df["bucket"], y=df["avg_bpm_after"],
        mode="lines+markers",
        line=dict(color="#e74c3c", width=3),
        marker=dict(size=10, color="#e74c3c"),
        yaxis="y2",
    ))
    fig.update_layout(
        **LAYOUT,
        title=dict(text="Recruiting Composite vs Transfer Success", font=dict(size=17)),
        xaxis=dict(title="Recruiting Bucket", tickfont=dict(color=TEXT)),
        yaxis=dict(title="Success Rate (%)", range=[0, 105], tickfont=dict(color=TEXT), gridcolor=GRID),
        yaxis2=dict(title=dict(text="Avg BPM After", font=dict(color="#e74c3c")),
                    overlaying="y", side="right", tickfont=dict(color="#e74c3c")),
        legend=dict(bgcolor=BG, bordercolor=GRID),
        showlegend=True,
    )
    save(fig, "04_recruit_vs_success")


# ── 5. Position success at high-major (from high-mid) ────────────────────────
def chart_position_breakdown():
    df = q("""
        SELECT from_tier, position,
               COUNT(*) AS n,
               ROUND(100.0 * SUM(CASE WHEN transfer_verdict IN ('High Value','Solid Addition')
                                      THEN 1 ELSE 0 END) / COUNT(*), 1) AS success_pct,
               ROUND(AVG(context_score), 2) AS avg_ctx
        FROM individual_transfer_scores
        WHERE to_tier = 'high_major'
          AND position IN ('G', 'G/F', 'F', 'F/C', 'C')
        GROUP BY from_tier, position
        HAVING COUNT(*) >= 5
        ORDER BY from_tier, success_pct DESC
    """)
    df["from_label"] = df["from_tier"].map(TIER_LABELS)

    fig = px.bar(
        df, x="position", y="success_pct", color="from_label",
        barmode="group",
        text=df["success_pct"].apply(lambda v: f"{v}%"),
        color_discrete_sequence=[ACCENT, "#2ecc71", "#f39c12", "#e74c3c"],
        labels={"success_pct": "Success Rate (%)", "position": "Position", "from_label": "Origin Tier"},
    )
    fig.update_traces(textposition="outside", textfont_color=TEXT)
    fig.update_layout(
        **LAYOUT,
        title=dict(text="High-Major Success Rate by Position & Origin Tier", font=dict(size=17)),
        xaxis=dict(tickfont=dict(color=TEXT), gridcolor=GRID),
        yaxis=dict(range=[0, 110], tickfont=dict(color=TEXT), gridcolor=GRID),
        legend=dict(bgcolor=BG, bordercolor=GRID, title_text="Origin Tier"),
    )
    save(fig, "05_position_success")


# ── 6. Verdict distribution donut ────────────────────────────────────────────
def chart_verdict_donut():
    df = q("""
        SELECT transfer_verdict, COUNT(*) AS n
        FROM individual_transfer_scores
        GROUP BY transfer_verdict
        ORDER BY n DESC
    """)

    fig = go.Figure(go.Pie(
        labels=df["transfer_verdict"],
        values=df["n"],
        hole=0.55,
        marker_colors=[VERDICT_COLORS.get(v, "#888") for v in df["transfer_verdict"]],
        textfont=dict(size=14, color="white"),
        textinfo="label+percent",
    ))
    fig.update_layout(
        **LAYOUT,
        title=dict(text=f"Transfer Verdict Distribution  (n={df['n'].sum()})", font=dict(size=17)),
        showlegend=False,
        annotations=[dict(text="581<br>transfers", x=0.5, y=0.5, font_size=16,
                          showarrow=False, font_color=TEXT)],
    )
    save(fig, "06_verdict_distribution", w=600, h=500)


if __name__ == "__main__":
    print("Generating charts...")
    chart_heatmap()
    chart_origin_tier()
    chart_top_transfers()
    chart_recruit_vs_success()
    chart_position_breakdown()
    chart_verdict_donut()
    print("Done — all charts saved to assets/")
