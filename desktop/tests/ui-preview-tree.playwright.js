// Synthetic Tauri file source + synthetic HTTP only. No real workspace path is read.
// Windows reference shell; original file operations and normalized preview resizing retained.
// Tree icons/indentation/Git palette: tree-source.txt and filetree-colors-source.txt.
async (page, { outputDir }) => {
  const checks = [], httpWrites = [], missing = [], previewGeometry = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  await page.addInitScript(() => {
    window.isTauri = true;
    window.__treeFixtureCalls = [];
    const entry = (path, directory = false) => ({ path, name: path.split('/').pop(), directory, size: directory ? undefined : 120 });
    const directories = {
      '': [entry('src', true), entry('README.md'), entry('deleted.txt')],
      src: [entry('src/nested', true), entry('src/main.ts'), entry('src/new.ts'), entry('src/unchanged.ts')],
      'src/nested': [entry('src/nested/deep.ts')],
    };
    const files = [
      { path: 'README.md', status: 'M', added: 1, removed: 1 },
      { path: 'src/main.ts', status: 'M', added: 1, removed: 1 },
      { path: 'src/new.ts', status: 'A', added: 2, removed: 0 },
      { path: 'src/nested/deep.ts', status: 'M', added: 1, removed: 1 },
      { path: 'deleted.txt', status: 'D', added: 0, removed: 1 },
    ];
    window.__TAURI_INTERNALS__ = { invoke: async (command, args = {}) => {
      window.__treeFixtureCalls.push({ command, args });
      if (command === 'connection_get') return { state: 'ready', baseUrl: 'http://127.0.0.1:8765', bearer: 'synthetic-tree-bearer', workspaceId: 'tree-fixture', generation: 'tree-generation' };
      if (command === 'runtime_status') return { state: 'ready', generation: 'tree-generation', restartCount: 0, lastErrorCode: null };
      if (command === 'workspace_operation_status') throw new Error('OPERATION_NOT_FOUND');
      if (command === 'workspace_files_list') return directories[args.path] || [];
      if (command === 'workspace_changes') return { repository: true, files };
      if (command === 'workspace_file_read') return { path: args.path, kind: 'text', text: args.path === 'README.md' ? '# Synthetic tree fixture\n\nCurrent Markdown file.' : args.path === 'deleted.txt' ? '' : 'export const answer = 42;\n// Synthetic current source bytes.', size: 120 };
      if (command === 'workspace_file_diff') return { text: `--- a/${args.path}\n+++ b/${args.path}\n@@ -7,2 +9,2 @@\n unchanged source\n-export const answer = 41;\n+export const answer = 42;\n`, scope: 'worktree' };
      throw new Error(`unexpected synthetic native command: ${command}`);
    } };
  });
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    let body, status = 200;
    if (request.method() !== 'GET') { httpWrites.push(url.pathname); status = 400; body = { detail: 'tree fixture is read-only' }; }
    else if (url.pathname === '/api/conversation') body = { messages: [{ id: 'tree-intro', role: 'assistant', content: '合成文件树验收，所有路径和内容均为测试数据。', created_at: '2026-01-01T00:00:00Z', metadata: { execution_mode: 'mock' } }], next_before: null, has_more: false };
    else if (['/api/timeline', '/api/quests', '/api/approvals/pending', '/api/activity'].includes(url.pathname)) body = { items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/settings') body = { revision: 0, config: null, workspace_id: 'tree-fixture', workspace_root: 'D:/Synthetic/TreeFixture', context_limit: 8192, restore_fenced: false, environment_overrides: [] };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/projects/discovered') body = [];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/budget') body = { token_limit: 0, concurrency_limit: 1 };
    else if (url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/memory') body = { items: [], cursor: null, omitted: 0, bytes: 0 };
    else { missing.push(url.pathname); status = 404; body = { detail: 'tree fixture endpoint missing' }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.goto(process.env.FORGE_UI_BASE_URL || 'http://127.0.0.1:5173/');
  await page.waitForFunction(() => document.querySelector('.current-project')?.textContent.includes('TreeFixture'));
  await page.getByLabel('消息内容').fill('tree fixture draft');
  await page.getByRole('button', { name: '从输入栏打开工作空间文件', exact: true }).click();
  const preview = page.getByLabel('工作空间文件预览', { exact: true }), tree = preview.locator('.preview-file-tree');
  const changedSection = tree.locator('.file-tree-section').first(), allSection = tree.locator('.file-tree-section').last();
  await changedSection.locator('.changed-file').first().waitFor();
  await allSection.getByRole('button', { name: '所有文件' }).waitFor();
  check(await changedSection.getByRole('button', { name: '已更改 5', exact: true }).isVisible() && await allSection.getByRole('button', { name: '所有文件' }).isVisible(), 'changed files and all files remain available as two simultaneous tree sections');
  const treeToggle = preview.getByRole('button', { name: '切换文件树', exact: true });
  check(await preview.locator('.preview-toolbar').getByRole('button', { name: '切换文件树', exact: true }).count() === 1, 'the native file-tree toggle lives in the document toolbar');
  await treeToggle.click();
  check(!(await tree.isVisible()) && await treeToggle.getAttribute('aria-pressed') === 'false', 'the toolbar file-tree toggle hides its tree and exposes the current state');
  await preview.getByRole('button', { name: '打开文件', exact: true }).click();
  await tree.getByLabel('筛选已加载文件', { exact: true }).waitFor();
  check(await tree.isVisible() && await treeToggle.getAttribute('aria-pressed') === 'true' && await tree.getByLabel('筛选已加载文件', { exact: true }).evaluate((element) => element === document.activeElement) && await allSection.getByRole('button', { name: '所有文件' }).getAttribute('aria-expanded') === 'true', 'the native tab plus reveals all workspace files and focuses the existing filter');
  check(await changedSection.locator('.changed-file').count() === 5 && await changedSection.locator('.changed-directory-toggle[title="src"]').count() === 1 && await changedSection.locator('.changed-directory-toggle[title="src/nested"]').count() === 1, 'the changed-file tree groups same-level leaves and nested directories from actual synthetic paths');
  check((await changedSection.locator('.changed-file[title="README.md"]').innerText()).includes('+1') && (await changedSection.locator('.changed-file[title="deleted.txt"]').innerText()).includes('−1'), 'the changed-file rows preserve supplied addition and deletion counts');
  const colors = await changedSection.locator('.changed-file').evaluateAll((rows) => Object.fromEntries(rows.map((row) => [row.dataset.status, getComputedStyle(row).color])));
  check(colors.A === 'rgb(0, 162, 64)' && colors.D === 'rgb(224, 46, 42)' && colors.M === 'rgb(2, 133, 255)', `sectioned native tree uses the captured preset's exact added/deleted colors and modified-blue override: ${JSON.stringify(colors)}`);
  const fileGlyphs = await changedSection.locator('.changed-file').evaluateAll((rows) => Object.fromEntries(rows.map((row) => {
    const icon = row.querySelector('.file-type-glyph'), rect = icon.getBoundingClientRect();
    return [row.title, { type: icon.dataset.fileType, width: rect.width, height: rect.height, hidden: icon.getAttribute('aria-hidden'), paths: Array.from(icon.querySelectorAll('path')).map((path) => path.getAttribute('d')) }];
  })));
  check(fileGlyphs['README.md'].type === 'markdown' && fileGlyphs['src/main.ts'].type === 'typescript' && fileGlyphs['deleted.txt'].type === 'text' && Object.values(fileGlyphs).every((icon) => icon.width === 16 && icon.height === 16 && icon.hidden === 'true'), 'real Markdown, TypeScript and text paths use distinct 16px decorative native file glyphs');
  check(fileGlyphs['README.md'].paths[0] === 'M1 12V4h2l2 2.5L7 4h2v8H7V7.5l-2 2-2-2V12zm9-3 3 3.5L16 9h-2V4h-2v5z' && fileGlyphs['src/main.ts'].paths.length === 2 && fileGlyphs['deleted.txt'].paths.length === 2 && fileGlyphs['src/main.ts'].paths[1] !== fileGlyphs['deleted.txt'].paths[1], 'native DMvp glyph artwork retains its Markdown mark and distinct source/text paths');
  const statusGlyphs = await changedSection.locator('.changed-file').evaluateAll((rows) => rows.map((row) => {
    const status = row.querySelector('.file-change-status'), rect = status.getBoundingClientRect();
    return { state: row.dataset.status, change: status.dataset.change, width: rect.width, height: rect.height, svg: status.querySelector('svg')?.getAttribute('aria-hidden'), paths: status.querySelectorAll('path').length };
  }));
  check(statusGlyphs.every((status) => status.change === ({ A: 'added', D: 'deleted', M: 'modified' })[status.state] && status.width === 20 && status.height === 20 && status.svg === 'true' && status.paths === 2), 'Git states use the native 20px outlined plus/minus/dot glyph lane without changing accessible status text');
  const indentation = await changedSection.evaluate((section) => ['src', 'src/nested', 'src/nested/deep.ts'].map((path) => {
    const row = section.querySelector(`[title="${path}"]`), guide = row.querySelector('.file-tree-guides');
    return { path, padding: parseFloat(getComputedStyle(row).paddingInlineStart), guides: Array.from(guide.children).map((line) => ({ left: parseFloat(line.style.left), width: line.getBoundingClientRect().width })) };
  }));
  check(indentation.every((row, index) => row.padding === 11.75 + 8.75 * index && row.guides.length === index + 1 && row.guides.every((line, level) => line.left === 10.75 + 8.75 * level && line.width === 1)), `nested paths retain native 8.75px level spacing and aligned 1px guides: ${JSON.stringify(indentation)}`);
  await page.getByLabel('消息内容', { exact: true }).focus();
  await page.mouse.move(0, 0);
  await page.waitForTimeout(180);
  const deepGuides = changedSection.locator('.changed-file[title="src/nested/deep.ts"] .file-tree-guides');
  check(await deepGuides.evaluate((guide) => Number(getComputedStyle(guide).opacity)) === 0, 'indent guides remain visually quiet outside hover or keyboard focus');
  await tree.hover();
  await page.waitForTimeout(180);
  check(await deepGuides.evaluate((guide) => Number(getComputedStyle(guide).opacity)) === .75, 'hover reveals the native indent guides at .75 opacity');
  const changedSrc = changedSection.locator('.changed-directory-toggle[title="src"]'), changedMain = changedSection.locator('.changed-file[title="src/main.ts"]');
  await changedSrc.click();
  check(await changedSrc.getAttribute('aria-expanded') === 'false' && !(await changedMain.isVisible()) && await changedSection.locator('.changed-file[title="README.md"]').isVisible(), 'collapsing a changed directory hides only its descendants');
  await changedSrc.click();
  await changedMain.click();
  const diff = preview.locator('.document-main:visible .code-preview-diff');
  await diff.waitFor();
  check(await preview.locator('.preview-breadcrumb').getAttribute('title') === 'src/main.ts' && await preview.getByRole('button', { name: '显示更改', exact: true }).getAttribute('aria-pressed') === 'true', 'a changed file opens the exact path in current Git diff mode');
  check(await diff.locator('.diff-removed').getAttribute('data-old-line') === '8' && await diff.locator('.diff-added').getAttribute('data-new-line') === '10', 'the changed-file preview derives old and new line numbers from its actual hunk');
  check(await diff.locator('.diff-removed .diff-word-change').innerText() === '1' && await diff.locator('.diff-added .diff-word-change').innerText() === '2', 'replacement emphasis marks only the changed token within otherwise equal source lines');
  await preview.getByRole('button', { name: '显示更改', exact: true }).click();
  await preview.getByLabel('src/main.ts 源码', { exact: true }).waitFor();
  check((await preview.getByLabel('src/main.ts 源码', { exact: true }).innerText()).includes('Synthetic current source bytes') && await preview.getByRole('button', { name: '显示更改', exact: true }).getAttribute('aria-pressed') === 'false', 'the same tab switches from recorded current diff to independently read current source');
  const filter = tree.getByLabel('筛选已加载文件', { exact: true });
  await filter.fill('deep.ts');
  check(await changedSection.locator('.changed-file:visible').count() === 1 && await changedSection.locator('.changed-file[title="src/nested/deep.ts"]').isVisible(), 'filtering changed paths retains only the matching nested file');
  await filter.fill('');
  const allSrc = allSection.locator('.file-tree-row[title="src"]');
  if (await allSrc.getAttribute('aria-expanded') !== 'true') await allSrc.click();
  const nested = allSection.locator('.file-tree-row[title="src/nested"]');
  if (await nested.getAttribute('aria-expanded') !== 'true') await nested.click();
  await allSection.locator('.file-tree-row[title="src/nested/deep.ts"]').waitFor();
  check(await allSection.locator('.file-tree-row[title="src/unchanged.ts"]').isVisible(), 'the all-files section includes unchanged siblings beside changed paths');
  const unchanged = allSection.locator('.file-tree-row[title="src/unchanged.ts"]');
  await unchanged.click();
  await page.waitForTimeout(180);
  const selected = await unchanged.evaluate((row) => { const style = getComputedStyle(row); return { color: style.color, background: style.backgroundColor }; });
  check(selected.background === 'rgb(38, 38, 38)' && selected.color !== selected.background, `a normal selected file uses the Windows reference neutral selected background and visible text: ${JSON.stringify(selected)}`);
  const nativeBefore = await page.evaluate(() => window.__treeFixtureCalls.length);
  await tree.getByRole('button', { name: '刷新文件树', exact: true }).click();
  await page.waitForFunction((count) => window.__treeFixtureCalls.length > count + 2, nativeBefore);
  const refreshCalls = await page.evaluate((count) => window.__treeFixtureCalls.slice(count), nativeBefore);
  check(refreshCalls.some((call) => call.command === 'workspace_changes') && refreshCalls.some((call) => call.command === 'workspace_files_list' && call.args.path === '') && refreshCalls.some((call) => call.command === 'workspace_files_list' && call.args.path === 'src'), 'tree refresh rereads change state and expanded directories through the existing read-only source');
  await changedSection.locator('.changed-file[title="README.md"]').click();
  await preview.getByRole('button', { name: '显示更改', exact: true }).click();
  await preview.locator('.file-markdown h1').waitFor();
  check(await preview.locator('.file-markdown h1').innerText() === 'Synthetic tree fixture', 'a changed Markdown file can return to its current rendered preview');
  await preview.getByRole('button', { name: '查看源代码', exact: true }).click();
  check((await preview.getByLabel('README.md 源码', { exact: true }).innerText()).includes('# Synthetic tree fixture'), 'Markdown source remains available with the original accessible action');
  await preview.getByRole('button', { name: '预览', exact: true }).click();
  check(await preview.locator('.file-markdown h1').isVisible(), 'the original preview action returns to rendered Markdown');
  await changedMain.click();
  await diff.waitFor();
  const divider = page.getByRole('separator', { name: '调整文件预览宽度' });
  for (const width of [1152, 1280, 1400, 1440, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.waitForTimeout(180);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const geometry = await preview.evaluate((panel) => {
      const bounds = (selector) => document.querySelector(selector).getBoundingClientRect();
      const shortNames = Array.from(panel.querySelectorAll('.file-tree-row[title="README.md"], .file-tree-row[title="src/nested/deep.ts"]')).map((row) => {
        const name = row.querySelector('span:not(.file-tree-chevron):not(.file-change-status)');
        return { path: row.title, available: name.clientWidth, text: name.scrollWidth };
      });
      return { viewport: innerWidth, workspace: bounds('.chat-workspace').width, preview: panel.getBoundingClientRect().width, document: bounds('.preview-document').width, tree: bounds('.preview-file-tree').width, navigation: bounds('.rail').width, overflow: document.documentElement.scrollWidth - innerWidth, shortNames };
    });
    previewGeometry.push(geometry);
    const maximum = Math.max(320, geometry.workspace - 352), expected = Math.max(320, Math.min(maximum, Math.round(geometry.workspace * .47)));
    check(Math.abs(geometry.preview - expected) <= 2 && geometry.preview >= 319 && geometry.preview <= maximum + 1, `${width}px window: untouched preview uses the reference 47% split within its safe bounds: ${JSON.stringify(geometry)}`);
    const expectedTree = geometry.preview <= 440 ? Math.min(250, Math.max(200, geometry.preview * .6), geometry.preview * .45) : Math.min(250, Math.max(200, geometry.preview * .6));
    check(Math.abs(geometry.tree - expectedTree) <= 1 && geometry.document >= 140 && geometry.overflow <= 1, `${width}px window: shared file tree preserves the document on narrow panes: ${JSON.stringify(geometry)}`);
    const expectedNavigation = Math.max(52, Math.min(64, 110.24 - width * .02844)) + Math.max(288, Math.min(360, 637.42 - width * .1706));
    check(Math.abs(geometry.navigation - expectedNavigation) <= 1, `${width}px window: measured Windows icon rail and project panel retain reference widths`);
    check(geometry.shortNames.length === 4 && geometry.shortNames.every((name) => name.available > 0 && (geometry.preview <= 440 || name.text <= name.available + 1)), `${width}px window: short filenames remain readable, with intentional ellipsis only in narrow trees: ${JSON.stringify(geometry.shortNames)}`);
    await page.mouse.move(0, 0);
    await page.screenshot({ path: `${outputDir}/scale-preview-tree-${width}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(180);
  const beforeResize = Number(await divider.getAttribute('aria-valuenow'));
  await divider.focus(); await divider.press('ArrowLeft');
  await page.waitForTimeout(180);
  const manualWidth = Number(await divider.getAttribute('aria-valuenow'));
  check(Math.abs(manualWidth - beforeResize - 10) <= 1, 'keyboard resize increases preview width by ten pixels');
  const manualRatio = (manualWidth - 320) / (Number(await divider.getAttribute('aria-valuemax')) - 320);
  await page.setViewportSize({ width: 1920, height: 1000 });
  await page.waitForTimeout(180);
  check(Math.abs(Number(await divider.getAttribute('aria-valuenow')) - (320 + manualRatio * (Number(await divider.getAttribute('aria-valuemax')) - 320))) <= 2, 'an explicitly resized preview preserves its normalized manual ratio across viewport changes');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(180);
  await preview.getByRole('button', { name: '展开文件预览', exact: true }).click();
  await page.locator('.sidebar-panel').waitFor({ state: 'hidden' });
  check(!(await page.getByLabel('主导航').locator('.workspace-selector-wrap').isVisible()) && await page.locator('.conversation-shell').evaluate((element) => element.inert), 'expanded preview hides the workspace panel and removes the retained conversation from keyboard interaction');
  await page.mouse.move(0, 0); await page.waitForTimeout(220);
  await page.screenshot({ path: `${outputDir}/simple-preview-tree-expanded.png`, fullPage: true });
  await preview.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await preview.waitFor({ state: 'hidden' });
  check(await page.getByLabel('消息内容').inputValue() === 'tree fixture draft' && await page.getByLabel('主导航').locator('.workspace-selector-wrap').isVisible(), 'closing expanded synthetic preview restores the actual prior sidebar and preserved draft');
  const commands = await page.evaluate(() => window.__treeFixtureCalls.map((call) => call.command));
  check(commands.every((command) => ['runtime_status', 'connection_get', 'workspace_operation_status', 'workspace_files_list', 'workspace_changes', 'workspace_file_read', 'workspace_file_diff'].includes(command)) && httpWrites.length === 0 && missing.length === 0, 'the fixture performs only allowlisted read commands and HTTP reads');
  check(await page.evaluate(() => localStorage.length === 0 && sessionStorage.length === 0), 'hierarchical file browsing persists no browser storage');
  return { status: 'passed', fixture: 'synthetic HTTP and Tauri file source only', checks: checks.length, nativeReads: commands.length, writes: httpWrites.length, previewGeometry };
}
