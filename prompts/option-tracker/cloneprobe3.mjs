import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'fs';
const OUT = '/tmp/claude-1001/-home-selden-django-assets-django-assets/da5d34ba-7dd5-44f4-95f1-43b45b540065/scratchpad/r3clone';
mkdirSync(OUT, { recursive: true });
const BASE = 'http://127.0.0.1:8642';
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
page.on('pageerror', e => console.log('PAGEERROR', e.message.slice(0,150)));
const bad = [];
page.on('response', r => { if (r.status() >= 500) bad.push(r.status() + ' ' + r.url()); });
const log = (...a) => console.log('[c3]', ...a);

// 1. light theme across all screens
await page.goto(BASE + '/tracker/', { waitUntil: 'networkidle', timeout: 120000 });
await page.locator('#theme-toggle').click();
await page.waitForTimeout(400);
log('theme now:', await page.evaluate(() => document.documentElement.dataset.theme));
for (const [path, name] of [['/tracker/', 'positions'], ['/tracker/wheel/', 'wheel'], ['/tracker/equities/', 'equities'], ['/tracker/analytics/', 'analytics'], ['/tracker/analytics/flow/', 'flow'], ['/tracker/calendar/', 'calendar'], ['/tracker/history/', 'history'], ['/tracker/broker/', 'broker']]) {
  await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 120000 });
  await page.waitForTimeout(400);
  log(name, 'theme persisted:', await page.evaluate(() => document.documentElement.dataset.theme));
  await page.screenshot({ path: `${OUT}/light-${name}.png`, fullPage: true });
}
// back to dark
await page.locator('#theme-toggle').click();
await page.waitForTimeout(300);

// 2. rapid typing in history search
await page.goto(BASE + '/tracker/history/', { waitUntil: 'networkidle', timeout: 120000 });
const box = page.locator('input[name=q]').first();
await box.pressSequentially('CONL', { delay: 60 });
await page.waitForTimeout(3500);
let rows = await page.locator('tbody tr.pos-row, tbody tr.expandable').count();
let text = await page.locator('body').innerText();
log('rapid CONL rows:', rows, 'totals:', (text.match(/Total Strategies:\s*\d+/) || [''])[0]);
// clear fast then type junk
await box.fill('');
await box.pressSequentially('ZZGX', { delay: 40 });
await page.waitForTimeout(3500);
text = await page.locator('body').innerText();
log('junk search empty msg present:', /No closed strategies match/.test(text));
await page.screenshot({ path: `${OUT}/history-junk.png`, fullPage: false });

// 3. Enter key in search must not lose state / navigate weirdly
await box.fill('');
await box.pressSequentially('AAPL', { delay: 30 });
await box.press('Enter');
await page.waitForTimeout(2500);
log('after Enter url:', page.url(), 'still on history:', /Historical Positions/.test(await page.locator('body').innerText()));

// 4. combined filters: strategy + range + q on history
await page.goto(BASE + '/tracker/history/', { waitUntil: 'networkidle', timeout: 120000 });
await page.locator('#history-strategy-btn').click();
await page.waitForTimeout(300);
const cb = page.locator('#history-strategy-menu input[type=checkbox]').first();
const slug = await cb.getAttribute('value');
await cb.check();
await page.waitForTimeout(2500);
text = await page.locator('body').innerText();
log('strategy filter applied:', slug, 'totals:', (text.match(/Total Strategies:\s*\d+/) || [''])[0]);
await page.locator('select[name=range]').selectOption('1y');
await page.waitForTimeout(2500);
text = await page.locator('body').innerText();
log('range+strategy totals:', (text.match(/Total Strategies:\s*\d+/) || [''])[0], 'url:', page.url());
// now tick Assigned on top of filters
await page.locator('input[name=assigned]').check();
await page.waitForTimeout(2500);
text = await page.locator('body').innerText();
log('assigned w/ filters: totalAssignments:', (text.match(/Total Assignments:\s*\d+/) || ['none'])[0], 'strategy btn disabled:', await page.locator('#history-strategy-btn[disabled]').count());
await page.screenshot({ path: `${OUT}/history-combined.png`, fullPage: false });
await page.locator('input[name=assigned]').uncheck();
await page.waitForTimeout(2000);
log('after uncheck, strategy still selected:', page.url().includes('strategy='));

// 5. weird direct URLs — no 500s
for (const u of ['/tracker/?sort=bogus&pnl=weird&metric=x', '/tracker/calendar/?year=abc&month=13', '/tracker/calendar/?view=month&year=1900', '/tracker/history/?range=nope&sort=--pnl&assigned=maybe', '/tracker/analytics/?goal=-50&mode=x&chart=y', '/tracker/analytics/?goal=1e309', '/tracker/history/?q=%00%ff', '/tracker/equities/?pnl=usd&q=<script>']) {
  const resp = await page.goto(BASE + u, { waitUntil: 'domcontentloaded', timeout: 120000 });
  log('url', u, '->', resp.status());
}
log('5xx responses:', JSON.stringify(bad));
await browser.close();
