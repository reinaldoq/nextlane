import fs from 'node:fs'
import { generateKeyPairSync, type JsonWebKey } from 'node:crypto'
import http from 'node:http'
import { ARTIFACTS_DIR, JWKS_PORT } from './constants'

export const PRIVATE_KEY_PATH = `${ARTIFACTS_DIR}/e2e-key.pem`
export const KID = 'e2e-key'

/**
 * Generates a throwaway EC P-256 keypair and serves its public half as a
 * JWKS document, standing in for Supabase's real JWKS endpoint. The API
 * (SUPABASE_JWKS_URL) and the spec's token signer (auth.ts) both point at
 * this key: the API fetches it lazily on the first authed request, so this
 * only needs to be up before any test *interacts* with the app -- not
 * before the webServers finish booting.
 */
export default async function globalSetup(): Promise<() => Promise<void>> {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true })

  const { privateKey, publicKey } = generateKeyPairSync('ec', { namedCurve: 'P-256' })

  fs.writeFileSync(PRIVATE_KEY_PATH, privateKey.export({ type: 'pkcs8', format: 'pem' }))

  const jwk: JsonWebKey & { kid: string; alg: string; use: string } = {
    ...(publicKey.export({ format: 'jwk' }) as JsonWebKey),
    kid: KID,
    alg: 'ES256',
    use: 'sig',
  }
  const body = JSON.stringify({ keys: [jwk] })

  const server = http.createServer((_req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(body)
  })

  await new Promise<void>((resolve, reject) => {
    server.once('error', (err) => {
      reject(
        new Error(
          `e2e JWKS server could not bind 127.0.0.1:${JWKS_PORT} -- is a stale process ` +
            `holding the port? (JWKS_PORT lives in e2e/constants.ts)`,
          { cause: err },
        ),
      )
    })
    server.listen(JWKS_PORT, '127.0.0.1', resolve)
  })

  return async () => {
    await new Promise<void>((resolve, reject) => {
      server.close((err) => {
        if (err) reject(err)
        else resolve()
      })
    })
  }
}
