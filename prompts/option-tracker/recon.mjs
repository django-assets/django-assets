// Deep recon of the reference app: expand rows, visit every sidebar
// screen, dump innerText + screenshots. Usage:
//   node --env-file=.env recon.mjs <outDir>
import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const OUT = process.argv[2] || '.';
const USER = process.env.OI_EMAIL;
const PASS = process.env.OI_PASSWORD;
const log = (...a) => console.log('[recon]', ...a);

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

async function login() {
  await page.goto('https://www.optionincome.io/', { waitUntil: 'networkidle', timeout: 45000 });
  await page.locator('a:has-text("Sign in"), button:has-text("Sign in")').first().click();
  await page.waitForTimeout(2500);
  const email = page.getByPlaceholder(/email/i).first();
  await email.waitFor({ state: 'visible', timeout: 15000 });
  await email.fill(USER);
  await page.locator('button:visible').filter({ hasText: /continue/i }).filter({ hasNotText: /google/i }).first().click();
  await page.waitForTimeout(3500);
  const pass = page.locator('input[type="password"]:visible').first();
  await pass.waitFor({ state: 'visible', timeout: 15000 });
  await pass.fill(PASS);
  await page.locator('.cl-formButtonPrimary:visible, .cl-card button[type="submit"]:visible').first().click();
  await page.waitForLoadState('networkidle', { timeout: 45000 }).catch(() => {});
  await page.waitForTimeout(4000);
  await page.goto('https://www.optionincome.io/dashboard#live', { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(5000);
}

async function snap(name, { full = true } = {}) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full });
  const text = await page.locator('body').innerText();
  writeFileSync(`${OUT}/${name}.txt`, text);
  log('captured', name);
}

await login();
log('logged in at', page.url());

// 1. Expand the first three option rows (per-leg greeks + roll history).
const expanders = page.locator('table tbody tr td:first-child, [role=row] >> nth=0');
try {
  const chevrons = page.locator('tbody tr').locator('svg').first();
  // click the row itself; most row-expanders toggle on row click
  const rows = page.locator('tbody tr');
  const count = await rows.count();
  log('table rows:', count);
  for (const i of [0, 1, 2]) {
    await rows.nth(i).click().catch(() => {});
    await page.waitForTimeout(800);
  }
  await snap('dashboard-rows-expanded');
} catch (e) { log('expand failed:', e.message); }

// 2. HTML structure dump of the positions table (for exact columns/classes).
const tableHtml = await page.locator('table').first().evaluate(el => el.outerHTML).catch(() => 'NO TABLE');
writeFileSync(`${OUT}/positions-table.html`, tableHtml.slice(0, 400000));

// 3. Strategy filter open.
try {
  await page.locator('button:has-text("Strategy")').first().click();
  await page.waitForTimeout(800);
  await snap('strategy-filter-open', { full: false });
  await page.keyboard.press('Escape');
} catch (e) { log('strategy filter failed:', e.message); }

// 4. Sidebar screens.
for (const item of ['Equity Positions', 'Analytics', 'Calendar', 'History', 'Broker Connection']) {
  try {
    await page.locator(`text="${item}"`).first().click();
    await page.waitForTimeout(2500);
    await snap(item.toLowerCase().replace(/\s+/g, '-'));
    // expandable menus may reveal sub-items
    const sub = await page.locator('nav, aside').first().innerText().catch(() => '');
    writeFileSync(`${OUT}/sidebar-after-${item.replace(/\s+/g, '-')}.txt`, sub);
  } catch (e) { log(item, 'failed:', e.message); }
}

// 5. Light theme.
try {
  await page.locator('button:has([class*=sun]), [aria-label*=theme i], button:has(svg):near(:text("Dashboard"))').first().click();
  await page.waitForTimeout(1200);
  await snap('light-theme', { full: false });
} catch (e) { log('theme toggle failed:', e.message); }

await browser.close();
log('done');
