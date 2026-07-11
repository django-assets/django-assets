import { chromium } from 'playwright';
import { writeFileSync } from 'fs';
const OUT = process.argv[2] || '.';
const USER = process.env.OI_EMAIL, PASS = process.env.OI_PASSWORD;
const log = (...a) => console.log('[r2]', ...a);
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.goto('https://www.optionincome.io/', { waitUntil: 'networkidle', timeout: 45000 });
await page.locator('a:has-text("Sign in"), button:has-text("Sign in")').first().click();
await page.waitForTimeout(2500);
const email = page.getByPlaceholder(/email/i).first();
await email.fill(USER);
await page.locator('button:visible').filter({ hasText: /continue/i }).filter({ hasNotText: /google/i }).first().click();
await page.waitForTimeout(3500);
await page.locator('input[type="password"]:visible').first().fill(PASS);
await page.locator('.cl-formButtonPrimary:visible, .cl-card button[type="submit"]:visible').first().click();
await page.waitForTimeout(5000);
await page.goto('https://www.optionincome.io/dashboard#live', { waitUntil: 'networkidle', timeout: 45000 });
await page.waitForTimeout(4000);
async function snap(name) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  writeFileSync(`${OUT}/${name}.txt`, await page.locator('body').innerText());
  log('captured', name);
}
// Analytics -> PnL Flow
await page.locator('text="Analytics"').first().click(); await page.waitForTimeout(1500);
await page.locator('text="PnL Flow"').first().click().catch(e => log('pnlflow', e.message));
await page.waitForTimeout(2500); await snap('analytics-pnl-flow');
// Equity Positions -> plain Equity Positions subitem
await page.locator('text="Equity Positions"').first().click(); await page.waitForTimeout(1500);
await page.locator('nav >> text="Equity Positions", aside >> text="Equity Positions"').last().click().catch(async () => {
  const items = page.locator('text="Equity Positions"');
  const n = await items.count();
  await items.nth(n - 1).click();
});
await page.waitForTimeout(2500); await snap('equity-positions-plain');
// Search + share behavior on dashboard
await page.locator('text="Option Positions"').first().click(); await page.waitForTimeout(2000);
await page.getByPlaceholder(/search/i).first().fill('ETHA').catch(e => log('search', e.message));
await page.waitForTimeout(1200); await snap('dashboard-search-etha');
await browser.close(); log('done');
