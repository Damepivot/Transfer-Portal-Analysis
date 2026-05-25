-- ============================================================
-- MATERIALIZED VIEW: Peer expectation baseline per tier jump
-- Rebuild after each ETL run: REFRESH MATERIALIZED VIEW tier_pair_expectations;
-- ============================================================
CREATE MATERIALIZED VIEW tier_pair_expectations AS
SELECT
    c_from.tier AS from_tier,
    c_to.tier   AS to_tier,
    CASE
        WHEN p.recruiting_composite >= 90 THEN 'elite'
        WHEN p.recruiting_composite >= 80 THEN 'high'
        WHEN p.recruiting_composite >= 70 THEN 'mid'
        ELSE                                   'low'
    END                  AS recruit_tier,
    AVG(ps_after.bpm)    AS avg_bpm_after,
    STDDEV(ps_after.bpm) AS stddev_bpm,
    COUNT(*)             AS sample_size
FROM transfers t
JOIN players p ON t.player_id = p.player_id
JOIN player_seasons ps_after
    ON t.player_id = ps_after.player_id AND ps_after.season = t.season
JOIN teams t_from ON t.from_team_id = t_from.team_id
JOIN teams t_to   ON t.to_team_id   = t_to.team_id
JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
GROUP BY 1, 2, 3;


-- ============================================================
-- VIEW: Individual Transfer Performance Score
-- context_score = bpm_after × tier_weight × role_weight
--   tier_weight: higher destination = more credit for same BPM
--   role_weight: smaller role = harder to produce BPM, more credit
-- BPM = Box Plus/Minus (offensive + defensive combined)
-- ============================================================
CREATE VIEW individual_transfer_scores AS
WITH recruit_buckets AS (
    SELECT
        player_id,
        CASE
            WHEN recruiting_composite >= 90 THEN 'elite'
            WHEN recruiting_composite >= 80 THEN 'high'
            WHEN recruiting_composite >= 70 THEN 'mid'
            ELSE                                 'low'
        END AS recruit_tier
    FROM players
),
base AS (
    SELECT
        tr.transfer_id,
        p.player_id,
        p.full_name,
        p.position,
        p.height_in,
        p.weight_lbs,
        p.birth_year,
        p.recruiting_composite,
        t_from.name       AS from_school,
        c_from.tier       AS from_tier,
        t_to.name         AS to_school,
        c_to.tier         AS to_tier,
        tr.season,
        ps_before.bpm     AS bpm_before,
        ps_after.bpm      AS bpm_after,
        ps_before.usg_pct AS usage_before,
        ps_after.usg_pct  AS usage_after,
        ps_after.ts_pct   AS efficiency_after,
        ps_after.obpm     AS obpm_after,
        ps_after.dbpm     AS dbpm_after,
        ps_after.mpg      AS mpg_after,
        ps_after.ppg      AS ppg_after,
        tpe.avg_bpm_after AS projected_bpm,
        tpe.sample_size   AS peer_group_size,
        rb.recruit_tier,
        -- Tier weight: destination tier difficulty multiplier
        CASE c_to.tier
            WHEN 'high_major'     THEN 1.35
            WHEN 'high_mid_major' THEN 1.15
            WHEN 'mid_major'      THEN 0.90
            WHEN 'low_major'      THEN 0.75
            ELSE 1.0
        END AS tier_weight,
        -- Role weight: credit for maintaining BPM in smaller role
        CASE
            WHEN (ps_after.usg_pct - ps_before.usg_pct) < -5 THEN 1.20
            WHEN (ps_after.usg_pct - ps_before.usg_pct) < -2 THEN 1.10
            WHEN (ps_after.usg_pct - ps_before.usg_pct) >  5 THEN 0.85
            WHEN (ps_after.usg_pct - ps_before.usg_pct) >  2 THEN 0.95
            ELSE 1.0
        END AS role_weight
    FROM transfers tr
    JOIN players p           ON tr.player_id   = p.player_id
    JOIN recruit_buckets rb  ON p.player_id    = rb.player_id
    JOIN player_seasons ps_before
        ON tr.player_id = ps_before.player_id
        AND ps_before.season = (
            SELECT MAX(season) FROM player_seasons
            WHERE player_id = tr.player_id AND season < tr.season
        )
    JOIN player_seasons ps_after
        ON tr.player_id = ps_after.player_id AND ps_after.season = tr.season
    JOIN teams t_from ON tr.from_team_id = t_from.team_id
    JOIN teams t_to   ON tr.to_team_id   = t_to.team_id
    JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
    JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
    JOIN tier_pair_expectations tpe
        ON  c_from.tier     = tpe.from_tier
        AND c_to.tier       = tpe.to_tier
        AND rb.recruit_tier = tpe.recruit_tier
        AND tpe.sample_size >= 5
)
SELECT
    transfer_id,
    player_id,
    full_name,
    position,
    height_in,
    weight_lbs,
    birth_year,
    recruiting_composite,
    from_school,
    from_tier,
    to_school,
    to_tier,
    season,
    bpm_before,
    bpm_after,
    ROUND(bpm_after - bpm_before, 2)                           AS bpm_change,
    projected_bpm,
    ROUND(bpm_after - projected_bpm, 2)                        AS transfer_premium,
    usage_before,
    usage_after,
    ROUND(usage_after - usage_before, 1)                       AS usg_change,
    efficiency_after,
    obpm_after,
    dbpm_after,
    mpg_after,
    ppg_after,
    peer_group_size,
    ROUND((bpm_after * tier_weight * role_weight)::NUMERIC, 2) AS context_score,
    -- Verdict uses absolute bpm_after so the bar is consistent across tiers.
    -- context_score is a separate ranking metric (rewarding harder environments)
    -- but does not determine whether a transfer "succeeded" or not.
    CASE
        WHEN bpm_after >  2.0 THEN 'High Value'
        WHEN bpm_after >  0.5 THEN 'Solid Addition'
        WHEN bpm_after > -0.5 THEN 'Neutral'
        ELSE                       'Didn''t Fit'
    END AS transfer_verdict
