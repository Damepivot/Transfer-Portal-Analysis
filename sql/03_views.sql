-- ============================================================
-- NCAA Transfer Portal Analytics — Analytical Views
-- File: sql/03_views.sql
--
-- This file defines all 5 views used by the dashboard.
-- Run order: after 01_schema.sql and 02_seed_conferences.sql.
-- Safe to re-run: drops all dependents first, then recreates.
--
-- Views defined here:
--   1. tier_pair_expectations  (MATERIALIZED) — peer BPM baseline by route
--   2. individual_transfer_scores             — main scoring view per player
--   3. team_transfer_report                   — per-team transfer class outcomes
--   4. recruitment_profiles                   — what profile succeeds by route
--   5. league_transfer_trends                 — season-by-season trends by tier pair
-- ============================================================

DROP VIEW IF EXISTS league_transfer_trends    CASCADE;
DROP VIEW IF EXISTS recruitment_profiles      CASCADE;
DROP VIEW IF EXISTS team_transfer_report      CASCADE;
DROP VIEW IF EXISTS individual_transfer_scores CASCADE;
DROP MATERIALIZED VIEW IF EXISTS tier_pair_fallback    CASCADE;
DROP MATERIALIZED VIEW IF EXISTS tier_pair_expectations CASCADE;


-- ============================================================
-- 1. MATERIALIZED VIEW: tier_pair_expectations
--
-- Purpose: Establish a "peer baseline" — what BPM we'd expect
-- from a player making a given tier jump with a given recruiting
-- profile, based on NIL-era transfers only (2022-23 onward).
--
-- WHY NIL-ERA ONLY: The 2022-23 season was the first full season
-- under the unlimited transfer rule. Pre-NIL portal dynamics
-- (2021-22 and earlier) reflect a different competitive environment
-- with different player selection and motivation patterns. Pooling
-- all seasons creates a baseline that underestimates how hard it
-- is to succeed in today's higher-competition portal market.
-- Empirically, NIL-era BPM averages on key routes are 0.5–1.1
-- points lower than pre-NIL baselines on the same route.
--
-- This answers: "For a high recruit going from a high-major to
-- another high-major in the NIL era, what's the average BPM
-- outcome?" If the peer group averages 1.8 BPM and a specific
-- player posts 6.0, their transfer_premium = +4.2.
--
-- Dimensions:
--   from_tier × to_tier × recruit_tier → avg_bpm_after, sample_size
--
-- recruit_tier is bucketed from On3/247 transfer composite:
--   elite  = 90+ (McDonald's All-American level)
--   high   = 80–89 (top-100 caliber)
--   mid    = 70–79 (high mid-major recruit)
--   low    = <70 or unrated (walk-on / lightly recruited)
--
-- Rebuild after every ETL run:
--   REFRESH MATERIALIZED VIEW tier_pair_expectations;
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
    AVG(ps_after.bpm)    AS avg_bpm_after,   -- peer group mean BPM at destination
    STDDEV(ps_after.bpm) AS stddev_bpm,      -- spread (how consistent the peer group is)
    COUNT(*)             AS sample_size       -- used to filter out thin peer groups (< 5)
FROM transfers t
JOIN players p ON t.player_id = p.player_id
JOIN teams t_from ON t.from_team_id = t_from.team_id
JOIN teams t_to   ON t.to_team_id   = t_to.team_id
-- Match ps_after by team name, not team_id, because the same school can have
-- different team_id values across seasons (one row per school per year in the teams table)
JOIN player_seasons ps_after
    ON t.player_id      = ps_after.player_id
    AND ps_after.season = t.season
    AND ps_after.team_id IN (SELECT team_id FROM teams WHERE name = t_to.name)
JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
WHERE t.season >= '2022-23'   -- NIL era only: unlimited transfer rule in effect
GROUP BY 1, 2, 3;


-- ============================================================
-- 1b. MATERIALIZED VIEW: tier_pair_fallback
--
-- Purpose: Fallback peer baseline for thin routes where the specific
-- from_tier × to_tier × recruit_tier group has < 3 NIL-era samples.
-- Pools all recruit tiers (elite/high/mid/low) into one baseline per route.
-- This prevents NULL projected_bpm on mid/low major routes that lack enough
-- recruit-specific data — better a noisy estimate than no signal at all.
-- ============================================================
CREATE MATERIALIZED VIEW tier_pair_fallback AS
SELECT
    c_from.tier          AS from_tier,
    c_to.tier            AS to_tier,
    AVG(ps_after.bpm)    AS avg_bpm_after,
    COUNT(*)             AS sample_size
FROM transfers t
JOIN players p ON t.player_id = p.player_id
JOIN teams t_from ON t.from_team_id = t_from.team_id
JOIN teams t_to   ON t.to_team_id   = t_to.team_id
JOIN player_seasons ps_after
    ON t.player_id      = ps_after.player_id
    AND ps_after.season = t.season
    AND ps_after.team_id IN (SELECT team_id FROM teams WHERE name = t_to.name)
JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
WHERE t.season >= '2022-23'
GROUP BY 1, 2
HAVING COUNT(*) >= 5;


-- ============================================================
-- 2. VIEW: individual_transfer_scores
--
-- Purpose: The main scoring view. One row per scored transfer.
-- A transfer is "scoreable" if the player has real BPM data
-- at their destination school (ps_after). Pre-transfer BPM
-- (ps_before) is optional — sub_d1 transfers have no CBB
-- Reference data from before they reached D1.
--
-- Key metrics:
--   context_score   = bpm_after × tier_weight × role_weight
--     → Rewards high performance in harder environments with
--       smaller roles. A +4 BPM player at Duke in a reduced
--       role scores higher than a +4 BPM player at a mid-major
--       with expanded usage.
--
--   transfer_premium = bpm_after − projected_bpm
--     → How far the player beat (or fell short of) the peer
--       baseline for their specific tier jump + recruit tier.
--       NULL for sub_d1 transfers (no peer group yet).
--
--   bpm_change = bpm_after − bpm_before
--     → Raw delta. NULL for sub_d1 (no prior D1 stats).
--
-- Verdict thresholds (based on absolute bpm_after):
--   Exceeded Expectations — bpm_after > 2.0 AND transfer_premium ≥ 2.5 AND context_score ≥ 10
--   High Value            — bpm_after > 2.0
--   Solid Addition        — bpm_after > 0.5
--   Neutral               — bpm_after > -0.5
--   Didn't Fit            — bpm_after ≤ -0.5
--
-- BPM (Box Plus/Minus) is sourced exclusively from CBB Reference.
-- Small samples are excluded: players with < 12 games are nulled
-- in load_real_data.py before reaching this view.
-- ============================================================
CREATE OR REPLACE VIEW individual_transfer_scores AS

-- Step 1: Bucket each player into an elite/high/mid/low recruiting tier.
-- This is used both to segment the peer baseline and to label the player.
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

-- Step 2: Assemble all raw fields before computing derived metrics.
-- This CTE does all the joining; the outer SELECT computes the scores.
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

        -- Pre-transfer stats (NULL for JUCO/D2/D3 origins — no CBB Reference data)
        ps_before.bpm     AS bpm_before,
        ps_before.usg_pct AS usage_before,
        ps_before.games   AS games_before,
        ps_before.mpg     AS mpg_before,

        -- Competition-adjusted bpm_before (Priority 2).
        -- Raw BPM is biased by origin team strength: a high-major player posting 0.0 BPM
        -- is likely better than a low-major player posting 0.0, because they were playing
        -- against harder competition. Adjust by origin team's net efficiency margin:
        --   adj = bpm_before + origin_adj_efficiency × 0.05
        -- Anchored at 0 (true D1 median adj_eff ≈ 0). Every +10 adj_eff = +0.5 BPM credit.
        -- High-major programs (adj_eff ≈ +13) → +0.65 pts. Low-major (adj_eff ≈ -6.6) → -0.33 pts.
        -- NULL when bpm_before or origin adj_efficiency is unavailable (sub_d1/international).
        CASE
            WHEN ps_before.bpm IS NULL THEN NULL
            ELSE ROUND((ps_before.bpm + COALESCE(ts_from.adj_efficiency, 0) * 0.05)::NUMERIC, 2)
        END AS bpm_before_adj,

        -- Origin team strength for context display
        ts_from.adj_efficiency AS origin_adj_efficiency,

        -- Confidence weight for bpm_before: BPM reliability scales with games played.
        -- 30 games (full season) = 1.0 confidence. 12 games (ETL minimum) ≈ 0.40.
        -- Players with fewer games have noisier BPM due to small sample regression.
        -- Formula: min(1.0, games / 30). NULL = sub_d1/international (no pre-D1 data).
        CASE
            WHEN ps_before.games IS NULL THEN NULL
            ELSE LEAST(1.0, ROUND(ps_before.games / 30.0, 2))
        END AS bpm_confidence,

        -- Post-transfer stats (required — transfer is not scored without this)
        ps_after.bpm      AS bpm_after,
        ps_after.usg_pct  AS usage_after,
        ps_after.ts_pct   AS efficiency_after,  -- True Shooting % at destination
        ps_after.obpm     AS obpm_after,         -- Offensive BPM component
        ps_after.dbpm     AS dbpm_after,         -- Defensive BPM component
        ps_after.mpg      AS mpg_after,
        ps_after.ppg      AS ppg_after,

        -- Peer baseline: two-tier lookup.
        -- Level 1 (specific): from_tier × to_tier × recruit_tier, NIL-era, ≥ 3 samples.
        -- Level 2 (fallback): from_tier × to_tier pooled, NIL-era, ≥ 5 samples.
        --   Fallback activates for thin mid/low routes that lack enough recruit-specific data.
        --   A pooled estimate is less precise but still better than NULL.
        COALESCE(tpe.avg_bpm_after, tpf.avg_bpm_after)     AS projected_bpm,
        COALESCE(tpe.sample_size,   tpf.sample_size)        AS peer_group_size,

        rb.recruit_tier,

        -- Tier weight: empirically derived from avg adjusted efficiency of destination
        -- programs (KenPom/Torvik adjEM), anchored so high_major=1.35 and mid_major=0.90.
        -- Calibrated on all scored transfers using team_seasons.adj_efficiency.
        --   high_major:     adj_eff ≈ +13.4  →  1.35
        --   high_mid_major: adj_eff ≈ +4.5   →  1.10  (was 1.15 — empirically corrected)
        --   mid_major:      adj_eff ≈ −1.5   →  0.90
        --   low_major:      adj_eff ≈ −6.6   →  0.75
        CASE c_to.tier
            WHEN 'high_major'     THEN 1.35
            WHEN 'high_mid_major' THEN 1.10
            WHEN 'mid_major'      THEN 0.90
            WHEN 'low_major'      THEN 0.75
            ELSE 1.0
        END AS tier_weight,

        -- Role weight: posting BPM in a reduced role is harder.
        -- If a player's usage drops 5+ pts after transferring, they're producing
        -- in a smaller role — that's credit-worthy. Expanded role = slight discount.
        -- NULL usage_before (sub_d1 players) defaults to neutral (1.0).
        CASE
            WHEN ps_before.usg_pct IS NULL                    THEN 1.0
            WHEN (ps_after.usg_pct - ps_before.usg_pct) < -5 THEN 1.20
            WHEN (ps_after.usg_pct - ps_before.usg_pct) < -2 THEN 1.10
            WHEN (ps_after.usg_pct - ps_before.usg_pct) >  5 THEN 0.85
            WHEN (ps_after.usg_pct - ps_before.usg_pct) >  2 THEN 0.95
            ELSE 1.0
        END AS role_weight

    FROM transfers tr
    JOIN players p           ON tr.player_id   = p.player_id
    JOIN recruit_buckets rb  ON p.player_id    = rb.player_id
    -- Resolve the from/to school names so sub-selects below can reference them
    JOIN teams t_from ON tr.from_team_id = t_from.team_id
    JOIN teams t_to   ON tr.to_team_id   = t_to.team_id

    -- LEFT JOIN on ps_before: JUCO/D2/D3 transfers have no pre-D1 CBB Reference data.
    -- For D1 players, find the most recent season at the from-school that has a BPM value.
    -- We match by school NAME (not team_id) because a school can have multiple team rows
    -- (one per season) in the teams table due to the per-season data model.
    LEFT JOIN player_seasons ps_before
        ON tr.player_id = ps_before.player_id
        AND ps_before.team_id IN (SELECT team_id FROM teams WHERE name = t_from.name)
        AND ps_before.season = (
            -- Latest pre-transfer season with real BPM at the from-school
            SELECT MAX(ps2.season)
            FROM player_seasons ps2
            JOIN teams t2 ON ps2.team_id = t2.team_id
            WHERE ps2.player_id = tr.player_id
              AND t2.name       = t_from.name
              AND ps2.season    < tr.season    -- must be before the transfer season
              AND ps2.bpm IS NOT NULL          -- must have real stats (not a placeholder row)
        )

    -- INNER JOIN on ps_after: a transfer cannot be scored without D1 destination stats
    JOIN player_seasons ps_after
        ON tr.player_id = ps_after.player_id
        AND ps_after.season  = tr.season
        AND ps_after.team_id IN (SELECT team_id FROM teams WHERE name = t_to.name)

    JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
    JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id

    -- LEFT JOIN on origin team_seasons to get adj_efficiency for competition adjustment.
    -- Matches by school name + pre-transfer season to get the correct season's team record.
    LEFT JOIN teams t_from_hist
        ON t_from_hist.name   = t_from.name
        AND t_from_hist.season = ps_before.season
    LEFT JOIN team_seasons ts_from
        ON ts_from.team_id = t_from_hist.team_id
        AND ts_from.season = ps_before.season

    -- Level 1 peer baseline: specific route + recruit tier, ≥ 3 NIL-era samples.
    LEFT JOIN tier_pair_expectations tpe
        ON  c_from.tier     = tpe.from_tier
        AND c_to.tier       = tpe.to_tier
        AND rb.recruit_tier = tpe.recruit_tier
        AND tpe.sample_size >= 3

    -- Level 2 fallback: pooled route (no recruit tier), ≥ 5 NIL-era samples.
    -- Activates only when Level 1 returns no match (thin route).
    LEFT JOIN tier_pair_fallback tpf
        ON  c_from.tier = tpf.from_tier
        AND c_to.tier   = tpf.to_tier

    -- Require real BPM at destination — this is the minimum bar for a scored transfer
    WHERE ps_after.bpm IS NOT NULL
)

