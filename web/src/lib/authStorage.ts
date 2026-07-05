/** Side-effect-free: the deterministic supabase-js session storageKey, shared
 * between the app's client (`lib/supabase.ts`) and the e2e session-injection
 * helper (`e2e/auth.ts`) so the two never drift apart. */
export const AUTH_STORAGE_KEY = 'nextlane-auth'