FROM base;


-- ============================================================
-- VIEW: Team Transfer Portfolio Report
-- Shows each team's transfer class composition + outcome
-- ============================================================
CREATE VIEW team_transfer_report AS
SELECT
    t.name                                                     AS team,
    c.tier                                                     AS team_tier,
    tr.season,
    COUNT(DISTINCT tr.transfer_id)                             AS transfer_count,
    SUM(CASE WHEN c_from.tier = 'high_major'     THEN 1 ELSE 0 END) AS from_high_major,
    SUM(CASE WHEN c_from.tier = 'high_mid_major' THEN 1 ELSE 0 END) AS from_high_mid,
    SUM(CASE WHEN c_from.tier = 'mid_major'      THEN 1 ELSE 0 END) AS from_mid_major,
    SUM(CASE WHEN c_from.tier = 'low_major'      THEN 1 ELSE 0 END) AS from_low_major,
    ROUND(AVG(its.transfer_premium), 2)                        AS avg_transfer_premium,
    ROUND(AVG(ps_after.bpm), 2)                                AS avg_transfer_bpm,
    ROUND(AVG(ps_after.usg_pct), 1)                            AS avg_usage_rate,
    ts.wins,
    ts.losses,
    ts.adj_efficiency
FROM transfers tr
JOIN individual_transfer_scores its ON tr.transfer_id = its.transfer_id
JOIN teams t      ON tr.to_team_id   = t.team_id
JOIN teams t_from ON tr.from_team_id = t_from.team_id
JOIN conferences c      ON t.conference_id      = c.conference_id
JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
JOIN player_seasons ps_after
    ON tr.player_id = ps_after.player_id AND ps_after.season = tr.season
JOIN team_seasons ts ON t.team_id = ts.team_id AND tr.season = ts.season
GROUP BY t.name, c.tier, tr.season, ts.wins, ts.losses, ts.adj_efficiency;


