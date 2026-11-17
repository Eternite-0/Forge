// Chromium device metrics emulation, not native Windows/Tauri DPI validation.
// All Core responses are synthetic and read-only; unmatched traffic is blocked.
async (page, { outputDir }) => {
  const checks = [], measurements = [], missing = [], writes = [], external = [];
  const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const base = process.env.FORGE_TEST_URL || 'http://127.0.0.1:5173/';
  const origin = new URL(base).origin;
  const longWorkspace = `C:/Fixture/${'long-workspace-directory/'.repeat(10)}FORGE`;
  const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (url.origin === origin && method === 'GET') return route.continue();
    if (url.origin !== 'http://127.0.0.1:8765') { external.push(`${method} ${url.origin}${url.pathname}`); return route.abort('blockedbyclient'); }
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (method !== 'GET') { writes.push(`${method} ${url.pathname}`); return route.fulfill({ status: 405, headers, json: { detail: 'Read-only scaling fixture' } }); }
    let json;
    if (url.pathname === '/health') json = { session: 'scaling-fixture' };
    else if (url.pathname === '/api/settings') json = { revision: 1, config: {}, workspace_id: 'scaling-workspace', workspace_root: longWorkspace, provider_configured: true, provider_id: 'synthetic-provider', router_model_id: 'synthetic-model', context_limit: 32768 };
    else if (url.pathname === '/api/restore-status') json = { fenced: false };
    else if (url.pathname === '/api/conversation') json = { messages: [], router: {}, has_more: false };
    else if (['/api/quests', '/api/timeline', '/api/approvals/pending', '/api/activity'].includes(url.pathname)) json = { items: [], has_more: false };
    else if (url.pathname === '/api/conversations/main/subagents') json = { conversation_id: 'main', workspace_id: 'scaling-workspace', items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/runtime/health') json = { components: [] };
    else if (url.pathname === '/api/projects/discovered') json = [];
    else if (url.pathname === '/api/budget') json = { token_limit: 10000, concurrency_limit: 2 };
    else { missing.push(url.pathname); return route.fulfill({ status: 404, headers, json: { detail: 'Unknown scaling fixture route' } }); }
    return route.fulfill({ headers, json });
  });
  const cdp = await page.context().newCDPSession(page);
  const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.goto(base);
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.locator('.simple-connection').waitFor({ state: 'hidden' });
  await page.keyboard.press('Control+,');
  const nav = page.getByRole('navigation', { name: '设置导航' });
  await nav.waitFor();
  await nav.getByRole('button', { name: '常规', exact: true }).click();
  await page.locator('.general-settings .settings-row').first().waitFor();
  const viewports = [{ width: 1440, height: 900 }, { width: 818, height: 582 }, { width: 390, height: 844 }, { width: 320, height: 720 }];
  try {
    for (const scale of [1, 1.25, 1.5]) for (const viewport of viewports) for (const theme of ['light', 'dark', 'forced']) {
      const label = `DPR${scale} ${viewport.width}x${viewport.height} ${theme}`;
      await page.setViewportSize(viewport);
      await cdp.send('Emulation.setDeviceMetricsOverride', { ...viewport, deviceScaleFactor: scale, mobile: false });
      await page.emulateMedia({ colorScheme: theme === 'light' ? 'light' : 'dark', forcedColors: theme === 'forced' ? 'active' : 'none', reducedMotion: 'reduce' });
      await page.waitForFunction(({ width, height, scale }) => innerWidth === width && innerHeight === height && Math.abs(devicePixelRatio - scale) < .01, { ...viewport, scale });
      await settle();
      const geometry = await page.evaluate(() => {
        const content = document.querySelector('.settings-content'), nav = document.querySelector('.settings-nav');
        const card = document.querySelector('.general-settings .settings-group');
        const rect = element => { const r = element.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; };
        return { dpr: devicePixelRatio, viewport: innerWidth, documentWidth: document.documentElement.scrollWidth, content: rect(content), contentScrollWidth: content.scrollWidth, contentClientWidth: content.clientWidth, nav: rect(nav), card: rect(card), background: getComputedStyle(content).backgroundColor };
      });
      check(geometry.documentWidth <= viewport.width + 1 && geometry.contentScrollWidth <= geometry.contentClientWidth + 1 && geometry.content.left >= -1 && geometry.content.right <= viewport.width + 1, `${label}: settings stay inside the logical viewport without horizontal overflow`);
      check(geometry.card.width > 100 && geometry.card.width <= geometry.content.width + 1 && geometry.content.width >= (viewport.width <= 390 ? viewport.width - 50 : 300), `${label}: actual settings cards retain usable content width`);
      if (viewport.width <= 390) {
        check(await page.locator('.general-settings .settings-row-value').first().evaluate((element, path) => element.textContent === path && element.scrollWidth <= element.clientWidth + 1, longWorkspace), `${label}: full long workspace path wraps without clipping or horizontal spill`);
      }
      await nav.getByRole('searchbox', { name: '搜索设置', exact: true }).focus();
      await settle();
      if (theme === 'forced') {
        check(await nav.locator('.settings-search').evaluate(element => { const style = getComputedStyle(element); return style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 1; }), `${label}: forced-color search focus has a visible outline rather than a suppressed shadow`);
      }
      for (let index = 0; index < 7; index++) await page.keyboard.press('Tab');
      await settle();
      const focusMetrics = async name => nav.getByRole('button', { name, exact: true }).evaluate(element => {
        const r = element.getBoundingClientRect(), strip = element.closest('.settings-nav-sections').getBoundingClientRect(), style = getComputedStyle(element);
        return { focused: document.activeElement === element, visible: r.left >= Math.max(0, strip.left) - 1 && r.right <= Math.min(innerWidth, strip.right) + 1 && r.top >= Math.max(0, strip.top) - 1 && r.bottom <= Math.min(innerHeight, strip.bottom) + 1, outline: style.outlineStyle, outlineWidth: parseFloat(style.outlineWidth), shadow: style.boxShadow };
      });
      const triggerFocus = await focusMetrics('触发器');
      check(triggerFocus.focused && triggerFocus.visible, `${label}: forward Tab reveals the entire focused settings button`);
      check((triggerFocus.outline !== 'none' && triggerFocus.outlineWidth > 0) || triggerFocus.shadow !== 'none', `${label}: focused settings entry has a visible focus indicator`);
      await page.keyboard.press('Shift+Tab'); await settle();
      const previousFocus = await focusMetrics('记忆');
      check(previousFocus.focused && previousFocus.visible, `${label}: reverse Tab preserves order and visibility`);
      if (theme === 'forced') {
        check(await nav.getByRole('button', { name: '常规', exact: true }).evaluate(element => getComputedStyle(element).outlineStyle !== 'none'), `${label}: forced colors preserve a non-color-only current-section marker`);
      } else {
        const contrast = await page.locator('.general-settings .settings-row-desc').first().evaluate(element => {
          const luminance = value => {
            const channels = value.match(/[\d.]+/g).slice(0, 3).map(Number).map(channel => { const c = channel / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; });
            return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
          };
          const text = luminance(getComputedStyle(element).color), background = luminance(getComputedStyle(element.closest('.settings-group')).backgroundColor);
          return (Math.max(text, background) + .05) / (Math.min(text, background) + .05);
        });
        check(contrast >= 4.5, `${label}: real settings description has at least 4.5:1 contrast`);
      }
      measurements.push({ label, ...geometry, triggerFocus });
      if (process.env.FORGE_CAPTURE_UI === '1' && (viewport.width === 320 || viewport.width === 1440) && scale !== 1.25) {
        // Capture directly through this CDP session. Playwright's screenshot
        // helper can restore its context DPR and invalidate emulated metrics.
        const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
        process.getBuiltinModule('fs').writeFileSync(`${outputDir}/settings-scaling-${scale}-${viewport.width}-${theme}.png`, Buffer.from(screenshot.data, 'base64'));
      }
    }
    check(writes.length === 0 && missing.length === 0 && external.length === 0, `scaling and focus are read-only with complete local fixtures: ${JSON.stringify({ writes, missing, external })}`);
    return { checks, states: measurements.length, evidence: 'Chromium emulated device pixel ratio and logical viewports; not Windows OS scaling or native Tauri validation', measurements };
  } finally {
    await cdp.send('Emulation.clearDeviceMetricsOverride');
    await cdp.detach();
  }
}