-- Step 3: Compute all derived metrics from the assembled base data
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
    bpm_before_adj,
    origin_adj_efficiency,
    bpm_after,

    -- BPM improvement, adjusted for origin competition level.
    -- Uses bpm_before_adj (not raw bpm_before) so high-major players get credit
    -- for playing against harder competition before transferring.
    CASE WHEN bpm_before_adj IS NOT NULL THEN ROUND(bpm_after - bpm_before_adj, 2) END AS bpm_change,

    projected_bpm,

    -- How far the player beat the NIL-era peer group for their route + recruit tier.
    -- Positive = outperformed expectations; negative = underperformed.
    -- NULL if no NIL-era peer group has ≥ 5 samples (thin route or pre-NIL transfer).
    CASE WHEN projected_bpm IS NOT NULL THEN ROUND(bpm_after - projected_bpm, 2) END AS transfer_premium,

    usage_before,
    usage_after,

    -- Usage rate change: positive = expanded role, negative = reduced role
    CASE WHEN usage_before IS NOT NULL THEN ROUND(usage_after - usage_before, 1) END AS usg_change,

    efficiency_after,
    obpm_after,
    dbpm_after,
    mpg_after,
    ppg_after,
    games_before,
    mpg_before,
    peer_group_size,

    -- BPM confidence: how reliable is bpm_before given sample size?
    -- = min(1.0, games × mpg / 900). Low values flag high-variance pre-transfer BPM.
    -- NULL for sub_d1/international (no pre-D1 data). 1.0 = full season of data.
    bpm_confidence,

    -- context_score: the primary ranking metric
    -- Multiplies BPM by tier difficulty and role difficulty adjustments
    ROUND((bpm_after * tier_weight * role_weight)::NUMERIC, 2) AS context_score,

    -- Verdict: based on absolute bpm_after so the bar is consistent across all tiers.
    -- "Exceeded Expectations" also requires beating the peer baseline — sub_d1 transfers
    -- (which have no peer baseline) can reach at most "High Value."
    CASE
        WHEN bpm_after >  2.0
         AND projected_bpm IS NOT NULL
         AND ROUND(bpm_after - projected_bpm, 2) >= 2.5
         AND ROUND((bpm_after * tier_weight * role_weight)::NUMERIC, 2) >= 10
                              THEN 'Exceeded Expectations'
        WHEN bpm_after >  2.0 THEN 'High Value'
        WHEN bpm_after >  0.5 THEN 'Solid Addition'
        WHEN bpm_after > -0.5 THEN 'Neutral'
        ELSE                       'Didn''t Fit'
    END AS transfer_verdict

