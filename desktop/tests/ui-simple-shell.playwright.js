// Desktop shell and compact task flow. All backend responses here are synthetic.
async (page, { outputDir }) => {
  const checks = [], posts = [], messages = [], events = [], readReceipts = [], unexpected = [];
  const capture = (name) => process.env.FORGE_CAPTURE_UI === '1' ? page.screenshot({ path: `${outputDir}/${name}.png`, animations: 'disabled' }) : Promise.resolve();
  const check = (ok, label) => { if (!ok) throw new Error(label); checks.push(label); };
  const id = '1234567890abcdef1234567890abcdef';
  const quest = { id, title: id, goal: '整理项目资料', status: 'running', version: 1 };
  const receiptId = 'abcdef1234567890abcdef1234567890';
  const time = '2026-11-16T08:00:00Z';
  let showQuest = false;
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request(), { pathname } = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    const method = request.method();
    if (method !== (pathname === '/api/conversation/message' ? 'POST' : 'GET')) {
      unexpected.push(`${method} ${pathname}`);
      return route.fulfill({ status: 405, headers, json: { detail: 'Unexpected fixture mutation' } });
    }
    let json;
    if (pathname === '/health') json = { session: 'simple-shell' };
    else if (pathname === '/api/settings') json = { revision: 1, config: {}, workspace_root: '/fixture/FORGE', provider_configured: true, router_model_id: 'fixture-model' };
    else if (pathname === '/api/restore-status') json = { fenced: false };
    else if (pathname === '/api/conversation') json = { messages, router: {}, has_more: false };
    else if (pathname === '/api/quests') json = { items: showQuest ? [quest] : [], has_more: false };
    else if (pathname === '/api/timeline') json = { items: events, has_more: false };
    else if (pathname === `/api/artifacts/receipts/${receiptId}/diff`) {
      readReceipts.push(pathname);
      json = { receipt_id: receiptId, base_receipt_id: 'fixture-base', quest_id: id, run_id: 'fixture-run', path: 'reports/summary.md',
        execution_mode: 'live', change: 'revised', created_at: time, sha256: 'a'.repeat(64), base_sha256: 'b'.repeat(64),
        added: 1, removed: 1, text: '--- a/reports/summary.md\n+++ b/reports/summary.md\n@@ -1 +1 @@\n-整理初稿\n+整理结果\n', truncated: false,
        origin: { original_request_message_id: 'user-fixture', goal_update_message_id: null } };
    }
    else if (pathname === `/api/quests/${id}`) json = { quest, workers: [] };
    else if ([`/api/quests/${id}/grants`, `/api/quests/${id}/plan/history`, `/api/quests/${id}/teams`, '/api/models', '/api/projects/discovered'].includes(pathname)) json = [];
    else if (pathname === `/api/quests/${id}/plan`) json = null;
    else if (['/api/approvals/pending', '/api/approvals', '/api/activity', '/api/memory'].includes(pathname)) json = { items: [], has_more: false };
    else if (pathname === '/api/runtime/health') json = { components: [] };
    else if (pathname === '/api/budget') json = { token_limit: 0, concurrency_limit: 1 };
    else if (pathname === '/api/conversation/message') {
      const posted = request.postDataJSON();
      posts.push(posted);
      const user_message = { id: 'user-fixture', role: 'user', content: posted.content, created_at: time, metadata: { request_id: posted.request_id } };
      const assistant_message = { id: 'assistant-fixture', role: 'assistant', content: '整理好了。', created_at: '2026-11-16T08:00:02Z' };
      messages.push(user_message, assistant_message);
      quest.status = 'completed';
      events.push({ kind: 'tool_receipt', event_id: receiptId, seq: 1, created_at: '2026-11-16T08:00:01Z', quest_id: id,
        status: 'succeeded', execution_mode: 'live', detail: { tool: 'file_write', path: 'reports/summary.md' } });
      json = { user_message, assistant_message };
    }
    if (json === undefined) {
      unexpected.push(`${method} ${pathname}`);
      return route.fulfill({ status: 404, headers, json: { detail: 'Unknown synthetic fixture endpoint' } });
    }
    return route.fulfill({ headers, json });
  });
  await page.setViewportSize({ width: 1626, height: 1022 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto(process.env.FORGE_TEST_URL || 'http://127.0.0.1:5173/');
  const composer = page.getByLabel('消息内容');
  const send = page.getByRole('button', { name: '发送消息', exact: true });
  const tasks = page.getByRole('button', { name: '查看任务', exact: true });
  const more = page.getByRole('button', { name: '更多对话操作' });
  const rail = page.getByLabel('主导航');
  check(await rail.isVisible() && await page.locator('.desktop-chrome').isVisible(), 'desktop has the workspace sidebar and titlebar controls');
  check(await rail.evaluate(element => element.querySelector('.sidebar-panel').getBoundingClientRect().width > 160), 'Windows project sidebar is visible beside the compact icon rail');
  check(await rail.getByRole('navigation', { name: '主要功能' }).getByRole('button', { name: '与 FORGE 对话', exact: true }).count() === 1, 'icon rail exposes one genuine conversation entry');
  const fileMenu = page.locator('.titlebar').getByRole('menuitem', { name: '文件', exact: true });
  await fileMenu.press('ArrowDown');
  await page.getByRole('menu', { name: '文件菜单' }).getByRole('menuitem', { name: '打开工作空间文件' }).waitFor();
  await page.keyboard.press('Escape');
  check(await composer.count() === 1 && await send.isDisabled(), 'single composer cannot send empty messages');
  check(await page.locator('.composer button').count() === 3, 'composer offers files, model settings and send');
  check(await page.evaluate(() => {
    const greeting = document.querySelector('.empty-conversation').getBoundingClientRect();
    const editor = document.querySelector('.composer').getBoundingClientRect();
    return editor.top > innerHeight * .72 && greeting.top < innerHeight * .56 && editor.top > greeting.bottom;
  }), 'the welcome and low composer occupy distinct reference bands');
  check(await page.locator('.composer-workspace-context').isVisible(), 'the welcome composer has a real workspace context strip');
  check(await page.locator('.composer-workspace-context').evaluate(element => element.getBoundingClientRect().bottom <= document.querySelector('.composer').getBoundingClientRect().top + 1), 'workspace context attaches above the Windows editor');
  check(await page.locator('.composer-suggestions').count() === 0, 'reference home has no fabricated starter cards');
  check(await page.getByRole('button', { name: '打开工作空间文件', exact: true }).isVisible() && await rail.getByRole('button', { name: '后台任务', exact: true }).isVisible(), 'empty home retains genuine file and task entry points');
  const initialViewport = page.viewportSize();
  await composer.fill('手动输入草稿');
  check(posts.length === 0 && await composer.inputValue() === '手动输入草稿', 'typing a draft does not send it');
  await composer.fill('');
  const ready = page.waitForResponse(response => new URL(response.url()).pathname === '/api/quests');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await ready;
  await page.locator('.simple-connection').waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: '打开对话模型配置', exact: true }).filter({ hasText: 'fixture-model' }).waitFor();
  await page.locator('.empty-conversation').waitFor();
  await capture('windows-home-connected-reference');
  showQuest = true;
  await page.setViewportSize(initialViewport);
  const progress = page.getByRole('region', { name: '当前任务进展' });
  await progress.getByText('运行中', { exact: true }).waitFor();
  check(await progress.locator('.task-progress-icon[data-state="running"] svg').count() === 1, 'a live task shows a distinct running glyph without a fabricated progress value');
  check(!(await progress.innerText()).includes(id), 'background status is visible in conversation without IDs');
  await composer.fill('替我整理项目资料');
  await tasks.click();
  const drawer = page.locator('.task-inspector');
  const taskRow = drawer.getByRole('button', { name: /^整理项目资料，/ });
  await taskRow.waitFor();
  check(!(await drawer.innerText()).includes(id), 'task list shows a human goal instead of the internal ID');
  check(await drawer.locator('.quest-detail').count() === 0, 'opening Tasks shows the list first');
  await taskRow.click();
  await drawer.locator('.quest-detail').waitFor();
  check(await drawer.locator('.quest-detail-heading').evaluate(element => element === document.activeElement), 'opening the task moves focus into its heading');
  check(!(await drawer.locator('.quest-technical').evaluate(element => element.open)), 'task technical details are closed by default');
  await drawer.locator('.quest-detail-heading').press('Escape');
  await taskRow.waitFor();
  check(await drawer.isVisible() && await taskRow.evaluate(element => element === document.activeElement), 'Escape goes back one level and restores the originating task row');
  await taskRow.click();
  await drawer.getByRole('button', { name: /返回任务列表/ }).click();
  check(await taskRow.evaluate(element => element === document.activeElement), 'clicking Back also restores the task row');
  await taskRow.press('Escape');
  await drawer.waitFor({ state: 'hidden' });
  await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '消息内容');
  check(await composer.evaluate(element => element === document.activeElement), 'Escape in Tasks restores focus to the message box');
  check(await composer.inputValue() === '替我整理项目资料', 'task detail navigation preserves the draft');
  const settings = page.getByRole('dialog', { name: '对话操作' }).getByRole('button', { name: '设置', exact: true });
  await more.press('ArrowUp');
  await settings.waitFor();
  await page.waitForFunction(() => document.activeElement?.textContent === '设置');
  check((await page.getByRole('dialog', { name: '对话操作' }).evaluate(element => getComputedStyle(element).boxShadow)) !== 'none', 'overflow has its own elevation');
  await settings.press('Escape');
  check(await more.evaluate(element => element === document.activeElement), 'Escape in the overflow restores its trigger');
  await more.press('ArrowDown');
  await page.getByRole('button', { name: '查看工作空间文件' }).waitFor();
  await settings.click();
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  check(await composer.inputValue() === '替我整理项目资料', 'settings navigation preserves the draft');
  await composer.press('Enter');
  await page.getByText('整理好了。', { exact: true }).waitFor();
  check(posts.length === 1 && posts[0].content === '替我整理项目资料' && await composer.inputValue() === '', 'one send shows the response and clears draft');
  await progress.getByText('已完成', { exact: true }).waitFor();
  const turnFiles = page.getByRole('region', { name: '记录的文件变更' });
  await turnFiles.getByRole('button', { name: '查看变更', exact: true }).waitFor();
  check(await progress.locator('.task-progress-row').count() === 1 && await progress.getByRole('button', { name: /查看最近文件记录/ }).count() === 0 && (await turnFiles.innerText()).includes('reports/summary.md'), 'one task updates in place without duplicating the exact file receipt action');
  const threadHeader = page.locator('.conversation-topbar');
  check(await threadHeader.getByRole('heading', { name: '替我整理项目资料' }).isVisible() && await threadHeader.getByRole('button', { name: '改动 1' }).isVisible(), 'working header uses actual request title and change count');
  check(!(await threadHeader.innerText()).includes('条已加载消息') && await threadHeader.getByRole('button', { name: /活动 \d|子任务 \d/ }).count() === 0, 'working header does not crowd the title with diagnostic counts');
  const readingColumnAligned = () => turnFiles.evaluate(element => {
    const file = element.getBoundingClientRect();
    const editor = document.querySelector('.composer').getBoundingClientRect();
    const shell = document.querySelector('.conversation-shell').getBoundingClientRect();
    return file.width <= 737 && editor.width <= shell.width &&
      Math.abs(file.left + file.width / 2 - editor.left - editor.width / 2) < 2 &&
      editor.width <= 921 && editor.width >= Math.min(736, shell.width - 300) &&
      Math.abs(editor.left + editor.width / 2 - shell.left - shell.width / 2) < 2;
  });
  check(await readingColumnAligned(), 'Windows file review and editor use bounded centered reading columns');
  check(await turnFiles.evaluate(element => {
    const previous = element.previousElementSibling;
    return previous?.classList.contains('from-agent') &&
      element.getBoundingClientRect().top - previous.getBoundingClientRect().bottom <= 14 &&
      parseFloat(getComputedStyle(element).borderTopWidth) >= 1;
  }), 'changes follow the answer in one lightly framed review block');
  await page.waitForFunction(() => document.querySelector('.composer textarea').getBoundingClientRect().height >= 30);
  check(await page.locator('.composer').evaluate(element => element.getBoundingClientRect().height >= 98 && element.getBoundingClientRect().height <= 150), 'working Windows editor retains its two-row input and action layout');
  await capture('round2-thread');
  const preview = page.getByLabel('工作空间文件预览', { exact: true });
  await turnFiles.getByRole('button', { name: '查看变更', exact: true }).click();
  await preview.locator('.artifact-history-summary').waitFor();
  check(readReceipts.length === 1 && readReceipts[0] === `/api/artifacts/receipts/${receiptId}/diff`, 'review opens the exact immutable file receipt and no other one');
  check(await preview.locator('.diff-removed code').innerText() === '整理初稿' &&
    await preview.locator('.diff-added code').innerText() === '整理结果' &&
    await preview.locator('.diff-removed code').isVisible() && await preview.locator('.diff-added code').isVisible(),
    'immutable receipt renders the exact previous and resulting content, not merely a summary');
  await capture('round2-review');
  await page.setViewportSize({ width: 818, height: 582 });
  await preview.locator('.preview-document').waitFor();
  check(await preview.locator('.preview-content').evaluate(element => {
    const bounds = element.getBoundingClientRect();
    const documentBounds = element.querySelector('.preview-document').getBoundingClientRect();
    return documentBounds.width >= Math.min(200, bounds.width * .5) && documentBounds.right <= bounds.right + 1 && documentBounds.height > 100;
  }), '818px Windows history retains a readable document alongside the file tree');
  await capture('round5-review-818');
  await page.setViewportSize(initialViewport);
  await preview.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await preview.waitFor({ state: 'hidden' });
  await progress.locator('.task-progress-summary').click();
  await drawer.locator('.quest-detail-heading').waitFor();
  await drawer.locator('.quest-technical > summary').waitFor();
  check(await drawer.getByText('暂无计划', { exact: true }).isVisible() && await drawer.locator('.quest-technical > summary').isVisible(), 'completed task honestly reports no recorded plan and retains technical details');
  await drawer.locator('.quest-detail-heading').press('Escape');
  await taskRow.waitFor();
  check(await taskRow.evaluate(element => element === document.activeElement), 'returning from a task opened directly in chat focuses the matching list row');
  await taskRow.press('Escape');
  await drawer.waitFor({ state: 'hidden' });
  await page.setViewportSize({ width: 390, height: 650 });
  await rail.waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: '展开工作空间侧栏' }).click();
  await rail.waitFor();
  check(await page.locator('.chat-workspace').evaluate(element => element.hasAttribute('inert')), 'narrow navigation prevents keyboard access behind the overlay');
  await page.keyboard.press('Escape');
  await rail.waitFor({ state: 'hidden' });
  await composer.fill('窄屏草稿');
  await tasks.click();
  await drawer.waitFor();
  check(await drawer.evaluate(element => Math.abs(element.getBoundingClientRect().width - innerWidth) < 1), 'narrow Tasks takes the full viewport');
  check(await page.locator('.chat-workspace').evaluate(element => element.hasAttribute('inert') && getComputedStyle(element).visibility === 'hidden'), 'narrow Tasks fully hides and disables the underlying chat');
  await taskRow.click();
  await drawer.locator('.quest-detail-heading').waitFor();
  await capture('round3-mobile-task');
  await page.setViewportSize({ width: 900, height: 700 });
  await page.waitForFunction(() => document.querySelector('.app-shell').classList.contains('task-page-open'));
  check(await drawer.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    return Math.abs(bounds.width - innerWidth) < 1 && Math.abs(bounds.left) < 1;
  }), 'a 900px window shows a full task page rather than covering only part of the chat');
  check(await drawer.locator('.quest-detail-heading').isVisible(), 'rotating or widening keeps the same selected task');
  await capture('round3-tablet-task');
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.waitForFunction(() => !document.querySelector('.app-shell').classList.contains('task-page-open'));
  check(await drawer.evaluate(element => element.getBoundingClientRect().width < innerWidth / 2) &&
    await page.locator('.chat-workspace').evaluate(element => !element.hasAttribute('inert')), 'wide mode makes the same task a side panel and restores chat interaction');
  await capture('round3-desktop-task');
  await page.setViewportSize({ width: 390, height: 650 });
  await page.waitForFunction(() => document.querySelector('.app-shell').classList.contains('task-page-open'));
  await drawer.getByRole('button', { name: '关闭任务详情' }).click();
  await composer.waitFor();
  check(await readingColumnAligned(), 'narrow file review and composer remain aligned and centered without overflowing');
  check(await composer.inputValue() === '窄屏草稿', 'return from the full task page restores the same draft');
  check(await composer.isVisible() && await tasks.isVisible() && await threadHeader.getByRole('button', { name: '改动 1' }).isVisible(), 'narrow windows keep composer, tasks and actual changes available');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'no horizontal page overflow');
  const contrast = () => page.evaluate(() => {
    const luminance = (color) => {
      const rgb = color.match(/\d+(?:\.\d+)?/g).slice(0, 3).map(value => {
        const channel = Number(value) / 255;
        return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
      });
      return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
    };
    const ratio = (a, b) => {
      const x = luminance(a), y = luminance(b);
      return (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
    };
    const surface = getComputedStyle(document.querySelector('.conversation-topbar')).backgroundColor;
    return Math.min(...['.thread-tool', '.process-transcript > summary', '.turn-diff-actions button', '.task-progress-status']
      .map(selector => ratio(getComputedStyle(document.querySelector(selector)).color, surface)));
  });
  check(await contrast() >= 4.5, 'default dark action and status captions have at least 4.5:1 contrast');
  await page.setViewportSize({ width: 320, height: 560 });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await more.press('ArrowDown');
  const darkMenu = page.getByRole('dialog', { name: '对话操作' });
  await darkMenu.waitFor();
  check(await darkMenu.evaluate(element => getComputedStyle(element).transitionDuration.split(',').every(value => parseFloat(value) <= .001)), 'reduced motion disables the floating transition');
  check(await page.locator('.conversation-topbar').evaluate(element => getComputedStyle(element).backgroundColor === 'rgb(17, 17, 17)'), 'dark appearance uses the dark surface');
  check(await contrast() >= 4.5, 'dark action and status captions have at least 4.5:1 contrast');
  check(await darkMenu.evaluate(element => {
    const { left, right } = element.getBoundingClientRect();
    return left >= 0 && right <= innerWidth;
  }), 'dark keyboard menu fits a 320px viewport');
  await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '查看工作空间文件');
  check(await darkMenu.getByRole('button', { name: '查看工作空间文件' }).evaluate(element => {
    const style = getComputedStyle(element);
    return element === document.activeElement && style.outlineStyle === 'solid' && style.outlineColor === 'rgb(120, 185, 255)';
  }), 'the focused dark-menu action has a visible inset ring');
  await capture('round4-dark-320');
  await page.keyboard.press('Escape');
  check(await more.evaluate(element => document.activeElement === element), 'Escape restores the menu trigger at 320px');
  await tasks.click();
  await drawer.waitFor();
  check(await drawer.evaluate(element => getComputedStyle(element).animationName === 'none'), 'reduced motion disables task drawer travel');
  await drawer.getByRole('button', { name: '关闭任务详情' }).click();
  await drawer.waitFor({ state: 'hidden' });
  await page.setViewportSize({ width: 818, height: 582 });
  await rail.waitFor();
  await page.emulateMedia({ forcedColors: 'active', colorScheme: 'light', reducedMotion: 'reduce' });
  await composer.fill('');
  check(await send.evaluate(element => element.disabled && getComputedStyle(element).opacity === '1' && getComputedStyle(element).borderTopStyle === 'solid'), 'forced colors keep disabled Send legible without enabling it');
  check(await rail.locator('[aria-current="page"]').evaluate(element => getComputedStyle(element).outlineStyle === 'solid'), 'forced colors mark the active navigation even without the painted background');
  await capture('round4-forced-colors');
  await turnFiles.getByRole('button', { name: '查看变更', exact: true }).click();
  await preview.locator('.artifact-history-summary').waitFor();
  const added = preview.locator('.diff-added .diff-kind-symbol');
  const removed = preview.locator('.diff-removed .diff-kind-symbol');
  check(await added.isVisible() && await removed.isVisible() && await added.innerText() === '+' && await removed.innerText() === '−',
    'forced-color history differentiates added and removed lines without relying on color');
  await capture('round4-forced-diff');
  check(unexpected.length === 0, `all API requests match explicit synthetic fixtures: ${unexpected.join(', ')}`);
  return { checks };
}
