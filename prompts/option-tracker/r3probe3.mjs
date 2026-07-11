import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'fs';
const OUT = process.argv[2];
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const log = (...a) => console.log('[r3c]', ...a);
const save = async (name) => {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }).catch(()=>{});
  writeFileSync(`${OUT}/${name}.txt`, await page.locator('body').innerText().catch(()=>''), 'utf8');
};
await page.goto('https://www.optionincome.io/', { waitUntil: 'networkidle', timeout: 45000 });
await page.locator('a:has-text("Sign in"), button:has-text("Sign in")').first().click();
await page.waitForTimeout(2500);
await page.getByPlaceholder(/email/i).first().fill(process.env.OI_EMAIL);
await page.locator('button:visible').filter({ hasText: /continue/i }).filter({ hasNotText: /google/i }).first().click();
await page.waitForTimeout(3500);
await page.locator('input[type="password"]:visible').first().fill(process.env.OI_PASSWORD);
await page.locator('.cl-formButtonPrimary:visible, .cl-card button[type="submit"]:visible').first().click();
await page.waitForLoadState('networkidle', { timeout: 45000 }).catch(() => {});
await page.waitForTimeout(3000);
await page.goto('https://www.optionincome.io/dashboard#live', { waitUntil: 'networkidle', timeout: 45000 });
await page.waitForTimeout(4000);

// 1. History: filter by real symbol HIMS — do totals recompute?
await page.locator('text="History"').first().click();
await page.waitForTimeout(2500);
const box = page.getByPlaceholder(/symbol/i).first();
await box.fill('HIMS');
await page.waitForTimeout(2500);
const t = await page.locator('body').innerText();
const m = t.match(/Total Strategies:\s*\d+[\s\S]{0,80}/);
log('HIMS totals:', JSON.stringify((m||[''])[0].slice(0,120)));
await save('hist-hims');
await box.fill('');
await page.waitForTimeout(1500);

// 2. Date range: pick "This Month" — button label + totals?
await page.locator('button:has-text("Date Range")').first().click();
await page.waitForTimeout(1000);
await page.getByText('This Month', { exact: true }).first().click();
await page.waitForTimeout(2500);
await save('hist-thismonth');
log('button label now:', await page.locator('button:has-text("Range"), button:has-text("Month")').allInnerTexts());

// 3. Equity positions empty states (wheel + all)
await page.locator('text="Equity Positions"').first().click();
await page.waitForTimeout(2000);
await save('equity-nav-open');
const sub = await page.locator('nav, aside').first().innerText();
log('sidebar after equity click:', JSON.stringify(sub.slice(0,400)));
// wheel table search junk
const wbox = page.getByPlaceholder(/symbol/i).first();
if (await wbox.count()) { await wbox.fill('ZZZZ'); await page.waitForTimeout(2200); await save('wheel-empty'); await wbox.fill(''); await page.waitForTimeout(1200); }
// find sub-tab for all equity positions
const allTab = page.getByText(/All Positions|Equity/i);
await browser.close();
log('done');
