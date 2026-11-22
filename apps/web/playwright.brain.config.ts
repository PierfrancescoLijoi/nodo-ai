import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const webRoot = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  testDir: 'e2e',
  testMatch: /local\.brain\.spec\.ts/,
  workers: 1,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:8798', channel: 'chrome', trace: 'retain-on-failure' },
  webServer: {
    command: 'node e2e/support/static-web.mjs',
    cwd: webRoot,
    env: { PORT: '8798' },
    url: 'http://127.0.0.1:8798',
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
