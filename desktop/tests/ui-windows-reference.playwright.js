// Reference geometry regression. All Core HTTP and Tauri file reads are synthetic;
// no real workspace bytes, provider calls, or write operations are used.
async (page, { outputDir }) => {
  const checks = [], writes = [], unknown = [];
  const check = (value, description) => {
    if (!value) throw new Error(description);
    checks.push(description);
  };
  const capture = (name) => process.env.FORGE_CAPTURE_UI === '1' ? page.screenshot({ path: `${outputDir}/${name}.png`, animations: 'disabled' }) : Promise.resolve();
  const settings = { revision: 1, config: {}, workspace_id: 'pixel-fixture', workspace_root: 'C:/Fixture/Agent', provider_configured: true, router_model_id: 'Forge Test' };
  const taskEquivalents = ['analysis.py', 'checks.py', 'config.py', 'fixtures.py', 'paths.py', 'report.py', 'runtime.py', 'source.py', 'summary.py', 'validation.py'];
  await page.addInitScript((scripts) => {
    window.isTauri = true;
    window.__layoutReferenceCalls = [];
    const entry = (path, directory = false) => ({ path, name: path.split('/').pop(), directory, size: directory ? undefined : 200 });
    const changes = ['README.md', ...scripts.map(name => `src/${name}`), 'docs/HANDOFF.md'].map((path, index) => ({ path, status: index < 1 ? 'M' : 'A', added: index + 3, removed: index ? 0 : 1 }));
    const markdown = [
      '# FORGE 项目交接记录', '', '本页是前端布局验收用的合成 Markdown。该文档用于验证真实文件预览、阅读层级、文件树和滚动区域，不代表执行过任何项目任务。',
      '', '## 变更概览', '', '- 独立呈现正文、文件树和文件路径。', '- 用小标题、列表和段落确认正文排版。', '- 文件标签可以切换，回到会话后草稿可恢复。',
      '', '## 验收范围', '', '已在浏览器中检查深色外壳、工作区与代码预览。设置页保留 FORGE 已有的功能。',
      '', '### 阅读与查找', '', '文件树保留独立滚动，切换当前文件不会覆盖同一对话。',
      ...Array.from({ length: 25 }, (_, index) => `\n## 阅读测试 ${index + 1}\n\n合成段落用于校验正文滚动范围，与文件树的滚动互不影响。`),
      '', '## 文档末尾'
    ].join('\n');
    const source = [
      '"""Reference preview with deterministic local fixture text, a longer synthetic first line that should wrap naturally next to the file tree."""', '', 'from pathlib import Path', '', 'RESULT = "layout verification"', '',
      'def read_preview(path):', '    path = Path(path)', '    return {"name": path.name, "expected": RESULT}', '',
      'def summarize(items):', '    return [read_preview(item) for item in items]', '', ...Array.from({ length: 22 }, (_, i) => `# Example ${String(i + 1).padStart(2, '0')}: source preview line`)
    ].join('\n');
    window.__TAURI_INTERNALS__ = { invoke: async (command, args = {}) => {
      window.__layoutReferenceCalls.push(command);
      if (command === 'connection_get') return { state: 'ready', baseUrl: 'http://127.0.0.1:8765', bearer: 'synthetic-layout-bearer', workspaceId: 'pixel-fixture', generation: 'pixel-fixture-gen' };
      if (command === 'runtime_status') return { state: 'ready', generation: 'pixel-fixture-gen', restartCount: 0 };
      if (command === 'workspace_operation_status') throw new Error('OPERATION_NOT_FOUND');
      if (command === 'workspace_changes') return { repository: true, files: changes };
      if (command === 'workspace_files_list') return ({
        '': [entry('src', true), entry('docs', true), entry('README.md')],
        src: scripts.map(name => entry(`src/${name}`)),
        docs: [entry('docs/HANDOFF.md')],
      })[args.path] || [];
      if (command === 'workspace_file_read') return { path: args.path, kind: 'text', text: args.path.endsWith('.md') ? markdown : source, size: 200 };
      if (command === 'workspace_file_diff') {
        const lines = args.path === 'src/config.py' ? Array.from({ length: 6400 }, (_, i) => `SAMPLE_${i + 1} = "${i === 6200 ? 'unique_tail_probe' : 'synthetic'}"`) : source.split('\n');
        return { scope: 'worktree', text: `--- a/${args.path}\n+++ b/${args.path}\n@@ -0,0 +1,${lines.length} @@\n${lines.map(line => '+' + line).join('\n')}` };
      }
      throw new Error('unexpected synthetic native command: ' + command);
    } };
  }, taskEquivalents);
  let populated = false;
  let longThread = false, hasIncoming = false;
  const messages = [
    { id: 'layout-ask-1', role: 'user', content: '先看一下项目现在的版本和已有的交接资料，把需要打开的文件集中起来。', created_at: '2026-11-16T08:00:00Z' },
    { id: 'layout-answer-1', role: 'assistant', content: '我把项目说明、配置记录和交接文档放在一起，方便在同一个工作区查看。\n\n文件改动会单独列出来，打开后可以查看源码、Markdown 正文和前后差异。', created_at: '2026-11-16T08:04:05Z' },
    { id: 'layout-ask-2', role: 'user', content: '好，保持会话不切换。我想一边读交接说明，一边对照文件里的改动。', created_at: '2026-11-16T08:06:00Z' },
    { id: 'layout-answer-2', role: 'assistant', content: '可以，文件会在右侧打开，当前对话保持在左边。\n\n- 先看项目概况，再打开对应源码。\n- 在文件树里切换其他资料，不会丢掉聊天记录。\n\n这些内容用于界面验收。', created_at: '2026-11-16T08:07:25Z' },
  ];
  const events = ['README.md', 'src/analysis.py', 'src/config.py', 'docs/HANDOFF.md'].map((path, index) => ({
    kind: 'tool_receipt', event_id: ('abc' + index).padEnd(32, '0'), seq: index + 1, status: 'succeeded', execution_mode: 'live',
    created_at: `2026-11-16T08:03:0${index}Z`, detail: { tool: 'file_write', path },
  }));
  const longReply = {
    id: 'layout-scroll-reply', role: 'assistant',
    content: Array.from({ length: 72 }, (_, i) => '验收记录 ' + (i + 1) + '：长对话可按原顺序滚动。').join('\n\n'),
    created_at: '2026-11-16T08:08:00Z',
  };
  const incoming = { id: 'layout-scroll-incoming', role: 'assistant', content: '回到最新测试的新回复。', created_at: '2026-11-16T08:09:00Z' };
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), { pathname } = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (request.method() !== 'GET') { writes.push(pathname); return route.fulfill({ status: 405, headers, json: { detail: 'Fixture is read-only' } }); }
    const lists = { items: [], next_before: null, has_more: false };
    let json;
    if (pathname === '/health') json = { session: 'layout-fixture' };
    else if (pathname === '/api/conversation') json = { ...lists, messages: populated ? [...messages, ...(longThread ? [longReply] : []), ...(hasIncoming ? [incoming] : [])] : [], router: {} };
    else if (pathname === '/api/timeline') json = { ...lists, items: populated ? events : [] };
    else if (pathname === '/api/settings') json = settings;
    else if (pathname === '/api/restore-status') json = { fenced: false };
    else if (['/api/quests', '/api/approvals/pending', '/api/activity'].includes(pathname)) json = lists;
    else if (pathname === '/api/projects/discovered') json = [{ id: 'tools', root: 'C:/Fixture/BuildTools', name: 'BuildTools' }, { id: 'docs', root: 'C:/Fixture/Documentation', name: 'Documentation' }];
    else if (pathname === '/api/models') json = [];
    else if (pathname === '/api/runtime/health') json = { components: [] };
    else if (pathname === '/api/budget') json = { token_limit: 0, concurrency_limit: 1 };
    else { unknown.push(pathname); return route.fulfill({ status: 404, headers, json: { detail: 'Unknown fixture endpoint' } }); }
    return route.fulfill({ headers, json });
  });
  const measurements = [];
  const rect = (selector) => page.locator(selector).first().evaluate(element => {
    const box = element.getBoundingClientRect();
    return { x: Math.round(box.x), y: Math.round(box.y), width: Math.round(box.width), height: Math.round(box.height), fills: getComputedStyle(element).backgroundColor };
  });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1626, height: 1038 });
  await page.goto('http://127.0.0.1:5173/');
  await page.locator('.welcome-project-name', { hasText: /^Agent$/ }).waitFor();
  await page.getByRole('button', { name: '打开工作空间文件', exact: true }).waitFor();
  const menubar = page.getByRole('menubar', { name: '应用程序菜单' });
  const fileMenu = menubar.getByRole('menuitem', { name: '文件', exact: true });
  const editMenu = menubar.getByRole('menuitem', { name: '编辑', exact: true });
  await fileMenu.press('ArrowRight');
  check(await editMenu.evaluate(node => node === document.activeElement), 'Left and Right move between visible application menu triggers');
  await editMenu.press('ArrowDown');
  await page.getByRole('menu', { name: '编辑菜单' }).getByRole('menuitem').first().press('ArrowRight');
  const viewItems = page.getByRole('menu', { name: '视图菜单' });
  await viewItems.waitFor();
  await page.keyboard.press('Escape');
  check(await menubar.getByRole('menuitem', { name: '视图', exact: true }).evaluate(node => node === document.activeElement), 'keyboard movement switches an open menu and Escape returns to its new trigger');
  await fileMenu.press('ArrowDown');
  const fileItems = page.getByRole('menu', { name: '文件菜单' });
  await fileItems.getByRole('menuitem').first().waitFor();
  await page.keyboard.press('Tab');
  await fileItems.waitFor({ state: 'hidden' });
  check(await editMenu.evaluate(node => node === document.activeElement), 'Tab exits an open application menu to the next focusable trigger');
  await fileMenu.press('ArrowDown');
  await fileItems.getByRole('menuitem').first().waitFor();
  const focusDraft = page.getByRole('textbox', { name: '消息内容' });
  await focusDraft.focus();
  await fileItems.waitFor({ state: 'hidden' });
  await page.keyboard.press('ArrowDown');
  check(await focusDraft.evaluate(node => node === document.activeElement), 'ArrowDown in the composer cannot be intercepted by a menu that lost focus');
  await menubar.getByRole('menuitem', { name: '视图', exact: true }).evaluate(node => node.blur());
  await page.mouse.move(0, 0);
  const home = { titlebar: await rect('.desktop-chrome'), workspace: await rect('.chat-workspace'), composer: await rect('.composer'), context: await rect('.composer-workspace-context'), banner: await rect('.empty-conversation h2') };
  check(home.titlebar.height === 54 && Math.abs(home.workspace.x - 424) <= 2 && Math.abs(home.workspace.y - 54) <= 2, '1626 home matches the reference outer frame and sidebar coordinates');
  check(home.composer.width >= 908 && home.composer.width <= 926 && Math.abs(home.composer.y - 892) <= 9 && home.composer.fills === 'rgb(47, 47, 47)', `home composer matches the reference width, baseline, and sampled dark fill: ${JSON.stringify(home.composer)}`);
  check(home.context.y < home.composer.y && Math.abs(home.context.x - home.composer.x - 16) <= 3 && Math.abs(home.banner.y - 495) <= 30, 'workspace context above the composer and the centered home heading match figure 1');
  check(home.context.fills === 'rgb(33, 33, 33)', 'home workspace context uses the sampled figure 1 gray');
  check(await page.locator('.current-project > summary').evaluate(row =>
    Math.abs(parseFloat(getComputedStyle(row).fontSize) - 18) < 0.2 && Math.abs(row.getBoundingClientRect().height - 39) < 1),
    '1626px desktop project labels follow the reference size and row rhythm');
  check(await page.getByRole('button', { name: '发送消息', exact: true }).isDisabled(), 'the reference arrangement preserves disabled empty sending');
  measurements.push({ viewport: '1626x1038', ...home });
  await capture('windows-reference-home');
  const homeFiles = page.getByRole('button', { name: '打开工作空间文件', exact: true });
  await homeFiles.click();
  await page.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await page.waitForFunction(() => document.activeElement === document.querySelector('.home-open-files'));
  check(await homeFiles.evaluate(button => document.activeElement === button), 'closing the home file workspace restores focus to its actual launcher');
  await page.setViewportSize({ width: 2048, height: 1104 });
  populated = true;
  await page.reload();
  await page.getByText('可以，文件会在右侧打开，当前对话保持在左边。', { exact: true }).waitFor();
  const firstProcess = page.locator('.process-transcript').first();
  check(await firstProcess.evaluate(group => !group.open && /4 分钟 5 秒/.test(group.querySelector('summary').textContent)), 'the folded process displays its measured request-to-reply duration');
  await firstProcess.locator(':scope > summary').click();
  check(await firstProcess.evaluate(group => group.open && /4 分钟 5 秒/.test(group.querySelector('summary').textContent)), 'opening process details preserves the same measured duration');
  await firstProcess.locator(':scope > summary').click();
  await page.getByRole('button', { name: '更多对话操作' }).click();
  await page.getByRole('button', { name: '查看工作空间文件', exact: true }).click();
  const preview = page.locator('.file-preview-workspace');
  const tree = preview.locator('.preview-file-tree');
  const changes = tree.locator('.changed-file[title="src/analysis.py"]');
  await changes.click();
  await preview.locator('.code-preview-diff .diff-added').first().waitFor();
  await page.mouse.move(0, 0);
  const work = { workspace: await rect('.chat-workspace'), conversation: await rect('.conversation-shell'), composer: await rect('.composer'), preview: await rect('.file-preview-workspace'), tree: await rect('.preview-file-tree') };
  check(Math.abs(work.workspace.x - 340) <= 2 && Math.abs(work.workspace.y - 44) <= 2 && Math.abs(work.preview.x - 1244) <= 9, `2048 reference sidebar and three-column split: ${JSON.stringify(work)}`);
  check(Math.abs(work.composer.width - 736) <= 2 && Math.abs(work.composer.x - 423) <= 8 && Math.abs(work.tree.width - 250) <= 8, 'thread composer and usable file tree match figure 3 widths');
  check(Math.abs(work.composer.y - 986) <= 8 && work.conversation.fills === 'rgb(17, 17, 17)' && work.composer.fills === 'rgb(46, 46, 46)', 'working composer baseline and sampled fills align with figure 3');
  check(await preview.getByRole('button', { name: '在工作空间打开文件', exact: true }).isVisible(), 'the desktop file bar provides an actual Open File action beside its tabs');
  const activeSourceTab = await rect('.preview-tab.active');
  check(Math.abs(activeSourceTab.x - 1491) <= 12, `source tabs start at the reference file-toolbar offset: ${activeSourceTab.x}`);
  const addedLine = await rect('.code-preview-diff .diff-added');
  const addedGutter = await rect('.code-preview-diff .diff-added > .source-line-number');
  const diffRows = preview.locator('.code-preview-diff .source-lines:visible');
  check(await diffRows.evaluate(element => element.classList.contains('is-wrapped') && element.scrollWidth === element.clientWidth && element.querySelector('[data-new-line="1"]').getBoundingClientRect().height > 40), 'short workspace diff wraps a long actual source row with no horizontal overflow');
  await preview.getByRole('button', { name: '查找文件内容', exact: true }).click();
  const wrapping = preview.getByRole('button', { name: '自动换行', exact: true });
  check(await wrapping.getAttribute('aria-pressed') === 'true', 'wrap preference is exposed to keyboard and assistive technology');
  await wrapping.press('Enter');
  check(await diffRows.evaluate(element => !element.classList.contains('is-wrapped') && element.scrollWidth > element.clientWidth), 'keyboard can restore horizontal reading of long diff rows');
  await wrapping.press('Enter');
  await preview.getByRole('textbox', { name: '在文件中查找', exact: true }).press('Escape');
  check(addedLine.fills === 'rgb(26, 43, 27)' && addedGutter.fills === 'rgb(15, 28, 16)', 'source diff uses distinct sampled greens for added lines and the line-number gutter');
  check(await page.locator('.turn-diff.activity-group-files').first().evaluate(card => {
    const tile = card.querySelector('.turn-diff-icon').getBoundingClientRect();
    const path = card.querySelector('.turn-diff-path');
    return Math.abs(tile.width - 40) < 1 && Math.abs(tile.height - 40) < 1 &&
      parseFloat(getComputedStyle(card).borderRadius) === 12 && parseFloat(getComputedStyle(path).fontSize) === 14 &&
      Math.abs(path.getBoundingClientRect().left - 436) <= 2;
  }), 'file-change card aligns icon, corners and filename with the desktop reference');
  measurements.push({ viewport: '2048x1104', ...work });
  await capture('windows-reference-source');
  await tree.locator('.changed-file[title="docs/HANDOFF.md"]').click();
  const showDiff = preview.getByRole('button', { name: '显示更改', exact: true });
  if (await showDiff.getAttribute('aria-pressed') === 'true') await showDiff.click();
  await preview.getByRole('heading', { name: 'FORGE 项目交接记录' }).waitFor();
  check(await preview.getByRole('tab', { name: 'analysis.py', exact: true }).count() === 1 && await preview.getByRole('tab', { name: 'HANDOFF.md', exact: true }).count() === 1, 'file tabs keep source and actual rendered Markdown open');
  await page.mouse.move(0, 0);
  const markdownHeading = await rect('.document-rendered:not([hidden]) .file-markdown h1');
  check(Math.abs(markdownHeading.x - 1260) <= 3 && Math.abs(markdownHeading.y - 149) <= 3, `Markdown content aligns to the reference reading inset: ${JSON.stringify(markdownHeading)}`);
  await capture('windows-reference-markdown');
  const treePriorScroll = await tree.locator('.file-tree-scroll').evaluate(element => element.scrollTop);
  const mdScroll = preview.locator('.file-tab-panel:visible .document-rendered:visible');
  await mdScroll.evaluate(element => { element.scrollTop = element.scrollHeight; });
  await page.waitForFunction(() => {
    const body = document.querySelector('.file-tab-panel:not([hidden]) .document-rendered:not([hidden])');
    const end = [...body.querySelectorAll('h2')].find(heading => heading.textContent === '文档末尾');
    return body.scrollTop > 100 && end.getBoundingClientRect().bottom <= body.getBoundingClientRect().bottom;
  });
  check(await tree.locator('.file-tree-scroll').evaluate(element => element.scrollTop) === treePriorScroll, 'long Markdown scrolls to its final real section without moving the file tree');
  // Visit the diff first so both editors exist; only the visible source should receive Find.
  await showDiff.click();
  await preview.locator('.code-preview-diff:visible .source-line').first().waitFor();
  await showDiff.click();
  await preview.getByRole('button', { name: '查看源代码', exact: true }).click();
  await preview.getByRole('button', { name: '查找文件内容', exact: true }).click();
  const markdownSourceFind = preview.locator('.document-source:visible .code-preview:not(.code-preview-diff)').getByRole('textbox', { name: '在文件中查找', exact: true });
  await markdownSourceFind.waitFor();
  await page.waitForFunction(() => document.activeElement.closest('.code-preview')?.matches(':not(.code-preview-diff)'));
  check(await markdownSourceFind.evaluate(element => document.activeElement === element), 'the toolbar find opens and focuses visible Markdown source after visiting a stale hidden diff');
  await markdownSourceFind.press('Escape');
  await tree.locator('.changed-file[title="src/config.py"]').click();
  if (await showDiff.getAttribute('aria-pressed') === 'false') await showDiff.click();
  await preview.locator('.code-preview-diff:visible [data-new-line="1"]').waitFor();
  check(await diffRows.evaluate(element => !element.classList.contains('is-wrapped') && element.querySelectorAll('.source-line').length <= 160), 'large diffs stay virtualized and do not enable automatic line wrapping');
  await preview.getByRole('button', { name: '查找文件内容', exact: true }).click();
  const fileSearch = preview.getByRole('textbox', { name: '在文件中查找', exact: true });
  await fileSearch.fill('unique_tail_probe');
  await fileSearch.press('Enter');
  const nearEndRow = preview.locator('.code-preview-diff:visible [data-new-line="6201"]');
  await nearEndRow.waitFor();
  check(await nearEndRow.evaluate(element => {
    const row = element.getBoundingClientRect(), pane = element.closest('.source-lines').getBoundingClientRect();
    return element.classList.contains('search-match') && row.top >= pane.top && row.bottom <= pane.bottom;
  }), 'Go to a match after row 6200 reaches the correct visible row of the virtualized diff');
  await preview.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await page.locator('.titlebar').getByRole('menuitem', { name: '文件', exact: true }).click();
  await page.getByRole('menu', { name: '文件菜单' }).getByRole('menuitem', { name: /^设置…/ }).click();
  await page.getByRole('heading', { name: '常规', exact: true }).waitFor();
  await page.waitForFunction(() => Math.abs(document.querySelector('.settings-nav').getBoundingClientRect().left - 52) < 3);
  await page.mouse.move(0, 0);
  const settingsLayout = { navigation: await rect('.settings-nav'), heading: await rect('.settings-title'), group: await rect('.settings-group') };
  check(Math.abs(settingsLayout.navigation.x - 52) <= 3 && Math.abs(settingsLayout.navigation.width - 288) <= 3 && Math.abs(settingsLayout.group.width - 728) <= 3, `settings matches figure 4 rail, navigation and content widths: ${JSON.stringify(settingsLayout)}`);
  check(Math.abs(settingsLayout.heading.x - 823) <= 7 && Math.abs(settingsLayout.heading.y - 78) <= 10, 'settings heading aligns to the centered reference reading column');
  check(settingsLayout.group.fills === 'rgb(28, 28, 28)' && Math.abs(settingsLayout.group.y - 187) <= 1, 'setting cards use the sampled dark gray and match the first reference card baseline');
  check(settingsLayout.navigation.fills === 'rgb(21, 23, 24)' && (await rect('.settings-search')).fills === 'rgb(38, 41, 43)', 'settings navigation and search use their measured reference colors');
  const settingsFileMenu = await rect('.application-menubar [data-app-menu="文件"]');
  check(Math.abs(settingsFileMenu.x - 75) <= 2 && !await page.locator('.titlebar-navigation > .sidebar-toggle[aria-expanded]').isVisible(), 'desktop settings aligns the application menu without a redundant project-sidebar toggle');
  check(await page.locator('.general-settings + .settings-block').evaluate(block => {
    const previous = block.previousElementSibling;
    return previous && block.getBoundingClientRect().top - previous.getBoundingClientRect().bottom >= 45;
  }), 'diagnostics follows the same section separation as the main settings groups');
  measurements.push({ viewport: '2048x1104-settings', ...settingsLayout });
  await capture('windows-reference-settings');
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.waitForFunction(() => !matchMedia('(prefers-color-scheme: dark)').matches &&
    getComputedStyle(document.querySelector('.settings-nav')).backgroundColor === 'rgb(244, 246, 247)');
  const lightContent = await rect('.settings-content');
  check(lightContent.fills === 'rgb(255, 255, 255)', `settings preserves a usable light system appearance: ${lightContent.fills}`);
  longThread = true;
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.reload();
  const latest = page.locator('.new-messages');
  const atBottom = () => page.waitForFunction(() => {
    const pane = document.querySelector('.timeline');
    return pane && pane.scrollHeight - pane.clientHeight > 500 && pane.scrollHeight - pane.clientHeight - pane.scrollTop < 4;
  });
  await atBottom();
  check(!await latest.isVisible(), 'a long transcript starts at the latest reply with no redundant return control');
  await page.getByRole('button', { name: '从输入栏打开工作空间文件', exact: true }).click();
  await preview.getByLabel('筛选已加载文件').waitFor();
  await tree.locator('.changed-file[title="docs/HANDOFF.md"]').click();
  if (await showDiff.getAttribute('aria-pressed') === 'true') await showDiff.click();
  await preview.getByRole('heading', { name: 'FORGE 项目交接记录' }).waitFor();
  await atBottom();
  const draft = page.getByRole('textbox', { name: '消息内容' });
  await draft.fill('保留这段草稿');
  const wheelAway = async (x) => {
    await page.mouse.move(x, 410);
    await page.mouse.wheel(0, -1000);
    await latest.waitFor();
    await page.waitForFunction(() => {
      const pane = document.querySelector('.timeline');
      return pane && pane.scrollHeight - pane.clientHeight - pane.scrollTop > 300;
    });
  };
  await wheelAway(820);
  const jump = await rect('.new-messages'), composer = await rect('.composer');
  check(jump.width === 32 && jump.height === 32 && Math.abs(jump.x + 16 - 792) <= 4 &&
    Math.abs(jump.x + 16 - (composer.x + composer.width / 2)) <= 2 &&
    Math.abs(composer.y - jump.y - 16 - 40) <= 8, 'ordinary desktop scroll exposes the centered 32px reference jump above the composer');
  await capture('windows-reference-return-latest');
  hasIncoming = true;
  const unread = page.getByRole('button', { name: /有新消息.*回到最新/ });
  await unread.waitFor({ timeout: 12000 });
  check(await page.locator('.timeline').evaluate(pane => pane.scrollHeight - pane.clientHeight - pane.scrollTop > 300), 'a real polled fixture reply announces itself without yanking the reading position');
  await unread.click();
  await atBottom();
  await latest.waitFor({ state: 'hidden' });
  check(await draft.inputValue() === '保留这段草稿', 'returning to the newest reply clears the prompt and preserves the unsent draft');
  await preview.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await preview.waitFor({ state: 'hidden' });
  await page.setViewportSize({ width: 390, height: 760 });
  await atBottom();
  await wheelAway(180);
  await page.mouse.wheel(0, 1400);
  await atBottom();
  await latest.waitFor({ state: 'hidden' });
  check(await draft.inputValue() === '保留这段草稿', 'manually scrolling the narrow conversation to its end dismisses the return control without dropping the draft');
  check(writes.length === 0 && unknown.length === 0, `no synthetic writes or unknown Core requests: ${JSON.stringify({ writes, unknown })}`);
  check(await page.evaluate(() => window.__layoutReferenceCalls.every(command => ['connection_get', 'runtime_status', 'workspace_operation_status', 'workspace_changes', 'workspace_files_list', 'workspace_file_read', 'workspace_file_diff'].includes(command))), 'only allowlisted synthetic runtime and file reads');
  return { checks, measurements };
}
