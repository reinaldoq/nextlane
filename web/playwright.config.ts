import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'
import { API_PORT, ISSUER, JWKS_URL, WEB_PORT } from './e2e/constants'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..')

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list']] : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: 'uv run uvicorn api.index:app --port 8000',
      cwd: repoRoot,
      env: {
        SUPABASE_JWKS_URL: JWKS_URL,
        SUPABASE_JWT_ISSUER: ISSUER,
        DATABASE_URL:
          process.env.DATABASE_URL ?? 'postgresql://postgres:postgres@127.0.0.1:54322/postgres',
      },
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: false,
    },
    {
      // Production build: vite preview serves the real build and inherits
      // the /api proxy from server.proxy in vite.config.ts. --host 127.0.0.1
      // pins it to IPv4: on this machine "localhost" resolves to ::1 first,
      // which would leave the IPv4 health-check/baseURL below unable to connect.
      command: 'npm run build && npm run preview -- --port 5173 --strictPort --host 127.0.0.1',
      cwd: __dirname,
      env: {
        VITE_SUPABASE_URL: 'http://127.0.0.1:8999',
        VITE_SUPABASE_ANON_KEY: 'e2e-dummy',
      },
      url: `http://127.0.0.1:${WEB_PORT}`,
      timeout: 180_000,
      reuseExistingServer: false,
    },
  ],
})
