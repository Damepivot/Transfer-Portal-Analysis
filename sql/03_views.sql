-- ============================================================
-- NCAA Transfer Portal Analytics — Analytical Views
-- File: sql/03_views.sql
--
-- This file defines all views used by the dashboard.
-- Run order: after 01_schema.sql and 02_seed_conferences.sql.
-- Safe to re-run: drops all dependents first, then recreates.
--
-- Views defined here:
--   0. skill_index_pop_stats                  — population mean/SD for z-scoring
--   1. tier_pair_expectations  (MATERIALIZED) — peer skill_index baseline by route
--   2. individual_transfer_scores             — main scoring view per player
--   3. team_transfer_report                   — per-team transfer class outcomes
--   4. recruitment_profiles                   — what profile succeeds by route
--   5. league_transfer_trends                 — season-by-season trends by tier pair
--
-- Everything downstream of individual_transfer_scores is denominated in
-- skill_index, not raw BPM. BPM still exists as a stored column (it's one
-- of the three inputs skill_index blends, and the ETL/UI may reference it
-- internally), but it is not the metric the site explains success through —
-- that's skill_index everywhere a number is shown to a user.
-- ============================================================

DROP VIEW IF EXISTS league_transfer_trends     CASCADE;
DROP VIEW IF EXISTS recruitment_profiles       CASCADE;
DROP VIEW IF EXISTS team_transfer_report       CASCADE;
DROP VIEW IF EXISTS individual_transfer_scores CASCADE;
DROP MATERIALIZED VIEW IF EXISTS tier_pair_fallback     CASCADE;
DROP MATERIALIZED VIEW IF EXISTS tier_pair_expectations CASCADE;
DROP VIEW IF EXISTS skill_index_pop_stats CASCADE;


-- ============================================================
-- 0. VIEW: skill_index_pop_stats
--
-- Purpose: One shared source of truth for the population mean/SD used to
-- z-score BPM, usage, and TS% into skill_index. Every place that computes
-- a skill_index (individual_transfer_scores, the peer-baseline materialized
-- views below, and the Python app for a user-entered profile in Player Fit
-- Finder) reads from here, so the formula can't drift between call sites.
-- ============================================================
CREATE VIEW skill_index_pop_stats AS
SELECT
    AVG(bpm)::numeric     AS mean_bpm,  STDDEV(bpm)::numeric     AS sd_bpm,
    AVG(usg_pct)::numeric AS mean_usg,  STDDEV(usg_pct)::numeric AS sd_usg,
    AVG(ts_pct)::numeric  AS mean_ts,   STDDEV(ts_pct)::numeric  AS sd_ts
FROM player_seasons
WHERE bpm IS NOT NULL;


-- ============================================================
-- 1. MATERIALIZED VIEW: tier_pair_expectations
--
-- Purpose: Establish a "peer baseline" — what skill_index we'd expect
-- from a player making a given tier jump with a given recruiting
-- profile, based on NIL-era transfers only (2022-23 onward).
--
-- WHY NIL-ERA ONLY: The 2022-23 season was the first full season
-- under the unlimited transfer rule. Pre-NIL portal dynamics
-- (2021-22 and earlier) reflect a different competitive environment
-- with different player selection and motivation patterns. Pooling
-- all seasons creates a baseline that underestimates how hard it
-- is to succeed in today's higher-competition portal market.
--
-- This answers: "For a high recruit going from a high-major to
-- another high-major in the NIL era, what's the average skill_index
-- outcome?" If the peer group averages 0.3 and a specific player
-- posts 1.8, their transfer_premium = +1.5.
--
-- Dimensions:
--   from_tier × to_tier × recruit_tier → avg_skill_index_after, sample_size
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
    END AS recruit_tier,

    -- Same 50/25/25 BPM/usage/TS% blend as individual_transfer_scores,
    -- computed inline (can't join individual_transfer_scores here — it
    -- depends on this view for its peer baseline, so that would be circular).
    AVG(
        0.50 * ((ps_after.bpm - ps.mean_bpm) / ps.sd_bpm)
        + 0.25 * COALESCE((ps_after.usg_pct - ps.mean_usg) / ps.sd_usg, 0)
        + 0.25 * COALESCE((ps_after.ts_pct  - ps.mean_ts)  / ps.sd_ts,  0)
    )    AS avg_skill_index_after,
    STDDEV(
        0.50 * ((ps_after.bpm - ps.mean_bpm) / ps.sd_bpm)
        + 0.25 * COALESCE((ps_after.usg_pct - ps.mean_usg) / ps.sd_usg, 0)
        + 0.25 * COALESCE((ps_after.ts_pct  - ps.mean_ts)  / ps.sd_ts,  0)
    )    AS stddev_skill_index,
    COUNT(*) AS sample_size       -- used to filter out thin peer groups (< 5)
