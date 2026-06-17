// Interim owner gate — NOT authentication. Until a real login exists the Era
// manager (the app's only write surface) is hidden unless this build-time flag
// is set. Hiding the UI is cosmetic; the real enforcement is the API returning
// 403 on the confirm/dismiss endpoints. Set VITE_OWNER=true in frontend/.env.local
// to enable it locally (see frontend/.env.example).
export const isOwner = import.meta.env.VITE_OWNER === 'true'
