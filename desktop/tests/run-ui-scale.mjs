import { mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Reuse the installed browser runtime. DPR emulation is not Windows system-DPI validation.
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.FORGE_PLAYWRIGHT_MODULE || 'playwright');
const output = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../output/scale-matrix');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const checks = [], cases = [], screenshots = [], pageErrors = [];
const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
const near = (value, expected, label, tolerance = 1) => check(Math.abs(value - expected) <= tolerance, `${label}: ${value} vs ${expected}`);
const matrix = [
  { id: 'css-1440-dpr100', width: 1440, height: 900, dpr: 1, kind: 'fixed CSS viewport' },
  { id: 'css-1440-dpr125', width: 1440, height: 900, dpr: 1.25, kind: 'fixed CSS viewport' },
  { id: 'css-1440-dpr150', width: 1440, height: 900, dpr: 1.5, kind: 'fixed CSS viewport' },
  { id: 'physical-1440-dpr125', width: 1152, height: 720, dpr: 1.25, kind: '1440×900 physical-pixel equivalent' },
  { id: 'physical-1440-dpr150', width: 960, height: 600, dpr: 1.5, kind: '1440×900 physical-pixel equivalent' },
  { id: 'reference-2796-dpr125', width: 2237, height: 984, dpr: 1.25, kind: 'approximately user screenshot physical dimensions; screenshot DPI metadata absent' },
  { id: 'minimum-800-dpr150', width: 800, height: 600, dpr: 1.5, kind: 'minimum CSS desktop window' },
];
const metrics = async (page, selectors) => page.evaluate((selectors) => {
  const result = { viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio }, document: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight } };
  for (const [key, selector] of Object.entries(selectors)) {
    const element = document.querySelector(selector);
    if (!element) { result[key] = null; continue; }
    const bounds = element.getBoundingClientRect(), style = getComputedStyle(element);
    result[key] = { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height,
      fontSize: style.fontSize, lineHeight: style.lineHeight, fontWeight: style.fontWeight, fontFamily: style.fontFamily,
      color: style.color, background: style.backgroundColor, borderWidth: style.borderTopWidth, borderColor: style.borderTopColor,
      radius: style.borderRadius, padding: style.padding, overflowX: style.overflowX, overflowY: style.overflowY,
      transition: style.transition, display: style.display, scrollHeight: element.scrollHeight, clientHeight: element.clientHeight };
  }
  return result;
}, selectors);
const shot = async (page, name) => {
  await page.mouse.move(0, 0); await page.waitForTimeout(220);
  await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true }); screenshots.push(`${name}.png`);
};
const bounded = async (page, locator, label) => {
  const bounds = await locator.boundingBox();
  check(bounds && bounds.x >= -.5 && bounds.y >= -.5 && bounds.x + bounds.width <= (await page.evaluate(() => innerWidth)) + .5 && bounds.y + bounds.height <= (await page.evaluate(() => innerHeight)) + .5, label);
};
const sourceName = 'implementation-with-a-very-long-file-name-to-check-tab-truncation.ts';
const source = Array.from({ length: 1200 }, (_, index) => `export const matrixLine${index} = "${index}: 真实 File API 内容 — 中文与Latin";`).join('\n');
const command = 'Get-Content backend/src/forge_core/worker_runtime.py | Select-Object -Skip 120 -First 40';
const receipt = { kind: 'tool_receipt', event_id: 'matrix-command', seq: null, created_at: '2026-01-01T00:00:02Z', quest_id: null, title: 'Tool execution recorded', status: 'succeeded', execution_mode: 'mock', detail: { tool: 'shell_inspect', command, stdout: Array.from({ length: 45 }, (_, index) => `Synthetic output ${index} ${'long content '.repeat(9)}`).join('\n'), exit_code: 0 } };
const fixtures = async (page) => {
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (request.method() !== 'GET') throw new Error(`Matrix must never write: ${url.pathname}`);
    let body = {};
    if (url.pathname === '/health') body = { session: 'scale-matrix-only' };
    else if (url.pathname === '/api/conversation') body = { messages: [
      { id: 'matrix-user', role: 'user', content: '核对布局与文件预览。\n这组数据只用于浏览器缩放验证。', created_at: '2026-01-01T00:00:01Z', metadata: {} },
      { id: 'matrix-agent', role: 'assistant', content: '已核对当前结构。\n\n这里用相同内容比较不同栅格比例，检查长路径、命令与设置字段是否仍能阅读。\n\n**测试数据**只表示界面状态，不代表实际 Core 或供应商执行。', created_at: '2026-01-01T00:00:03Z', metadata: { execution_mode: 'mock', model_id: 'scale-matrix-model', provider_id: 'scale-matrix-provider' } },
    ], has_more: false };
    else if (url.pathname === '/api/timeline') body = { items: [receipt], has_more: false };
    else if (url.pathname === '/api/settings') body = { revision: 0, config: { provider: { id: 'scale-matrix-provider', kind: 'openai_compatible', base_url: 'https://example.invalid/v1', credential_ref: 'FORGE_PROVIDER_KEY_EXAMPLE', supports_tools: true, supports_json_schema: true }, router_model_id: 'scale-matrix-model', router_context_limit: 16384, model_token_limit: 100000, model_concurrency_limit: 2, network_hosts: [] }, context_limit: 16384, provider_configured: true, provider_id: 'scale-matrix-provider', router_model_id: 'scale-matrix-model', restore_fenced: false, environment_overrides: [], workspace_id: 'scale-matrix', workspace_root: 'D:/Scale-matrix/带有较长中文名称的实际界面测试工作空间' };
    else if (url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/budget') body = { token_limit: 100000, concurrency_limit: 2 };
    else if (url.pathname === '/api/quests' || url.pathname === '/api/approvals/pending' || url.pathname === '/api/activity') body = { items: [], has_more: false };
    else throw new Error(`Missing scale fixture: ${url.pathname}`);
    return route.fulfill({ status: 200, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
};
let reference;
try {
  const referenceContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await referenceContext.newPage();
  await page.goto('http://127.0.0.1:8890/#/settings');
  await page.locator('#settings-search').waitFor();
  reference = await metrics(page, { sidebar: '.app-shell-left-panel', rail: '[data-app-navigation-rail]', nav: '#settings-view [style="width:288px"]', inner: '#settings-view [style="max-width:760px"]', navTitle: '#settings-view h1.heading-xl', title: '#settings-view .titleContent-nEpGod h1', item: '[data-section="general"]', row: '[class*="@container/settings-row"]', label: '[class*="@container/settings-row"] [style*="font-weight:500"]', description: '[class*="@container/settings-row"] [style*="font-size:12px"]' });
  const referenceSession = await referenceContext.newCDPSession(page);
  await referenceSession.send('DOM.enable'); await referenceSession.send('CSS.enable');
  const referenceRoot = await referenceSession.send('DOM.getDocument');
  const referenceTitle = await referenceSession.send('DOM.querySelector', { nodeId: referenceRoot.root.nodeId, selector: '#settings-view .titleContent-nEpGod h1' });
  reference.platformFonts = await referenceSession.send('CSS.getPlatformFontsForNode', { nodeId: referenceTitle.nodeId });
  await referenceSession.detach();
  await shot(page, 'reference-settings-1440');
  await referenceContext.close();
  for (const entry of matrix) {
    const context = await browser.newContext({ viewport: { width: entry.width, height: entry.height }, deviceScaleFactor: entry.dpr });
    const page = await context.newPage();
    page.on('pageerror', (error) => pageErrors.push({ case: entry.id, error: error.message }));
    await fixtures(page);
    await page.goto('http://127.0.0.1:5173/');
    await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
    await page.getByRole('button', { name: '连接', exact: true }).click();
    const group = page.locator('.event-group');
    await group.locator(':scope > summary').click();
    await page.locator('.event-line > summary').click();
    await page.waitForFunction(() => document.querySelector('.event-line > .detail-collapsible')?.style.height === 'auto');
    for (const theme of ['light', 'dark']) {
      await page.evaluate((theme) => document.documentElement.dataset.theme = theme, theme);
      await page.getByLabel('主导航').getByRole('button', { name: '对话', exact: true }).click();
      await page.waitForTimeout(220);
      const prefix = `${entry.id}-${theme}`, row = { ...entry, theme };
      row.chat = await metrics(page, { rail: '.primary-rail', sidebar: '.sidebar-panel', chrome: '.desktop-chrome', shell: '.conversation-shell', timeline: '.timeline', assistant: '.message.from-agent', assistantBody: '.message.from-agent .message-body', userBody: '.message.from-user .message-body', summary: '.event-group > summary', icon: '.event-group > summary > svg', command: '.command-card', output: '.command-card .out', composer: '.composer', send: '.send-button' });
      if (entry.id === 'css-1440-dpr100' && theme === 'light') {
        const session = await context.newCDPSession(page);
        await session.send('DOM.enable'); await session.send('CSS.enable');
        const { root } = await session.send('DOM.getDocument');
        const { nodeId } = await session.send('DOM.querySelector', { nodeId: root.nodeId, selector: '.message.from-agent .markdown p' });
        row.platformFonts = await session.send('CSS.getPlatformFontsForNode', { nodeId });
        await session.detach();
      }
      near(row.chat.viewport.dpr, entry.dpr, `${prefix}: DPR is explicit`, .01);
      near(row.chat.rail.width, 52, `${prefix}: icon rail`);
      near(row.chat.sidebar.width, 288, `${prefix}: workspace panel`);
      near(row.chat.chrome.height, 36, `${prefix}: desktop chrome`);
      near(row.chat.composer.width, Math.min(736, row.chat.shell.width - 48), `${prefix}: centered composer width`);
      near(row.chat.composer.x + row.chat.composer.width / 2, row.chat.shell.x + row.chat.shell.width / 2, `${prefix}: composer center`);
      near(row.chat.assistant.width, Math.min(736, row.chat.timeline.width - 48), `${prefix}: assistant column width`);
      near(row.chat.summary.height, 21, `${prefix}: summary row height`);
      near(parseFloat(row.chat.summary.fontSize), 14, `${prefix}: summary typography`);
      near(row.chat.icon.width, 16, `${prefix}: summary icon`);
      near(parseFloat(row.chat.command.radius), 12.5, `${prefix}: command radius`, .1);
      check(row.chat.output.height <= 144.5 && row.chat.output.scrollHeight > row.chat.output.clientHeight, `${prefix}: output is independently bounded`);
      check(row.chat.document.width <= entry.width && row.chat.document.height <= entry.height, `${prefix}: chat has no document overflow`);
      await bounded(page, page.getByLabel('消息内容'), `${prefix}: composer input remains in window`);
      await shot(page, `${prefix}-chat`);
      await page.getByRole('button', { name: '查看工作空间文件', exact: true }).click();
      await page.getByLabel('选择预览文件', { exact: true }).setInputFiles([{ name: sourceName, mimeType: 'text/plain', buffer: Buffer.from(source) }]);
      const preview = page.getByLabel('工作空间文件预览', { exact: true });
      await preview.locator('.file-tree-row').click();
      await preview.getByLabel(`${sourceName} 源码`, { exact: true }).waitFor();
      await preview.getByLabel(`${sourceName} 源码`, { exact: true }).evaluate((element) => element.scrollTop = 15000);
      await page.waitForTimeout(220);
      row.preview = await metrics(page, { panel: '.file-preview-workspace', content: '.preview-content', source: '.source-lines', tree: '.preview-file-tree', divider: '.preview-divider', tab: '.preview-tab' });
      check(await preview.locator('.source-line:visible').count() <= 160, `${prefix}: long source stays virtualized`);
      check((await page.locator('.conversation-shell').isVisible()) === (entry.width > 1000), `${prefix}: preview uses one pane below breakpoint`);
      await bounded(page, preview.getByRole('button', { name: '关闭文件预览', exact: true }), `${prefix}: preview close remains reachable`);
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${prefix}: long source/tab cannot overflow document`);
      await shot(page, `${prefix}-preview`);
      await preview.getByRole('button', { name: '关闭文件预览', exact: true }).click();
      await preview.waitFor({ state: 'hidden' });
      await page.keyboard.press('Control+,');
      await page.locator('.settings-title').waitFor();
      await page.waitForTimeout(220);
      row.settings = await metrics(page, { rail: '.rail', nav: '.settings-nav', content: '.settings-content', inner: '.settings-content-inner', title: '.settings-title', item: '.settings-nav-item.active', row: '.settings-row', label: '.settings-row-label', description: '.settings-row-desc' });
      near(row.settings.rail.width, 52, `${prefix}: settings hides workspace panel`);
      near(row.settings.nav.width, entry.width > 1000 ? 288 : 224, `${prefix}: settings navigation width`);
      near(parseFloat(row.settings.title.fontSize), 28, `${prefix}: settings heading font`);
      near(parseFloat(row.settings.title.lineHeight), 33.6, `${prefix}: settings heading line`);
      near(parseFloat(row.settings.label.fontSize), 13, `${prefix}: settings row label`);
      near(parseFloat(row.settings.description.fontSize), 12, `${prefix}: settings row description`);
      check(row.settings.document.width <= entry.width && row.settings.document.height <= entry.height, `${prefix}: general settings has no document overflow`);
      await shot(page, `${prefix}-settings`);
      await page.getByLabel('设置导航').getByRole('button', { name: '环境', exact: true }).click();
      await page.getByLabel('服务地址', { exact: true }).waitFor();
      await page.getByRole('button', { name: '保存配置', exact: true }).scrollIntoViewIfNeeded();
      await bounded(page, page.getByRole('button', { name: '保存配置', exact: true }), `${prefix}: long settings can reach its save control without submitting`);
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight), `${prefix}: long settings scroll internally`);
      await shot(page, `${prefix}-settings-long`);
      cases.push(row);
    }
    check(await page.evaluate(() => localStorage.length === 0 && sessionStorage.length === 0), `${entry.id}: no persistent browser storage`);
    await context.close();
  }
  check(pageErrors.length === 0, 'no uncaught page errors across scale/theme matrix');
  const report = { status: 'passed', method: 'Chromium deviceScaleFactor rasterization plus equivalent CSS window sizes. This does not change or validate Windows system DPI.', referenceImages: { width: 2796, height: 1230, scaleMetadata: 'absent; approximately 125% inferred from 52/288 CSS rail proportions, not asserted' }, source: 'synthetic authenticated HTTP projections and real File API bytes; no actual Core/provider execution', checks: checks.length, cases, reference, screenshots, pageErrors };
  await writeFile(path.join(output, 'matrix.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, checks: checks.length, cases: cases.length, screenshots: screenshots.length, pageErrors: pageErrors.length, method: report.method, report: path.join(output, 'matrix.json') }, null, 2));
} catch (error) {
  await writeFile(path.join(output, 'failure.json'), JSON.stringify({ error: String(error), checks, cases, pageErrors }, null, 2));
  throw error;
} finally { await browser.close(); }
