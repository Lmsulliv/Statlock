-- Schema v19: v_account_matches -- one per-(account, match) row set that unions
-- full metadata (match_players + matches) with provisional history summaries
-- (account_match_summaries), so summary-capable screens render instantly on a
-- fresh import and upgrade in place once the drain loop ingests metadata.
--
-- Why a view, not a table: the two sources already exist and stay in sync via
-- their own writers (metadata ingestion, discovery's summary upsert). A view is
-- a saved query -- zero extra storage, always current, and it can't drift from
-- its sources the way a hand-maintained third copy would.
--
-- The anti-join (NOT EXISTS) is the no-double-count guarantee: a summary row is
-- surfaced ONLY while its (account_id, match_id) has no match_players row. The
-- instant a match's metadata lands -- in ingestion's own transaction (hard rule
-- 4: one match = one transaction) -- the 'summary' twin disappears and the
-- 'full' row takes its place. A match is therefore counted exactly once, and the
-- upgrade is atomic: there is no window where both rows are visible.
--
-- NULL means "we don't know", never zero -- the same parsing rule the summary
-- table itself follows (migration 017). A summary row has no damage/healing
-- capture and no era/badge context, so player_damage, obj_damage, healing,
-- player_damage_taken, era_id, team, and the two average_badge columns are all
-- NULL on the 'summary' side. Consequences the read layer relies on:
--   * A metric whose input is NULL yields NULL for that row; the stats layer
--     already ignores non-finite values, so a summary row contributes only the
--     metrics it actually knows (kills, deaths, net worth, ...).
--   * era- and badge-narrowed scopes DROP summary rows automatically: NULL fails
--     both `era_id IN (...)` and the badge `BETWEEN`, so a narrowed scope
--     honestly excludes the rows it can't place. At the default all-time
--     full-range scope (no era filter, badge predicate dropped) they are kept.
-- The `source` column ('full' | 'summary') lets a caller both filter to metadata
-- only (WHERE source = 'full') and flag a mixed result as provisional.

CREATE VIEW v_account_matches AS
SELECT mp.account_id, mp.match_id, mp.hero_id, m.start_time, m.game_mode,
       m.era_id, mp.won, mp.kills, mp.deaths, mp.assists, mp.net_worth,
       mp.last_hits, mp.denies, mp.player_damage, mp.obj_damage, mp.healing,
       mp.player_damage_taken, m.duration_s,
       mp.team, m.average_badge_team0, m.average_badge_team1,
       'full' AS source
  FROM match_players mp
  JOIN matches m ON m.match_id = mp.match_id
UNION ALL
SELECT s.account_id, s.match_id, s.hero_id, s.start_time, s.game_mode,
       NULL AS era_id, s.won, s.kills, s.deaths, s.assists, s.net_worth,
       s.last_hits, s.denies, NULL AS player_damage, NULL AS obj_damage,
       NULL AS healing, NULL AS player_damage_taken, s.duration_s,
       NULL AS team, NULL AS average_badge_team0, NULL AS average_badge_team1,
       'summary' AS source
  FROM account_match_summaries s
 WHERE NOT EXISTS (
     SELECT 1 FROM match_players mp
      WHERE mp.account_id = s.account_id
        AND mp.match_id  = s.match_id
 );

-- Serves BOTH the view's anti-join probe (idx_mp_account is account-only, so
-- without match_id the NOT EXISTS would scan every one of an account's rows per
-- summary) AND the progress endpoint's `analyzed` count
-- (COUNT(*) FROM match_players WHERE account_id = ?), which reads straight off
-- this index without paying for the union.
CREATE INDEX idx_mp_account_match ON match_players(account_id, match_id);
