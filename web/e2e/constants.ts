import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/** Local JWKS server used to sign/verify the test-only auth session. */
export const JWKS_PORT = 8999
export const ISSUER = `http://127.0.0.1:${JWKS_PORT}/auth/v1`
export const JWKS_URL = `http://127.0.0.1:${JWKS_PORT}/jwks.json`

/** Where global-setup writes the generated EC keypair so the spec/auth
 * helper can read it back to sign tokens. */
export const ARTIFACTS_DIR = path.join(__dirname, '.artifacts')

export const WEB_PORT = 5173
export const API_PORT = 8000
