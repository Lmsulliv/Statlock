// Local/dev fallback for showing the management screens (Accounts importer, Era
// manager). When Steam login is configured (the API's DEADLOCK_BASE_URL), the app
// instead shows those screens to logged-in users and ignores this flag. In local
// single-user mode there's no login, so this build-time flag decides whether the
// management nav appears. Hiding the UI is cosmetic; the API enforces every write.
// Set VITE_OWNER=true in frontend/.env.local to enable it locally.
export const isOwner = import.meta.env.VITE_OWNER === 'true'
