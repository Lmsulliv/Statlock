-- Schema v12: Steam-login authentication.
--
-- users gains the Steam identity it is keyed by: steam_account_id is the 32-bit
-- account id (the same normalization the importer already does on SteamID64). It
-- is NULL for the migration-seeded local/dev user (id 1), which has no Steam login.
-- A UNIQUE index enforces one user per Steam account; SQLite treats NULLs as
-- distinct, so the local user's NULL never collides.
--
-- sessions backs cookie login server-side so logout truly revokes (delete the row)
-- and sessions can expire. The httpOnly cookie holds only the opaque random token;
-- everything else lives here.

ALTER TABLE users ADD COLUMN steam_account_id INTEGER;   -- 32-bit; NULL for the local/dev user
CREATE UNIQUE INDEX idx_users_steam ON users(steam_account_id);

CREATE TABLE sessions (
    token      TEXT PRIMARY KEY,                          -- random; carried in the cookie
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,                             -- ISO 8601
    expires_at TEXT NOT NULL                              -- ISO 8601; past = treated as logged out
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
