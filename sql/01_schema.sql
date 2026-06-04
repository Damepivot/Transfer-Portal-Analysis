-- ============================================================
-- NCAA Transfer Market Analysis — Database Schema
-- File: sql/01_schema.sql
--
-- Run this first (before seeding or loading data).
-- Creates 6 tables and 1 custom enum type.
--
-- Data flow overview:
--   conferences → teams → players → transfers → player_seasons
--                      ↘                      ↗
--                        team_seasons (per team per year)
--
-- Key design decisions:
--   - Teams are stored per-season (one row per school per year).
--     This handles conference realignment (e.g. Pac-12 breakup in 2024).
--   - BPM is the only stat sourced exclusively from CBB Reference.
--     All other stats (PPG, RPG, etc.) are optional and not used in scoring.
--   - recruiting_composite stores the On3 transfer portal rating (0–100),
--     NOT the high school recruiting rank (though they are correlated).
-- ============================================================


-- conference_tier classifies programs by competitive level.
-- Used to segment the peer baseline and weight the context score.
-- Ordered loosest → strictest (sub_d1 is pre-D1, not a D1 level).
CREATE TYPE conference_tier AS ENUM (
    'high_major',      -- ACC, Big Ten, Big 12, SEC, Big East, Pac-12
    'high_mid_major',  -- AAC, Mountain West, WCC, Atlantic 10
    'mid_major',       -- MVC, MAC, CUSA, Sun Belt, CAA, Horizon, Big West, SoCon
    'low_major',       -- Big South, NEC, OVC, SWAC, MEAC, Patriot, America East, WAC, Ivy, ASUN
    'sub_d1',          -- JUCO, NCAA D2, NCAA D3, NAIA (any non-D1 domestic origin)
    'international'    -- players from foreign professional/semi-pro leagues (EuroLeague, NBL, etc.)
);


-- conferences: one row per conference, with its tier classification.
-- Seeded from data/conferences_seed.csv via sql/02_seed_conferences.sql.
-- 31 D1 conferences + 1 catch-all Sub-D1 entry.
CREATE TABLE conferences (
    conference_id  SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    abbreviation   VARCHAR(20),
    tier           conference_tier NOT NULL
);


-- teams: one row per school per season.
-- Storing per-season allows us to handle conference realignment correctly.
-- Example: BYU moved from WCC (high_mid_major) to Big 12 (high_major) in 2023-24.
--          They have a different conference_id for each season.
-- season format: '2022-23' (the year the season ENDS).
-- Sub-D1 teams (JUCO/D2/D3) use NULL for season since they aren't in our CBB CSVs.
CREATE TABLE teams (
    team_id        SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    conference_id  INT REFERENCES conferences(conference_id),
    season         VARCHAR(9)   -- '2022-23' format; NULL for sub_d1 teams
);


-- players: one row per player, with their profile at time of transfer.
-- full_name has a UNIQUE constraint so ON CONFLICT upserts work correctly
-- when the same player appears in multiple data sources (Kaggle + On3).
-- recruiting_composite: On3 transfer portal rating 0-100 (not high school rank).
--   Only top ~200 portal entrants per year get rated. NULL = unrated/unranked.
CREATE TABLE players (
    player_id             SERIAL PRIMARY KEY,
    full_name             VARCHAR(100) NOT NULL UNIQUE,
    position              VARCHAR(5),     -- G, G/F, F, F/C, C
    height_in             INT,            -- total inches (e.g. 76 = 6'4")
    weight_lbs            INT,
    birth_year            INT,
    class_year            VARCHAR(10),    -- FR, SO, JR, SR, Grad
    recruiting_composite  NUMERIC(5,2)   -- On3 transfer composite score 0–100
);


-- transfers: one row per player per move.
-- season = the season they transferred INTO (e.g. played at new school in '2023-24').
-- transfer_type: 'portal' for standard portal entries.
-- A player can appear multiple times if they transferred more than once.
CREATE TABLE transfers (
    transfer_id   SERIAL PRIMARY KEY,
    player_id     INT REFERENCES players(player_id),
    from_team_id  INT REFERENCES teams(team_id),
    to_team_id    INT REFERENCES teams(team_id),
    season        VARCHAR(9),    -- '2023-24' = they transferred INTO this season
    transfer_type VARCHAR(20)   -- 'portal', 'grad_transfer'
);


-- player_seasons: one row per player per team per season.
-- Covers BOTH the pre-transfer season (from_school) and post-transfer season (to_school).
-- BPM, OBPM, DBPM come from CBB Reference (real scraped data, zero estimation).
-- Rows are initially inserted as empty placeholders by load_real_data.py,
-- then filled in by the CBB Reference UPSERT loop.
-- Players with < 12 games are excluded during ETL (small-sample BPM is unreliable).
-- JUCO/D2/D3 players only have a post-transfer row (no pre-D1 CBB Reference data).
CREATE TABLE player_seasons (
    player_season_id  SERIAL PRIMARY KEY,
    player_id         INT REFERENCES players(player_id),
    team_id           INT REFERENCES teams(team_id),
    season            VARCHAR(9),
    games             INT,
    games_started     INT,
    mpg               NUMERIC(5,2),
    ppg               NUMERIC(5,2),
    rpg               NUMERIC(5,2),
    apg               NUMERIC(5,2),
    spg               NUMERIC(5,2),
    bpg               NUMERIC(5,2),
    fg_pct            NUMERIC(5,3),
    three_pct         NUMERIC(5,3),
    ft_pct            NUMERIC(5,3),
    ts_pct            NUMERIC(5,3),   -- True Shooting % = pts / (2 × (FGA + 0.44 × FTA))
    efg_pct           NUMERIC(5,3),
    usg_pct           NUMERIC(5,2),   -- Usage Rate: % of team possessions used while on court
    bpm               NUMERIC(5,2),   -- Box Plus/Minus: points above avg per 100 possessions
    obpm              NUMERIC(5,2),   -- Offensive BPM component
    dbpm              NUMERIC(5,2),   -- Defensive BPM component
    porpag            NUMERIC(5,2),   -- Points Over Replacement Per Adjusted Game (BartTorvik)
    stat_note         VARCHAR(30),    -- NULL = current season; '*' = prior season (injury/limited games)
    UNIQUE (player_id, team_id, season)
);


-- team_seasons: one row per team per season.
-- Stores team-level outcomes for correlation analysis (do good transfers = more wins?).
-- adj_efficiency = ADJOE − ADJDE (adjusted offensive minus defensive efficiency).
-- Source: BartTorvik/CBB Reference season summary CSVs (cbb21.csv through cbb26.csv).
CREATE TABLE team_seasons (
    team_season_id  SERIAL PRIMARY KEY,
    team_id         INT REFERENCES teams(team_id),
    season          VARCHAR(9),
    wins            INT,
    losses          INT,
    conf_wins       INT,
    conf_losses     INT,
    adj_efficiency  NUMERIC(6,2),  -- net efficiency margin (positive = good team)
    UNIQUE (team_id, season)
);