FROM transfers t
CROSS JOIN skill_index_pop_stats ps
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
  AND ps_after.bpm IS NOT NULL
GROUP BY 1, 2, 3;


-- ============================================================
-- 1b. MATERIALIZED VIEW: tier_pair_fallback
--
-- Purpose: Fallback peer baseline for thin routes where the specific
-- from_tier × to_tier × recruit_tier group has < 3 NIL-era samples.
-- Pools all recruit tiers (elite/high/mid/low) into one baseline per route.
-- This prevents NULL projected_skill_index on mid/low major routes that
-- lack enough recruit-specific data — better a noisy estimate than none.
-- ============================================================
CREATE MATERIALIZED VIEW tier_pair_fallback AS
SELECT
    c_from.tier AS from_tier,
    c_to.tier   AS to_tier,
    AVG(
        0.50 * ((ps_after.bpm - ps.mean_bpm) / ps.sd_bpm)
        + 0.25 * COALESCE((ps_after.usg_pct - ps.mean_usg) / ps.sd_usg, 0)
        + 0.25 * COALESCE((ps_after.ts_pct  - ps.mean_ts)  / ps.sd_ts,  0)
    )        AS avg_skill_index_after,
    COUNT(*) AS sample_size
FROM transfers t
CROSS JOIN skill_index_pop_stats ps
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
  AND ps_after.bpm IS NOT NULL
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
-- Key metrics (all denominated in skill_index, not raw BPM):
--   skill_index_before/after = 50% BPM + 25% usage + 25% TS%, each
--     z-scored against the full player_seasons population (see
--     skill_index_pop_stats). This is the metric the site explains
--     "did this transfer work" through — not raw BPM.
--
--   transfer_premium = skill_index_after − projected_skill_index
--     → How far the player beat (or fell short of) the peer
--       baseline for their specific tier jump + recruit tier.
--       NULL for sub_d1 transfers (no peer group yet).
--
--   skill_index_change = skill_index_after − skill_index_before
--     → Direct before/after comparison. NULL for sub_d1 (no prior D1 stats).
--
-- Verdict thresholds (based on skill_index_after, re-centered on its own
-- actual population mean/SD via idx_stats, see Step 5 below):
--   Exceeded Expectations — index > +1.00 SD AND transfer_premium > +1.00 SD
--                            (premium's own mean/SD, see premium_stats)
--   High Value            — index > +1.00 SD
--   Solid Addition        — index > +0.25 SD
--   Neutral                — index > -0.50 SD
--   Didn't Fit             — index ≤ -0.50 SD
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
        ps_before.ts_pct  AS efficiency_before,
        ps_before.games   AS games_before,
        ps_before.mpg     AS mpg_before,

        -- Competition-adjusted bpm_before (feeds skill_index_before below).
        -- Raw BPM is biased by origin team strength: a high-major player posting 0.0 BPM
        -- is likely better than a low-major player posting 0.0, because they were playing
        -- against harder competition. Adjust by origin team's net efficiency margin:
        --   adj = bpm_before + origin_adj_efficiency × 0.05
        -- Anchored at 0 (true D1 median adj_eff ≈ 0). Every +10 adj_eff = +0.5 BPM credit.
        -- NULL when bpm_before or origin adj_efficiency is unavailable (sub_d1/international).
        CASE
            WHEN ps_before.bpm IS NULL THEN NULL
            ELSE ROUND((ps_before.bpm + COALESCE(ts_from.adj_efficiency, 0) * 0.05)::NUMERIC, 2)
        END AS bpm_before_adj,

        -- Origin team strength, kept for internal reference
        ts_from.adj_efficiency AS origin_adj_efficiency,

        -- Confidence weight for bpm_before: reliability scales with games played.
        -- 30 games (full season) = 1.0 confidence. 12 games (ETL minimum) ≈ 0.40.
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

        -- Peer baseline: two-tier lookup, now in skill_index terms.
        -- Level 1 (specific): from_tier × to_tier × recruit_tier, NIL-era, ≥ 3 samples.
        -- Level 2 (fallback): from_tier × to_tier pooled, NIL-era, ≥ 5 samples.
        COALESCE(tpe.avg_skill_index_after, tpf.avg_skill_index_after) AS projected_skill_index,
        COALESCE(tpe.sample_size,            tpf.sample_size)          AS peer_group_size,

        rb.recruit_tier,

        -- skill_index: 50% BPM / 25% usage / 25% true-shooting, each z-scored
        -- against the full player_seasons population so the three terms sit
        -- on one comparable scale. BPM-before uses bpm_before_adj (already
        -- competition-adjusted) so before/after are scored the same way.
        -- A single missing usage/TS term defaults to 0 (average) rather than
        -- nulling out the whole index.
        CASE WHEN ps_before.bpm IS NULL THEN NULL ELSE
            0.50 * ((ROUND((ps_before.bpm + COALESCE(ts_from.adj_efficiency, 0) * 0.05)::NUMERIC, 2) - ps.mean_bpm) / ps.sd_bpm)
            + 0.25 * COALESCE((ps_before.usg_pct - ps.mean_usg) / ps.sd_usg, 0)
            + 0.25 * COALESCE((ps_before.ts_pct  - ps.mean_ts)  / ps.sd_ts,  0)
        END AS skill_index_before,

        0.50 * ((ps_after.bpm - ps.mean_bpm) / ps.sd_bpm)
        + 0.25 * COALESCE((ps_after.usg_pct - ps.mean_usg) / ps.sd_usg, 0)
        + 0.25 * COALESCE((ps_after.ts_pct  - ps.mean_ts)  / ps.sd_ts,  0)
            AS skill_index_after

    FROM transfers tr
    CROSS JOIN skill_index_pop_stats ps
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
),

