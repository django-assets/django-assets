// Reference-app driver for the option-tracker build.
//
// Logs into optionincome.io (Clerk two-step: email -> Continue -> password -> Continue,
// NOT "Continue with Google") and screenshots the mock-data dashboard. Both the build
// agent and the fresh-context grader use this to drive the reference app headlessly for
// visual comparison against the clone.
//
// Usage:
//   cp .env.example .env      # then fill in real creds (.env is gitignored)
//   node --env-file=.env reference-login.mjs [outDir]
//
// Requires Playwright + a chromium build:
//   npx playwright install chromium
//
// Exits non-zero if login fails, so it can gate a loop.

import { chromium } from 'playwright';

const OUT = process.argv[2] || '.';
const USER = process.env.OI_EMAIL;
const PASS = process.env.OI_PASSWORD;
const URL = process.env.OI_URL || 'https://www.optionincome.io/dashboard#live';
const log = (...a) => console.log('[ref]', ...a);

if (!USER || !PASS) {
  console.error('Missing OI_EMAIL / OI_PASSWORD. Run with: node --env-file=.env reference-login.mjs');
  process.exit(2);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

try {
  await page.goto('https://www.optionincome.io/', { waitUntil: 'networkidle', timeout: 45000 });
  await page.locator('a:has-text("Sign in"), button:has-text("Sign in")').first().click();
  await page.waitForTimeout(2500);

  // Clerk step 1: email. Click the email form's Continue, NOT "Continue with Google".
  const email = page.getByPlaceholder(/email/i).first();
  await email.waitFor({ state: 'visible', timeout: 15000 });
  await email.fill(USER);
  await page.locator('button:visible')
    .filter({ hasText: /continue/i }).filter({ hasNotText: /google/i }).first().click();
  await page.waitForTimeout(3500);

  // Clerk step 2: password. Use Clerk's own primary button (page header has a decoy "Sign in").
  const pass = page.locator('input[type="password"]:visible').first();
  await pass.waitFor({ state: 'visible', timeout: 15000 });
  await pass.fill(PASS);
  await page.locator('.cl-formButtonPrimary:visible, .cl-card button[type="submit"]:visible').first().click();
  await page.waitForLoadState('networkidle', { timeout: 45000 }).catch(() => {});
  await page.waitForTimeout(4000);

  await page.goto(URL, { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(5000);

  const title = await page.title();
  const bodyText = await page.locator('body').innerText();
  const loggedIn = /mock data/i.test(bodyText) || /Account Summary/i.test(bodyText);
  log('url:', page.url());
  log('title:', title);
  log('logged in (mock data visible):', loggedIn);

  await page.screenshot({ path: `${OUT}/reference-dashboard.png`, fullPage: false });
  await page.screenshot({ path: `${OUT}/reference-dashboard-full.png`, fullPage: true });
  log('screenshots written to', OUT);

  if (!loggedIn) {
    console.error('[ref] ERROR: dashboard did not show mock data — login likely failed.');
    process.exit(1);
  }
} catch (e) {
  log('ERROR:', e.message);
  await page.screenshot({ path: `${OUT}/reference-error.png`, fullPage: true }).catch(() => {});
  process.exit(1);
} finally {
  await browser.close();
}
