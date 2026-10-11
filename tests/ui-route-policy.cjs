const { chromium } = require('playwright');
const { execFileSync } = require('node:child_process');

function render(snapshot, fragments = false) {
  const code = fragments
    ? 'import json,sys; from proxy_xray.dashboard_view import dashboard_v5_fragments; print(json.dumps({"fragments":dashboard_v5_fragments(json.loads(sys.argv[1]))}))'
    : 'from proxy_xray.dashboard_view import render_dashboard_v5_html; print(render_dashboard_v5_html().decode())';
  return execFileSync(process.env.PYTHON || 'python3', ['-c', code, JSON.stringify(snapshot)], { encoding: 'utf8' });
}

(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let loads = 0;
    page.on('load', () => loads++);
    let policy = 'auto';
    let command = null;
    const requests = [];
    await page.route('http://dashboard.test/**', async route => {
      if (route.request().url().endsWith('/control/route-policy')) {
        policy = route.request().postDataJSON().policy;
        requests.push(policy);
        command = { status: 'done', payload: { policy } };
        await route.fulfill({ status: 202, contentType: 'application/json', body: '{"id":"synthetic","status":"queued"}' });
      } else if (route.request().url().endsWith('/fragments/dashboard-v5')) {
        await route.fulfill({ contentType: 'application/json', body: render({
          route_mode: 'chained_amsterdam', route_policy: policy, route_policy_command: command,
          route_transition: { direct_recovery: { stable_seconds: 20 } },
        }, true) });
      } else {
        await route.fulfill({ contentType: 'text/html', body: render({}) });
      }
    });
    for (const [name, width, height] of [['desktop', 1440, 1000], ['mobile', 390, 844]]) {
      await page.setViewportSize({ width, height });
      await page.goto('http://dashboard.test/');
      const baseline = loads;
      await page.locator('[data-route-policy="subscription_only"]').click();
      await page.waitForFunction(() => document.querySelector('.v5-policy').dataset.commandStatus === 'done'
        && document.querySelector('[data-route-policy="subscription_only"]').getAttribute('aria-pressed') === 'true');
      if (await page.locator('[data-fragment="v5-current"]').textContent().then(text => text.includes('Direct stable'))) {
        throw new Error('Direct guard is visible under subscription policy');
      }
      // The current fragment was replaced; the delegated handler must still work.
      await page.locator('[data-route-policy="auto"]').click();
      await page.waitForFunction(() => document.querySelector('[data-route-policy="auto"]').getAttribute('aria-pressed') === 'true'
        && document.querySelector('.v5-policy-feedback').textContent.includes('применён'));
      await page.locator('[data-route-policy="subscription_only"]').click();
      await page.waitForFunction(() => document.querySelector('.v5-policy').dataset.commandStatus === 'done'
        && document.querySelector('[data-route-policy="subscription_only"]').getAttribute('aria-pressed') === 'true');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth
        || [...document.querySelectorAll('.v5-policy-options button')].some(node => node.scrollWidth > node.clientWidth + 1));
      if (overflow || loads !== baseline) throw new Error('Layout overflow or full-page reload');
      await page.screenshot({ path: `/tmp/proxy-policy-${name}.png`, fullPage: true });
      console.log(JSON.stringify({ viewport: name, switchAfterFragmentReplacement: true, overflow, reloads: loads - baseline }));
    }
    if (errors.length || requests.length !== 6) throw new Error(JSON.stringify({ errors, requests }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
