import fs from 'node:fs'
import jwt from 'jsonwebtoken'
import type { Page } from '@playwright/test'
import { ISSUER } from './constants'
import { KID, PRIVATE_KEY_PATH } from './global-setup'

export const TEST_USER = {
  sub: '22222222-2222-4222-8222-222222222222',
  email: 'e2e@nextlane.dev',
}

/** Signs a short-lived ES256 access token against the local JWKS keypair
 * written by global-setup, satisfying the API's required claims
 * (exp/aud/iss/sub). */
export function makeAccessToken(): string {
  const privateKey = fs.readFileSync(PRIVATE_KEY_PATH, 'utf8')
  const now = Math.floor(Date.now() / 1000)

  return jwt.sign(
    {
      sub: TEST_USER.sub,
      email: TEST_USER.email,
      aud: 'authenticated',
      role: 'authenticated',
      iss: ISSUER,
      exp: now + 3600,
    },
    privateKey,
    { algorithm: 'ES256', keyid: KID, noTimestamp: true },
  )
}

/** Seeds localStorage with a supabase-js-shaped session under the app's
 * deterministic storageKey ("nextlane-auth") before any app script runs, so
 * the AuthGuard sees an already-authenticated session on first paint. No
 * real auth server is ever contacted -- the session is trusted client-side
 * and only the API validates the token (against the local JWKS). */
export async function injectSession(page: Page): Promise<void> {
  const accessToken = makeAccessToken()
  const expiresAt = Math.floor(Date.now() / 1000) + 3600

  const session = {
    access_token: accessToken,
    refresh_token: 'e2e-refresh',
    token_type: 'bearer',
    expires_at: expiresAt,
    expires_in: 3600,
    user: {
      id: TEST_USER.sub,
      aud: 'authenticated',
      email: TEST_USER.email,
    },
  }

  await page.addInitScript(
    ([key, value]) => {
      window.localStorage.setItem(key, value)
    },
    ['nextlane-auth', JSON.stringify(session)],
  )
}