-- Step 3: Compute all derived metrics from the assembled base data.
-- skill_index_after is a blend of three z-scores (BPM/usage/TS%); because
-- the inputs are correlated, the blend's own spread isn't SD=1 like a single
-- z-score would be. idx_stats below measures the blend's *actual* mean/SD so
-- verdict thresholds are calibrated to the real distribution, not assumed.
scored AS (
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

    ROUND(skill_index_before, 2) AS skill_index_before,
    ROUND(skill_index_after,  2) AS skill_index_after,

    -- Direct "success before vs. success after" comparison on one scale.
    CASE WHEN skill_index_before IS NOT NULL
         THEN ROUND(skill_index_after - skill_index_before, 2) END AS skill_index_change,

    -- BPM improvement, adjusted for origin competition level. Kept as an
    -- internal/supporting figure — skill_index_change above is the metric
    -- the site explains "improvement" through.
    CASE WHEN bpm_before_adj IS NOT NULL THEN ROUND(bpm_after - bpm_before_adj, 2) END AS bpm_change,

    projected_skill_index,

    -- How far the player beat the NIL-era peer group for their route + recruit tier,
    -- in skill_index units. Positive = outperformed expectations.
    -- NULL if no NIL-era peer group has ≥ 5 samples (thin route or pre-NIL transfer).
    CASE WHEN projected_skill_index IS NOT NULL
         THEN ROUND(skill_index_after - projected_skill_index, 2) END AS transfer_premium,

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
    -- NULL for sub_d1/international (no pre-D1 data). 1.0 = full season of data.
    bpm_confidence

    FROM base
),

idx_stats AS (
    SELECT AVG(skill_index_after) AS mean_idx, STDDEV(skill_index_after) AS sd_idx
    FROM scored
),

-- premium_stats: same self-calibration pattern as idx_stats, applied to
-- transfer_premium so "Exceeded Expectations" means "beat the peer baseline
-- by a lot" relative to transfer_premium's own actual distribution, not a
-- hardcoded BPM-era number that no longer matches skill_index's scale.
premium_stats AS (
    SELECT AVG(transfer_premium) AS mean_premium, STDDEV(transfer_premium) AS sd_premium
    FROM scored
    WHERE transfer_premium IS NOT NULL
)