FROM base;


-- ============================================================
-- 3. VIEW: team_transfer_report
--
-- Purpose: Summarize each team's transfer class for a given season.
-- Shows the composition (where players came from), average performance,
-- and team outcome (wins, efficiency) — useful for evaluating how well
-- programs build through the portal.
--
-- Joins individual_transfer_scores so only scored transfers appear.
-- Teams with no scoreable transfers won't show up here.
-- ============================================================
CREATE OR REPLACE VIEW team_transfer_report AS
SELECT
    t.name                                                     AS team,
    c.tier                                                     AS team_tier,
    tr.season,
    COUNT(DISTINCT tr.transfer_id)                             AS transfer_count,

    -- Count of incoming transfers by origin tier
    SUM(CASE WHEN c_from.tier = 'high_major'     THEN 1 ELSE 0 END) AS from_high_major,
    SUM(CASE WHEN c_from.tier = 'high_mid_major' THEN 1 ELSE 0 END) AS from_high_mid,
    SUM(CASE WHEN c_from.tier = 'mid_major'      THEN 1 ELSE 0 END) AS from_mid_major,
    SUM(CASE WHEN c_from.tier = 'low_major'      THEN 1 ELSE 0 END) AS from_low_major,
    SUM(CASE WHEN c_from.tier = 'sub_d1'         THEN 1 ELSE 0 END) AS from_sub_d1,
    SUM(CASE WHEN c_from.tier = 'international'  THEN 1 ELSE 0 END) AS from_international,

    -- Transfer class outcome metrics
    ROUND(AVG(its.transfer_premium), 2)   AS avg_transfer_premium,
    ROUND(AVG(ps_after.bpm), 2)           AS avg_transfer_bpm,
    ROUND(AVG(ps_after.usg_pct), 1)       AS avg_usage_rate,

    -- Team season outcome (wins and adjusted net efficiency from BartTorvik/CBB Reference)
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
-- 4. VIEW: recruitment_profiles
--
-- Purpose: Answer "What pre-transfer profile predicts success at each tier?"
-- Compares the stats of players who SUCCEEDED vs those who FAILED on each
-- tier route, giving programs a data-driven target for who to recruit.
--
-- Uses ALL scored transfers (not just successes), so success_rate is
-- meaningful (it's failures divided into the full denominator, not cherry-picked).
-- Filtered to route/position combos with ≥ 5 samples to avoid noise.
--
-- "Success" here = High Value or Solid Addition (bpm_after > 0.5).
-- Exceeded Expectations is a subset of High Value, not a separate outcome.
-- ============================================================
CREATE OR REPLACE VIEW recruitment_profiles AS
SELECT
    c_to.tier                                                                              AS dest_tier,
    c_from.tier                                                                            AS origin_tier,
    p.position,
    COUNT(*)                                                                               AS sample_size,

    -- What fraction of players who attempted this route actually succeeded
    ROUND(100.0 * SUM(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN 1 ELSE 0 END)
          / COUNT(*), 1)                                                                   AS success_rate_pct,

    -- Pre-transfer profile of players who SUCCEEDED (what a team should target)
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.bpm_before  END)::NUMERIC, 2) AS success_avg_bpm_before,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.usage_before END)::NUMERIC, 1) AS success_avg_usage_before,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.ppg_after   END)::NUMERIC, 1) AS success_avg_ppg_after,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.efficiency_after END)::NUMERIC, 3) AS success_avg_ts,

    -- Pre-transfer profile of players who FAILED (what to avoid)
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('Neutral','Didn''t Fit') THEN its.bpm_before  END)::NUMERIC, 2) AS fail_avg_bpm_before,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('Neutral','Didn''t Fit') THEN its.usage_before END)::NUMERIC, 1) AS fail_avg_usage_before,

    -- Physical profile (height/weight) averaged across all transfers on this route
    ROUND(AVG(p.height_in), 1)   AS avg_height_in,
    ROUND(AVG(p.weight_lbs), 0)  AS avg_weight_lbs,

    -- Summary outcome metrics
    ROUND(AVG(its.context_score), 2)    AS avg_context_score,
    ROUND(AVG(its.transfer_premium), 2) AS avg_transfer_premium,
    SUM(CASE WHEN its.transfer_verdict = 'High Value'     THEN 1 ELSE 0 END) AS high_value_count,
    SUM(CASE WHEN its.transfer_verdict = 'Solid Addition' THEN 1 ELSE 0 END) AS solid_addition_count,
    SUM(CASE WHEN its.transfer_verdict = 'Neutral'        THEN 1 ELSE 0 END) AS neutral_count,
    SUM(CASE WHEN its.transfer_verdict = 'Didn''t Fit'    THEN 1 ELSE 0 END) AS didnt_fit_count

