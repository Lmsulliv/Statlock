-- Schema v17: materialize the match-history payload into account_match_summaries.
--
-- Why a table: a freshly imported account shows nothing useful until the drain
-- loop fetches full match metadata, which is rate-limited to one request per 5 s
-- -- hundreds of matches take hours. But the match-history call discovery already
-- makes carries everything the Overview-style screens need per match: hero,
-- K/D/A, net worth, last hits, denies, result, duration, start time, game mode
-- (see docs/api-findings.md "Match history response shape"). Storing that payload
-- at discovery time gives a new account useful data within seconds. It is a hand-
-- built denormalized copy of data the API hands us for free -- cheap writes buy
-- instant reads.
--
-- Deliberately NO foreign key on hero_id (contrast with match_players, whose FK
-- forces the drain worker's unknown-hero placeholder machinery). Discovery runs
-- before any asset refresh could learn a brand-new hero, and a summary row must
-- never be blocked by an unknown hero -- the whole point is to show data fast.
-- The hero_id is only a display hint here; the authoritative per-player rows with
-- their FK-enforced hero still arrive later via metadata ingestion.
--
-- Conventions matched to the rest of the schema: start_time is ISO-8601 text
-- (converted from the payload's unix seconds via unix_to_iso), game_mode is the
-- mode integer stored as text (matching matches.game_mode), and every stat is
-- NULL when the payload omits it -- never a fabricated 0, which would pollute any
-- future average. `won` is derived as (player_team == match_result) per
-- api-findings ("match_result is the winning team's number, not a won-flag").
--
-- Purely additive: a CREATE TABLE, no table rebuild, no foreign_keys toggling.

CREATE TABLE account_match_summaries (
    account_id  INTEGER NOT NULL,
    match_id    INTEGER NOT NULL,
    hero_id     INTEGER,            -- no FK: a brand-new hero must never block a row
    start_time  TEXT NOT NULL,      -- ISO 8601 (converted from unix int)
    game_mode   TEXT,               -- mode int stored as text (matches.game_mode convention)
    won         INTEGER,            -- 1/0 = player_team == match_result; NULL if either missing
    kills       INTEGER,
    deaths      INTEGER,
    assists     INTEGER,
    net_worth   INTEGER,
    last_hits   INTEGER,
    denies      INTEGER,
    duration_s  INTEGER,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (account_id, match_id)
);

CREATE INDEX idx_ams_account_start ON account_match_summaries(account_id, start_time);
