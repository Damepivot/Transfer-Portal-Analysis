-- NCAA Transfer Market Analysis
-- PostgreSQL Schema — Run this first

CREATE TYPE conference_tier AS ENUM ('high_major', 'high_mid_major', 'mid_major', 'low_major');

CREATE TABLE conferences (
    conference_id  SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    abbreviation   VARCHAR(20),
    tier           conference_tier NOT NULL
);

CREATE TABLE teams (
    team_id        SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    conference_id  INT REFERENCES conferences(conference_id),
    season         VARCHAR(9)   -- e.g. '2022-23', handles realignment per season
);

CREATE TABLE players (
    player_id             SERIAL PRIMARY KEY,
    full_name             VARCHAR(100) NOT NULL UNIQUE,
    position              VARCHAR(5),         -- G, G/F, F, F/C, C
    height_in             INT,
    class_year            VARCHAR(10),        -- FR, SO, JR, SR, Grad
    recruiting_composite  NUMERIC(5,2)        -- On3/247Sports composite score 0–100
);

CREATE TABLE transfers (
    transfer_id   SERIAL PRIMARY KEY,
    player_id     INT REFERENCES players(player_id),
    from_team_id  INT REFERENCES teams(team_id),
    to_team_id    INT REFERENCES teams(team_id),
    season        VARCHAR(9),   -- season they transferred INTO (e.g. '2022-23')
    transfer_type VARCHAR(20)   -- 'portal', 'grad_transfer'
);

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
    ts_pct            NUMERIC(5,3),   -- true shooting %
    efg_pct           NUMERIC(5,3),
    usg_pct           NUMERIC(5,2),   -- usage rate
    bpm               NUMERIC(5,2),   -- box plus/minus (from CBB Reference)
    porpag            NUMERIC(5,2),   -- points over replacement (from BartTorvik)
    UNIQUE (player_id, team_id, season)
);

CREATE TABLE team_seasons (
    team_season_id  SERIAL PRIMARY KEY,
    team_id         INT REFERENCES teams(team_id),
    season          VARCHAR(9),
    wins            INT,
    losses          INT,
    conf_wins       INT,
    conf_losses     INT,
    adj_efficiency  NUMERIC(6,2),  -- BartTorvik adjusted net efficiency
    UNIQUE (team_id, season)
);
