// Synthetic activity records only; no commands, semantic checks or provider requests execute.
async (page, { outputDir }) => {
  const checks = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  const command = 'python inspect.py ' + Array.from({ length: 24 }, (_, index) => `--source-${index}=very-long-synthetic-path/${index}/file.json`).join(' ');
  const events = [
    { kind: 'event', event_id: 'routine-plan-approved', seq: 1, created_at: '2026-01-01T00:00:00Z', quest_id: null, title: 'Plan approved', status: 'quest.plan_approved', detail: {}, execution_mode: 'mock' },
    { kind: 'tool_receipt', event_id: 'long-failed-command', seq: 2, created_at: '2026-01-01T00:00:01Z', quest_id: null, title: 'Tool execution recorded', status: 'failed', detail: { tool: 'shell_inspect', command, exit_code: 23, stderr: 'Synthetic command failure, no actual command ran.' }, execution_mode: 'mock' },
    { kind: 'semantic_test', event_id: 'semantic-failed', seq: 3, created_at: '2026-01-01T00:00:02Z', quest_id: null, title: 'Semantic check result', status: 'failed', detail: { result: 'synthetic failed semantic check' }, execution_mode: 'mock' },
    { kind: 'semantic_test', event_id: 'semantic-unknown', seq: 4, created_at: '2026-01-01T00:00:03Z', quest_id: null, title: 'Semantic check result', status: 'unknown', detail: { result: 'synthetic unknown semantic check' }, execution_mode: 'mock' },
    { kind: 'tool_receipt', event_id: 'short-success-command', seq: 5, created_at: '2026-01-01T00:00:03.500Z', quest_id: null, title: 'Tool execution recorded', status: 'succeeded', detail: { tool: 'shell_inspect', command: 'git status --short', exit_code: 0, stdout: ' M desktop/src/App.tsx\n M desktop/src/style.css' }, execution_mode: 'mock' },
  ];
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    let body, status = 200;
    if (request.method() !== 'GET') { status = 400; body = { detail: 'risk fixture is read-only' }; }
    else if (url.pathname === '/health') body = { session: 'risk-fixture-generation' };
    else if (url.pathname === '/api/conversation') body = { messages: [{ id: 'risk-message', role: 'assistant', content: '合成状态记录，用于检查风险信息是否清楚可见。', created_at: '2026-01-01T00:00:04Z', metadata: { execution_mode: 'mock' } }], next_before: null, has_more: false };
    else if (url.pathname === '/api/timeline') body = { items: events, next_before: null, has_more: false };
    else if (['/api/quests', '/api/approvals/pending', '/api/activity'].includes(url.pathname)) body = { items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/settings') body = { revision: 0, config: null, context_limit: 8192, restore_fenced: false, environment_overrides: [] };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/budget') body = { token_limit: 0, concurrency_limit: 1 };
    else if (url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/projects/discovered') body = [];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/memory') body = { items: [], cursor: null, omitted: 0, bytes: 0 };
    else { status = 404; body = { detail: `risk fixture endpoint missing: ${url.pathname}` }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  const process = page.locator('.process-transcript'), processSummary = process.locator(':scope > summary');
  await processSummary.waitFor();
  check((await processSummary.innerText()).includes('含失败记录') && (await processSummary.innerText()).includes('含未知结果') && (await processSummary.innerText()).includes('含模拟记录'), 'collapsed process preserves failure, unknown and mock provenance before opening its tools');
  await processSummary.click();
  const group = page.locator('.process-content > .process-tool-summary'), summary = group.locator(':scope > summary');
  await summary.waitFor();
  check((await summary.innerText()).includes('独立语义验证失败') && (await summary.innerText()).includes('独立语义验证结果未知') && !(await summary.innerText()).includes('验证通过'), 'failed and unknown semantic checks never become a passed headline');
  check(!(await summary.innerText()).includes('计划已批准') && await group.locator('.event-line').count() === 5, 'routine lifecycle information stays available without cluttering the work summary');
  check(await summary.locator('.event-risk').count() === 2 && (await summary.locator('.event-risk').allTextContents()).join('|') === '失败|结果未知', 'collapsed activity exposes both failure and unknown status separately from its text');
  await summary.click();
  const commandLine = page.locator('[data-scroll-key="event:tool_receipt:long-failed-command"]');
  const semanticFailed = page.locator('[data-scroll-key="event:semantic_test:semantic-failed"]');
  const semanticUnknown = page.locator('[data-scroll-key="event:semantic_test:semantic-unknown"]');
  await commandLine.locator(':scope > summary').waitFor();
  check((await semanticFailed.locator(':scope > summary').innerText()).includes('独立语义验证失败') && !(await semanticFailed.locator(':scope > summary').innerText()).includes('通过'), 'a failed semantic activity uses its actual failed label');
  check((await semanticUnknown.locator(':scope > summary').innerText()).includes('独立语义验证结果未知') && !(await semanticUnknown.locator(':scope > summary').innerText()).includes('通过'), 'an unknown semantic activity uses its actual unknown label');
  check((await commandLine.locator(':scope > summary .event-risk').innerText()).includes('失败（退出码 23）'), 'a long command retains its failure and exact exit code in the visible row');
  const risksFit = async () => page.locator('.event-group > summary, .event-line > summary').evaluateAll((summaries) => summaries.every((summary) => {
    const outer = summary.getBoundingClientRect();
    return [...summary.querySelectorAll(':scope > .event-risk')].every((risk) => {
      const box = risk.getBoundingClientRect(), style = getComputedStyle(risk);
      return box.width > 0 && box.left >= outer.left - 1 && box.right <= outer.right + 1 && risk.scrollWidth <= risk.clientWidth + 1 && style.flexShrink === '0';
    });
  }));
  check(await risksFit(), 'desktop risk labels remain fully inside every summary and never shrink or truncate');
  await commandLine.locator(':scope > summary').click();
  const commandButton = commandLine.locator('.command-card .cmd');
  await commandButton.waitFor();
  check(await commandLine.locator('.command-card > header').innerText() === 'Shell' && (await commandLine.locator('.command-result').innerText()).includes('退出码 23') && (await commandLine.locator('.command-result').innerText()).includes('模拟'), 'the Shell card exposes its recorded failure and mock execution mode');
  check(await commandButton.getAttribute('aria-label') === `$ ${command}` && await commandButton.getAttribute('aria-expanded') === 'false', 'a long command preserves its full accessible name and starts compact');
  await commandButton.click();
  check(await commandButton.getAttribute('aria-expanded') === 'true' && await commandButton.innerText() === command, 'the compact command expands to its complete original text');
  await commandButton.click();
  const successLine = page.locator('[data-scroll-key="event:tool_receipt:short-success-command"]');
  await successLine.locator(':scope > summary').click();
  const successfulCard = successLine.locator('.command-card');
  await successfulCard.waitFor();
  await page.waitForTimeout(250);
  check(await successfulCard.locator(':scope > header').innerText() === 'Shell' && await successfulCard.locator('.cmd').innerText() === 'git status --short' && (await successfulCard.locator('.out').innerText()).includes('desktop/src/App.tsx'), 'a successful Shell card displays its actual command and recorded output');
  check((await successfulCard.locator('.command-result').innerText()).includes('成功') && (await successfulCard.locator('.command-result').innerText()).includes('模拟') && await successfulCard.locator('.command-result svg').count() === 1, 'only a successful exit-zero command receives a check while its mock provenance stays visible');
  await successfulCard.scrollIntoViewIfNeeded();
  check(await successfulCard.evaluate((element) => { const card = element.getBoundingClientRect(), viewport = document.querySelector('.timeline').getBoundingClientRect(); return card.top >= viewport.top - 1 && card.bottom <= viewport.bottom + 1; }), 'the successful Shell screenshot captures the complete command card inside the conversation viewport');
  await page.mouse.move(0, 0);
  await page.screenshot({ path: `${outputDir}/simple-risk-status-desktop.png`, fullPage: true });
  await successLine.locator(':scope > summary').click();
  await commandLine.locator(':scope > summary').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(220);
  check(await commandLine.locator(':scope > summary .event-label').evaluate((element) => {
    const style = getComputedStyle(element), box = element.getBoundingClientRect();
    return style.whiteSpace === 'normal' && box.height > parseFloat(style.lineHeight) && element.scrollWidth <= element.clientWidth + 1;
  }), 'the narrow risk fixture wraps its complete long command label within the available width');
  check(await risksFit(), 'narrow activity rows preserve complete failure and unknown status beside wrapped command text');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'long command and semantic risk rows cause no horizontal document overflow');
  await page.mouse.move(0, 0);
  await page.screenshot({ path: `${outputDir}/simple-risk-status-small.png`, fullPage: true });
  return { status: 'passed', fixture: 'synthetic HTTP only', checks: checks.length };
}
