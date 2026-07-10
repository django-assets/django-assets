// Round-3 grader deep probe of the live reference.
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'fs';
const OUT = process.argv[2];
mkdirSync(OUT, { recursive: true });
const USER = process.env.OI_EMAIL, PASS = process.env.OI_PASSWORD;
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const log = (...a) => console.log('[r3]', ...a);
const save = async (name) => {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }).catch(()=>{});
  writeFileSync(`${OUT}/${name}.txt`, await page.locator('body').innerText().catch(()=>''), 'utf8');
};
const step = async (name, fn) => { try { await fn(); log('ok', name); } catch (e) { log('ERR', name, e.message.slice(0,150)); await save(`err-${name}`); } };

// login
await page.goto('https://www.optionincome.io/', { waitUntil: 'networkidle', timeout: 45000 });
await page.locator('a:has-text("Sign in"), button:has-text("Sign in")').first().click();
await page.waitForTimeout(2500);
const email = page.getByPlaceholder(/email/i).first();
await email.fill(USER);
await page.locator('button:visible').filter({ hasText: /continue/i }).filter({ hasNotText: /google/i }).first().click();
await page.waitForTimeout(3500);
await page.locator('input[type="password"]:visible').first().fill(PASS);
await page.locator('.cl-formButtonPrimary:visible, .cl-card button[type="submit"]:visible').first().click();
await page.waitForLoadState('networkidle', { timeout: 45000 }).catch(() => {});
await page.waitForTimeout(3000);
await page.goto('https://www.optionincome.io/dashboard#live', { waitUntil: 'networkidle', timeout: 45000 });
await page.waitForTimeout(4000);
log('logged in:', /Account Summary/.test(await page.locator('body').innerText()));

// ---- History: date range dropdown ----
await step('history-daterange', async () => {
  await page.locator('text="History"').first().click();
  await page.waitForTimeout(2500);
  await page.locator('button:has-text("Date Range"), text="Date Range"').first().click();
  await page.waitForTimeout(1200);
  await save('hist-daterange-open');
  // try selecting an option that mentions days / month / time
  const opt = page.locator('[role=menuitem], [role=option], li, label, button').filter({ hasText: /30|month|year|time|days/i }).first();
  if (await opt.count()) { await opt.click(); await page.waitForTimeout(2000); }
  await save('hist-daterange-selected');
});

// ---- History: empty search + Enter key ----
await step('history-empty-search', async () => {
  const box = page.getByPlaceholder(/symbol/i).first();
  await box.fill('ZZZZZ');
  await box.press('Enter');
  await page.waitForTimeout(2500);
  await save('hist-empty');
  await box.fill('');
  await page.waitForTimeout(1500);
});

// ---- Positions: empty search ----
await step('positions-empty', async () => {
  await page.locator('text="Option Positions"').first().click();
  await page.waitForTimeout(2500);
  const box = page.getByPlaceholder(/symbol/i).first();
  await box.fill('QQQQQ');
  await box.press('Enter');
  await page.waitForTimeout(2500);
  await save('pos-empty');
  await box.fill('');
  await page.waitForTimeout(1500);
});

// ---- header toggle labels on positions ----
await step('positions-toggles', async () => {
  await page.locator('th').filter({ hasText: /PNL/i }).first().click();
  await page.waitForTimeout(1500);
  await save('pos-pnl-toggled');
  await page.locator('th').filter({ hasText: /DELTA|EXTRINSIC/i }).first().click();
  await page.waitForTimeout(1500);
  await save('pos-delta-toggled');
});

// ---- Calendar: tooltips + selects ----
await step('calendar', async () => {
  await page.locator('text="Calendar"').first().click();
  await page.waitForTimeout(2500);
  await save('calendar-day');
  const titles = await page.locator('[title]').evaluateAll(els => els.slice(0,40).map(e => e.getAttribute('title')));
  writeFileSync(`${OUT}/calendar-titles.txt`, JSON.stringify(titles, null, 1));
});

// ---- Analytics goal + switch ----
await step('analytics', async () => {
  await page.locator('text="Analytics"').first().click();
  await page.waitForTimeout(1200);
  const sub = page.locator('text=/Overview|Performance|PnL/').first();
  await page.waitForTimeout(1500);
  await save('analytics-default');
});

// ---- light theme across screens ----
await step('light-theme', async () => {
  await page.locator('header button, nav button, [class*=theme]').filter({ has: page.locator('svg') }).first().click().catch(()=>{});
  await page.waitForTimeout(1200);
  await save('analytics-light');
  await page.locator('text="History"').first().click();
  await page.waitForTimeout(2200);
  await save('history-light');
  await page.locator('text="Option Positions"').first().click();
  await page.waitForTimeout(2200);
  await save('positions-light');
});

// ---- widths ----
await step('widths', async () => {
  for (const w of [1680, 1280, 900]) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${OUT}/positions-w${w}.png`, fullPage: false });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
});

await browser.close();
log('done');
