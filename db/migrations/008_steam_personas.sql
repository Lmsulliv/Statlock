-- Schema v8: cache Steam display names + avatars by account_id so the read
-- layer can show names instead of bare account ids. Fully optional: absent a
-- STEAM_API_KEY nothing populates this and callers fall back to account ids.
--
-- No FK on account_id: it is not a unique key in match_players (PK there is
-- (match_id, player_slot)); the same account spans many matches. persona_name
-- and avatar_url are NULL for private / unresolved profiles, which still get a
-- row (with fetched_at set) so they age out of the refresh query like any other.
CREATE TABLE steam_personas (
    account_id   INTEGER PRIMARY KEY,
    persona_name TEXT,            -- NULL for private / unresolved profiles
    avatar_url   TEXT,            -- avatarfull (184px), NULL if unresolved
    fetched_at   TEXT NOT NULL    -- ISO 8601
);
