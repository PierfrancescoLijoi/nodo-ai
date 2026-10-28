import { defineConfig } from '@playwright/test';

const LOCAL_PORT = 8797;
const CLOUD_PORT = 8796;
const python = process.env.NODO_PYTHON ?? 'python';
const reuse = !process.env.CI;

// Two stand-in back ends for the same static build: the real local engine (fake models) and a small
// Azure look-alike with an OpenID Connect provider (e2e/support/cloud-stack.mjs).
export default defineConfig({
  testDir: 'e2e',
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    channel: process.env.PW_CHANNEL || undefined,
    trace: 'retain-on-failure',
    permissions: ['microphone'],
    launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  },
  projects: [
    { name: 'local', testMatch: /local\..*\.spec\.ts/, use: { baseURL: `http://127.0.0.1:${LOCAL_PORT}` } },
    { name: 'cloud', testMatch: /cloud\..*\.spec\.ts/, use: { baseURL: `http://127.0.0.1:${CLOUD_PORT}` } },
  ],
  webServer: [
    {
      command: `${python} scripts/e2e_engine.py --web ../../apps/web/out --port ${LOCAL_PORT}`,
      cwd: '../../services/api',
      url: `http://127.0.0.1:${LOCAL_PORT}/healthz`,
      reuseExistingServer: reuse,
      timeout: 60_000,
    },
    {
      command: 'node e2e/support/cloud-stack.mjs',
      env: { PORT: String(CLOUD_PORT) },
      url: `http://127.0.0.1:${CLOUD_PORT}/healthz`,
      reuseExistingServer: reuse,
      timeout: 30_000,
    },
  ],
});
