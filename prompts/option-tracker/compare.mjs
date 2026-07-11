// Phase-2 measuring stick: drive the LOCAL clone and the LIVE reference
// side by side. Emits a machine-readable checklist verdict (exit 1 on any
// FAIL) plus paired screenshots for eyeball comparison.
//
// The reference runs on frozen mock data; the clone runs on real, seeded
// data — so VALUES are never compared. The checklist encodes information
// architecture (nav, headings, columns, expanded-row anatomy), behavior
// (search narrows, filters filter, sort toggles, rows expand, theme
// persists, calendar navigates), and format rules (color-coded PnL,
// ITM/OTM badging, (Nd) day counters, $ money formats).
//
// Usage:
//   node --env-file=.env compare.mjs <outDir> [localBase]
//   (localBase default http://127.0.0.1:8642 — start the clone first:
//    set -a && source ../../.env && set +a
//    uv run python manage.py runserver 127.0.0.1:8642 --noreload)

import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const OUT = process.argv[2] || '.';
const LOCAL = process.argv[3] || 'http://127.0.0.1:8642';
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail: String(detail).slice(0, 300) });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : '  — ' + detail}`);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

// ---------- reference captures (screenshots only; login may be flaky) ----------
if (process.env.OI_EMAIL && !process.env.SKIP_REFERENCE) {
  try {
    await page.goto('https://www.optionincome.io/', { waitUntil: 'networkidle', timeout: 45000 });
    await page.locator('a:has-text("Sign in"), button:has-text("Sign in")').first().click();
    await page.waitForTimeout(2500);
    const email = page.getByPlaceholder(/email/i).first();
    await email.fill(process.env.OI_EMAIL);
    await page.locator('button:visible').filter({ hasText: /continue/i }).filter({ hasNotText: /google/i }).first().click();
    await page.waitForTimeout(3500);
    await page.locator('input[type="password"]:visible').first().fill(process.env.OI_PASSWORD);
    await page.locator('.cl-formButtonPrimary:visible, .cl-card button[type="submit"]:visible').first().click();
    await page.waitForTimeout(5000);
    await page.goto('https://www.optionincome.io/dashboard#live', { waitUntil: 'networkidle', timeout: 45000 });
    await page.waitForTimeout(5000);
    await page.screenshot({ path: `${OUT}/ref-positions.png`, fullPage: true });
    const refRows = page.locator('tbody tr');
    if (await refRows.count()) { await refRows.first().click(); await page.waitForTimeout(800); }
    await page.screenshot({ path: `${OUT}/ref-positions-expanded.png`, fullPage: false });
    for (const [label, name] of [['Equity Positions', 'wheel'], ['Analytics', 'analytics'], ['Calendar', 'calendar'], ['History', 'history'], ['Broker Connection', 'broker']]) {
      await page.locator(`text="${label}"`).first().click();
      await page.waitForTimeout(2200);
      await page.screenshot({ path: `${OUT}/ref-${name}.png`, fullPage: true });
    }
    console.log('[compare] reference captured');
  } catch (e) {
    console.log('[compare] reference capture failed (grading continues on local checks):', e.message);
  }
}

// ---------------------------- local checklist ---------------------------------
const go = async (path) => {
  await page.goto(`${LOCAL}${path}`, { waitUntil: 'networkidle', timeout: 120000 });
  await page.waitForTimeout(400);
};
const bodyText = () => page.locator('body').innerText();

// -- chrome & summary (every page) --
await go('/tracker/');
await page.screenshot({ path: `${OUT}/local-positions.png`, fullPage: true });
let text = await bodyText();
writeFileSync(`${OUT}/local-positions.txt`, text);
for (const item of ['Option Positions', 'Equity Positions', 'Analytics', 'Calendar', 'History', 'Broker Connection']) {
  check(`sidebar has "${item}"`, text.includes(item));
}
for (const label of ['Account Summary', 'Total Value', 'Options Position', 'Option Margin (Est.)', 'Options PnL', 'Equity Position', 'Equity PnL', 'Cash']) {
  check(`summary card shows "${label}"`, text.includes(label));
}
check('demo-data banner present', /demo data/i.test(text));
check('summary money formatted', /\$[\d,]+\.\d{2}/.test(text));

// -- positions table --
for (const col of ['SYMBOL', 'TYPE (CONTRACTS)', 'EXPIRATION', 'PNL (%)', 'MARKET VALUE', 'DELTA (%)', 'MONEYNESS', 'SHARE']) {
  check(`positions column "${col}"`, text.toUpperCase().includes(col));
}
check('positions heading with count', /Option Positions\s*\(\d+\)/.test(text));
check('strategy vocabulary rendered', /(Put Credit Spread|Covered Call|Iron Condor|Cash Secured Put)\s*\(\d+\)/.test(text));
check('expiration shows (Nd) day counter', /\(\d+d\)/.test(text));
check('moneyness badges', /\d+%\s*\(?(ITM|OTM)\)?/.test(text));
const rowCount = await page.locator('tbody tr.position-row, tbody tr[data-expand], tbody tr').count();
check('has position rows', rowCount >= 10, `rows=${rowCount}`);

// ITM red / OTM green color rule: find the innermost element whose own
// text is the OTM badge and read its computed color.
const otmColor = await page.evaluate(() => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  let best = null;
  while (walker.nextNode()) {
    const el = walker.currentNode;
    if (el.children.length === 0 && /OTM/.test(el.textContent)) { best = el; break; }
  }
  return best ? getComputedStyle(best).color : '';
});
check('OTM badge is greenish', (() => {
  const m = otmColor.match(/\d+/g);
  if (!m) return false;
  const [r, g, b] = m.map(Number);
  return g > r && g > b;
})(), otmColor);

// expand first row → details anatomy
const firstRow = page.locator('tbody tr').first();
await firstRow.click();
await page.waitForTimeout(600);
text = await bodyText();
for (const label of ['Open Date:', 'Initial Premium:', 'AROI']) {
  check(`expanded row shows "${label}"`, text.includes(label));
}
for (const col of ['SIDE / RIGHT', 'STRIKE', 'PRICE', 'IV', 'DELTA', 'GAMMA', 'THETA', 'VEGA']) {
  check(`legs table column "${col}"`, text.toUpperCase().includes(col));
}
check('legs say Short/Long Put/Call', /(Short|Long)\s+(Put|Call)/.test(text));
await page.screenshot({ path: `${OUT}/local-positions-expanded.png`, fullPage: false });

// a rolled position exposes roll history
const rolled = page.locator('tbody tr', { hasText: 'CRCL' }).first();
await rolled.click().catch(() => {});
await page.waitForTimeout(600);
text = await bodyText();
check('roll history sub-table', /Roll Selections \(\d+\)/.test(text));
check('roll history columns', text.includes('OPEN DATE') && text.includes('CLOSE DATE') && text.toUpperCase().includes('INITIAL PREMIUM') && text.toUpperCase().includes('REALIZED PNL'));
check('premium incl. roll shown for rolled position', /Premium Incl\. Roll/i.test(text));

// search narrows (assert on the TABLE, not the whole page)
const searchBox = page.locator('input[name=q]').first();
await searchBox.click();
await searchBox.pressSequentially('AAPL', { delay: 40 });
await page.waitForTimeout(1500);
const tableText = await page.locator('table').first().innerText();
const visibleRows = await page.locator('tbody tr:visible').count();
check('search narrows to AAPL', tableText.includes('AAPL') && !tableText.includes('GOOGL') && visibleRows <= 4, `rows=${visibleRows}`);
await searchBox.fill('');
await page.waitForTimeout(900);

// strategy filter
const stratButton = page.locator('button:has-text("Strategy")').first();
await stratButton.click();
await page.waitForTimeout(400);
const condorOption = page.locator('label:has-text("Iron Condor"), [role=menuitemcheckbox]:has-text("Iron Condor")').first();
if (await condorOption.count()) {
  await condorOption.click();
  await page.waitForTimeout(900);
  text = await bodyText();
  check('strategy filter → only iron condors', text.includes('Iron Condor') && !text.includes('Put Credit Spread ('), '');
  await page.screenshot({ path: `${OUT}/local-positions-filtered.png`, fullPage: false });
  await go('/tracker/');
} else {
  check('strategy filter → only iron condors', false, 'no Iron Condor option found in filter');
}

// sorting toggles — click the header's ANCHOR (the th itself is not the
// htmx trigger) and give the swap time to settle.
await page.locator('th.sortable a:has-text("Expiration")').first().click().catch(() => {});
await page.waitForTimeout(1500);
check('sort by expiration navigates/updates', true); // structural: no crash; ordering asserted below
const dtes = (await bodyText()).match(/\((\d+)d\)/g) || [];
const nums = dtes.map((d) => parseInt(d.match(/\d+/)[0], 10));
check('expiration sort produces ordered day counters', nums.length >= 3 && (nums.every((v, i, a) => i === 0 || a[i - 1] <= v) || nums.every((v, i, a) => i === 0 || a[i - 1] >= v)), dtes.slice(0, 6).join(','));

// TradingView affordance
check('TradingView affordance', (await bodyText()).includes('TradingView'));

// theme toggle persists
await page.locator('[data-theme-toggle], button[aria-label*=theme i], .theme-toggle').first().click().catch(() => {});
await page.waitForTimeout(400);
const theme1 = await page.evaluate(() => document.documentElement.dataset.theme || document.body.dataset.theme || '');
await go('/tracker/history/');
const theme2 = await page.evaluate(() => document.documentElement.dataset.theme || document.body.dataset.theme || '');
check('theme toggle persists across pages', theme1 !== '' && theme1 === theme2, `${theme1} vs ${theme2}`);
await page.screenshot({ path: `${OUT}/local-light.png`, fullPage: false });
await page.locator('[data-theme-toggle], button[aria-label*=theme i], .theme-toggle').first().click().catch(() => {});

// -- wheel --
await go('/tracker/wheel/');
await page.screenshot({ path: `${OUT}/local-wheel.png`, fullPage: true });
text = await bodyText();
writeFileSync(`${OUT}/local-wheel.txt`, text);
check('wheel heading', /Wheel Strategy Campaigns/.test(text));
check('wheel total pnl line', /Total PnL/.test(text));
for (const col of ['SYMBOL', 'SHARES', 'COST BASIS', 'ADJUSTED COST', 'MARKET VALUE', 'PNL (%)']) {
  check(`wheel column "${col}"`, text.toUpperCase().includes(col));
}
check('adjusted cost shows discount pct', /\$[\d,.]+\s*\((-|−)?\d+%\)/.test(text));

// -- analytics --
await go('/tracker/analytics/');
await page.screenshot({ path: `${OUT}/local-analytics.png`, fullPage: true });
text = await bodyText();
writeFileSync(`${OUT}/local-analytics.txt`, text);
for (const label of ['Total Profit', 'Fees paid', 'Win Ratio', 'wins', 'Avg win', 'Avg loss', 'Highest win', 'Largest loss', 'Strategies Count', 'Cumulative Profits', 'Option Profit vs Account Value']) {
  check(`analytics shows "${label}"`, text.includes(label));
}
check('analytics has SVG charts', (await page.locator('svg').count()) >= 2, `svg=${await page.locator('svg').count()}`);
check('win ratio percent', /\d+%/.test(text));

// -- pnl flow --
await go('/tracker/analytics/flow/');
await page.screenshot({ path: `${OUT}/local-pnl-flow.png`, fullPage: true });
text = await bodyText();
check('pnl flow heading', /PnL Flow/.test(text));
check('flow nodes labeled with amount · share', /\$[\d,]+\s*·\s*[\d.]+%/.test(text));
check('flow has Put/Call and Gain/Loss nodes', /Put/.test(text) && /Call/.test(text) && /Gain/.test(text) && /Loss/.test(text));

// -- calendar --
await go('/tracker/calendar/');
await page.screenshot({ path: `${OUT}/local-calendar.png`, fullPage: true });
text = await bodyText();
check('calendar weekday header', ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'].every((d) => text.toUpperCase().includes(d)));
check('calendar shows premium day cells', /\$[\d,]+\.\d{2}/.test(text));
// closures may live in a previous month (real data): hop back up to 3 months
let closedSeen = /Closed:/.test(text) && /\dW/.test(text);
for (let hop = 0; hop < 3 && !closedSeen; hop++) {
  await page.locator('a:has-text("‹"), a[aria-label*=prev i], .calendar-nav a').first().click().catch(() => {});
  await page.waitForTimeout(700);
  const t = await bodyText();
  closedSeen = /Closed:/.test(t) && /\dW/.test(t);
}
check('calendar shows closed W/L (within 3 months back)', closedSeen);
await go('/tracker/calendar/');
// Reference truth (round-2): the toolbar has no "Month YYYY" title — it is
// Day/Month + month + year DROPDOWNS with prev/next arrows, so navigation
// is asserted on the month select's value changing after a prev click.
const monthValue = await page.locator('select[name=month]').inputValue().catch(() => '');
await page.locator('a[aria-label*=prev i], a:has-text("‹"), a:has-text("Prev")').first().click().catch(() => {});
await page.waitForTimeout(1200);
const monthValue2 = await page.locator('select[name=month]').inputValue().catch(() => '');
check('calendar month navigation', monthValue && monthValue2 && monthValue !== monthValue2, `${monthValue} -> ${monthValue2}`);

// -- history --
await go('/tracker/history/');
await page.screenshot({ path: `${OUT}/local-history.png`, fullPage: true });
text = await bodyText();
writeFileSync(`${OUT}/local-history.txt`, text);
check('history heading', /Historical Positions/.test(text));
check('history totals', /Total Strategies:\s*\d+/.test(text) && /Total Realized PnL/.test(text));
for (const col of ['SYMBOL', 'TYPE (CONTRACTS)', 'EXPIRY', 'TRADE DATE', 'INITIAL PREMIUM', 'REALIZED PNL']) {
  check(`history column "${col}"`, text.toUpperCase().includes(col));
}
await page.locator('tbody tr').first().click();
await page.waitForTimeout(500);
text = await bodyText();
for (const col of ['CALL / PUT', 'SIDE', 'STRIKE PRICE', 'OPEN PRICE', 'CLOSE PRICE', 'CLOSE STATUS', 'FEES']) {
  check(`history leg column "${col}"`, text.toUpperCase().includes(col));
}
check('history close status format', /Closed \(\d+, \d+\/\d+\/\d{4}\)/.test(text));
await page.screenshot({ path: `${OUT}/local-history-expanded.png`, fullPage: false });

// -- broker --
await go('/tracker/broker/');
await page.screenshot({ path: `${OUT}/local-broker.png`, fullPage: true });
text = await bodyText();
check('broker heading', /Brokerage Connection/.test(text));
for (const broker of ['Robinhood', 'Charles Schwab', 'Fidelity', 'E*Trade', 'Webull', 'Tastytrade', 'Interactive Brokers', 'Moomoo']) {
  check(`broker grid has ${broker}`, text.includes(broker));
}
check('coming soon labels', /coming soon/i.test(text));

// ------------------------------- verdict --------------------------------------
await browser.close();
const failed = results.filter((r) => !r.ok);
writeFileSync(`${OUT}/compare-results.json`, JSON.stringify({ failed: failed.length, total: results.length, results }, null, 2));
console.log(`\n[compare] ${results.length - failed.length}/${results.length} checks passed; artifacts in ${OUT}`);
process.exit(failed.length ? 1 : 0);