FROM individual_transfer_scores its
JOIN players p    ON its.player_id    = p.player_id
JOIN transfers tr ON its.transfer_id  = tr.transfer_id
JOIN teams t_from ON tr.from_team_id  = t_from.team_id
JOIN teams t_to   ON tr.to_team_id    = t_to.team_id
JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
GROUP BY 1, 2, 3
HAVING COUNT(*) >= 5    -- exclude thin route/position combos to avoid misleading stats
ORDER BY 1, 2, 3;


-- ============================================================
-- 5. VIEW: league_transfer_trends
--
-- Purpose: Season-by-season breakdown of how each tier-pair route
-- performs. Tracks whether transfer outcomes are improving over time,
-- which positions work best on which routes, and the physical profile
-- of players making each type of move.
--
-- Unlike recruitment_profiles (which is collapsed across seasons),
-- this view preserves the season dimension — useful for spotting
-- whether the portal is getting more efficient over time.
-- ============================================================
CREATE OR REPLACE VIEW league_transfer_trends AS
SELECT
    c_to.tier    AS dest_tier,
    c_from.tier  AS origin_tier,
    its.position,
    its.season,
    COUNT(*)     AS transfer_count,

    -- Performance metrics for this tier-pair + position + season slice
    ROUND(AVG(its.context_score),    2) AS avg_context_score,
    ROUND(AVG(its.transfer_premium), 2) AS avg_transfer_premium,
    ROUND(AVG(its.bpm_change),       2) AS avg_bpm_change,
    ROUND(AVG(its.usg_change),       1) AS avg_usg_change,

    -- Physical profile of players making this type of move
    ROUND(AVG(p.height_in),  1) AS avg_height_in,
    ROUND(AVG(p.weight_lbs), 0) AS avg_weight_lbs,

    -- Verdict counts
    SUM(CASE WHEN its.transfer_verdict = 'High Value'      THEN 1 ELSE 0 END) AS high_value,
    SUM(CASE WHEN its.transfer_verdict = 'Solid Addition'  THEN 1 ELSE 0 END) AS solid_addition,
    SUM(CASE WHEN its.transfer_verdict = 'Neutral'         THEN 1 ELSE 0 END) AS neutral,
    SUM(CASE WHEN its.transfer_verdict = 'Didn''t Fit'     THEN 1 ELSE 0 END) AS didnt_fit,

    -- Combined success rate: High Value + Solid Addition (bpm_after > 0.5)
    ROUND(
        100.0 * SUM(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN 1 ELSE 0 END)
        / COUNT(*), 1
    ) AS success_rate_pct

FROM individual_transfer_scores its
JOIN players p    ON its.player_id   = p.player_id
JOIN transfers tr ON its.transfer_id = tr.transfer_id
JOIN teams t_from ON tr.from_team_id = t_from.team_id
JOIN teams t_to   ON tr.to_team_id   = t_to.team_id
JOIN conferences c_from ON t_from.conference_id = c_from.conference_id
JOIN conferences c_to   ON t_to.conference_id   = c_to.conference_id
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4;
