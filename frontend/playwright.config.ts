import os from 'node:os';
import path from 'node:path';

import { defineConfig, devices } from '@playwright/test';

const PORT = 8765;
const video = path.join(os.tmpdir(), 'videokit-e2e', 'clip.mp4');
// Read by the specs (workers inherit the environment).
process.env.VIDEOKIT_E2E_VIDEO = video;

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    testIdAttribute: 'data-test',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop-chrome', use: { ...devices['Desktop Chrome'] } },
    { name: 'iphone', use: { ...devices['iPhone 13'] } },
    { name: 'pixel', use: { ...devices['Pixel 7'] } },
    // The narrowest supported phone width (FR-038, SC-010): 360x740, mobile, touch.
    { name: 'mobile-360', use: { ...devices['Galaxy S8'] } },
  ],
  // Builds the SPA, then serves it from the backend with fake providers (no API keys, no network).
  webServer: {
    command:
      `npm run build && cd ../backend && uv run python tests/e2e_app.py ` +
      `--static ../frontend/dist --video "${video}" --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