-- ============================================================
-- VIEW: League-Level Recruitment Profile Recommendations
-- Answers: "What pre-transfer profile predicts success at each tier?"
-- Uses ALL transfers (not just successes) so success_rate is meaningful.
-- A high dest_tier correctly shows a higher required pre-transfer floor
-- because the success rate for weak-profile players is near zero there.
-- ============================================================
CREATE VIEW recruitment_profiles AS
SELECT
    c_to.tier                                                                              AS dest_tier,
    c_from.tier                                                                            AS origin_tier,
    p.position,
    COUNT(*)                                                                               AS sample_size,
    -- Success rate: what fraction of attempts from this route actually worked
    ROUND(100.0 * SUM(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN 1 ELSE 0 END)
          / COUNT(*), 1)                                                                   AS success_rate_pct,
    -- Pre-transfer profile of players who SUCCEEDED (what the successful look like)
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.bpm_before  END)::NUMERIC, 2) AS success_avg_bpm_before,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.usage_before END)::NUMERIC, 1) AS success_avg_usage_before,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.ppg_after   END)::NUMERIC, 1) AS success_avg_ppg_after,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.efficiency_after END)::NUMERIC, 3) AS success_avg_ts,
    -- Pre-transfer profile of players who FAILED (for contrast)
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('Neutral','Didn''t Fit') THEN its.bpm_before  END)::NUMERIC, 2) AS fail_avg_bpm_before,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('Neutral','Didn''t Fit') THEN its.usage_before END)::NUMERIC, 1) AS fail_avg_usage_before,
    -- Physical profile across all transfers on this route
    ROUND(AVG(p.height_in), 1)                                                             AS avg_height_in,
    ROUND(AVG(p.weight_lbs), 0)                                                            AS avg_weight_lbs,
    -- Outcome summary
    ROUND(AVG(its.context_score), 2)                                                       AS avg_context_score,
    ROUND(AVG(its.transfer_premium), 2)                                                    AS avg_transfer_premium,
    SUM(CASE WHEN its.transfer_verdict = 'High Value'     THEN 1 ELSE 0 END)               AS high_value_count,
    SUM(CASE WHEN its.transfer_verdict = 'Solid Addition' THEN 1 ELSE 0 END)               AS solid_addition_count,
    SUM(CASE WHEN its.transfer_verdict = 'Neutral'        THEN 1 ELSE 0 END)               AS neutral_count,
    SUM(CASE WHEN its.transfer_verdict = 'Didn''t Fit'   THEN 1 ELSE 0 END)               AS didnt_fit_count
FROM individual_transfer_scores its
JOIN players p    ON its.player_id    = p.player_id
JOIN transfers tr ON its.transfer_id  = tr.transfer_id
JOIN teams t_from ON tr.from_team_id  = t_from.team_id
JOIN teams t_to   ON tr.to_team_id    = t_to.team_id
JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
GROUP BY 1, 2, 3
HAVING COUNT(*) >= 5
ORDER BY 1, 2, 3;


-- ============================================================
-- VIEW: League/Tier Transfer Trends
-- "How does each tier perform with transfers overall?"
-- Answers: what profile and position tends to work per tier,
-- expected context score, and role/size patterns
-- ============================================================
CREATE VIEW league_transfer_trends AS
SELECT
    c_to.tier                                                   AS dest_tier,
    c_from.tier                                                 AS origin_tier,
    its.position,
    its.season,
    COUNT(*)                                                    AS transfer_count,
    ROUND(AVG(its.context_score),    2)                        AS avg_context_score,
    ROUND(AVG(its.transfer_premium), 2)                        AS avg_transfer_premium,
    ROUND(AVG(its.bpm_change),       2)                        AS avg_bpm_change,
    ROUND(AVG(its.usg_change),       1)                        AS avg_usg_change,
    ROUND(AVG(p.height_in),          1)                        AS avg_height_in,
    ROUND(AVG(p.weight_lbs),         0)                        AS avg_weight_lbs,
    SUM(CASE WHEN its.transfer_verdict = 'High Value'      THEN 1 ELSE 0 END) AS high_value,
    SUM(CASE WHEN its.transfer_verdict = 'Solid Addition'  THEN 1 ELSE 0 END) AS solid_addition,
    SUM(CASE WHEN its.transfer_verdict = 'Neutral'         THEN 1 ELSE 0 END) AS neutral,
    SUM(CASE WHEN its.transfer_verdict = 'Didn''t Fit'     THEN 1 ELSE 0 END) AS didnt_fit,
    ROUND(
        100.0 * SUM(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN 1 ELSE 0 END)
        / COUNT(*), 1
    )                                                           AS success_rate_pct
FROM individual_transfer_scores its
JOIN players p    ON its.player_id   = p.player_id
JOIN transfers tr ON its.transfer_id = tr.transfer_id
JOIN teams t_from ON tr.from_team_id = t_from.team_id
JOIN teams t_to   ON tr.to_team_id   = t_to.team_id
JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4;