-- Step 4: Apply the verdict using skill_index_after's *actual* distribution
-- (idx_stats) — a player posting below-average BPM in an expanded role with
-- solid efficiency shouldn't grade out the same as one who was just
-- unproductive. Thresholds are in standard-deviation units off the real
-- population mean, so "Solid Addition" means "performed above average," not
-- an arbitrary cutoff — and it self-recalibrates as more seasons get
-- scraped in, instead of drifting stale like hardcoded thresholds would.
-- "Exceeded Expectations" also requires beating the peer baseline by a lot
-- (premium_stats) — sub_d1 transfers (no peer baseline) can reach at most
-- "High Value."
SELECT
    s.*,
    CASE
        WHEN (s.skill_index_after - i.mean_idx) / i.sd_idx >  1.00
         AND s.transfer_premium IS NOT NULL
         AND (s.transfer_premium - pr.mean_premium) / pr.sd_premium > 1.00
                                                            THEN 'Exceeded Expectations'
        WHEN (s.skill_index_after - i.mean_idx) / i.sd_idx >  1.00 THEN 'High Value'
        WHEN (s.skill_index_after - i.mean_idx) / i.sd_idx >  0.25 THEN 'Solid Addition'
        WHEN (s.skill_index_after - i.mean_idx) / i.sd_idx > -0.50 THEN 'Neutral'
        ELSE                                                            'Didn''t Fit'
    END AS transfer_verdict
FROM scored s
CROSS JOIN idx_stats i
CROSS JOIN premium_stats pr;


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
    ROUND(AVG(its.transfer_premium)::numeric, 2)    AS avg_transfer_premium,
    ROUND(AVG(its.skill_index_after)::numeric, 2)   AS avg_transfer_skill_index,
    ROUND(AVG(its.usage_after)::numeric, 1)         AS avg_usage_rate,

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
-- "Success" here = High Value or Solid Addition (skill_index_after > +0.25 SD,
-- see individual_transfer_scores). Exceeded Expectations is a subset of
-- High Value, not a separate outcome.
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
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.skill_index_before END)::NUMERIC, 2) AS success_avg_skill_index_before,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.usage_before END)::NUMERIC, 1) AS success_avg_usage_before,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.ppg_after   END)::NUMERIC, 1) AS success_avg_ppg_after,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('High Value','Solid Addition') THEN its.efficiency_after END)::NUMERIC, 3) AS success_avg_ts,

    -- Pre-transfer profile of players who FAILED (what to avoid)
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('Neutral','Didn''t Fit') THEN its.skill_index_before END)::NUMERIC, 2) AS fail_avg_skill_index_before,
    ROUND(AVG(CASE WHEN its.transfer_verdict IN ('Neutral','Didn''t Fit') THEN its.usage_before END)::NUMERIC, 1) AS fail_avg_usage_before,

    -- Physical profile (height/weight) averaged across all transfers on this route
    ROUND(AVG(p.height_in), 1)   AS avg_height_in,
    ROUND(AVG(p.weight_lbs), 0)  AS avg_weight_lbs,

    -- Summary outcome metric
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
    ROUND(AVG(its.transfer_premium),     2) AS avg_transfer_premium,
    ROUND(AVG(its.skill_index_change),   2) AS avg_skill_index_change,
    ROUND(AVG(its.usg_change),           1) AS avg_usg_change,

    -- Physical profile of players making this type of move
    ROUND(AVG(p.height_in),  1) AS avg_height_in,
    ROUND(AVG(p.weight_lbs), 0) AS avg_weight_lbs,

    -- Verdict counts
    SUM(CASE WHEN its.transfer_verdict = 'High Value'      THEN 1 ELSE 0 END) AS high_value,
    SUM(CASE WHEN its.transfer_verdict = 'Solid Addition'  THEN 1 ELSE 0 END) AS solid_addition,
    SUM(CASE WHEN its.transfer_verdict = 'Neutral'         THEN 1 ELSE 0 END) AS neutral,
    SUM(CASE WHEN its.transfer_verdict = 'Didn''t Fit'     THEN 1 ELSE 0 END) AS didnt_fit,

    -- Combined success rate: High Value + Solid Addition (skill_index_after > +0.25 SD)
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
