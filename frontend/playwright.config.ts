import { defineConfig, devices } from '@playwright/test'

// End-to-end tests run the production build (vite preview) against a real API with the
// in-memory payment provider and its own database. Locally the database comes from
// docker compose; in CI from the workflow's PostgreSQL service.
const database =
  process.env.E2E_DATABASE_URL ?? 'postgresql+asyncpg://tradepost:tradepost@localhost:5433/tradepost_e2e'
const web = 'http://localhost:4173'

export default defineConfig({
  testDir: 'e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: web,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'phone', use: { ...devices['Pixel 7'] } },
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 860 } } },
  ],
  webServer: [
    {
      cwd: '../backend',
      command: 'uv run alembic upgrade head && uv run uvicorn tradepost.main:create_app --factory --port 8001',
      url: 'http://localhost:8001/healthz',
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: {
        TP_DATABASE_URL: database,
        TP_PAYMENT_PROVIDERS: '["fake"]',
        TP_PUBLIC_ORIGIN: web,
        TP_MEDIA_LOCAL_DIR: './var/e2e-media',
        TP_RATE_LIMIT_SCALE: '100',
        TP_LOG_JSON: 'false',
        TP_BREACHED_PASSWORD_CHECK: 'false',
        // E-mails (order notices, password reset links) are written to the log, never sent.
        TP_EMAIL_PROVIDER: 'log',
      },
    },
    {
      command: 'npm run build && npx vite preview',
      url: web,
      timeout: 180_000,
      reuseExistingServer: !process.env.CI,
      env: { TP_API_URL: 'http://localhost:8001' },
    },
  ],
})
