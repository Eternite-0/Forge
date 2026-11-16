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
      '', '### 阅读与查找', '', '文件树保留独立滚动，切换当前文件不会覆盖同一对话。'
    ].join('\n');
    const source = [
      '"""Reference preview with deterministic local fixture text."""', '', 'from pathlib import Path', '', 'RESULT = "layout verification"', '',
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
      if (command === 'workspace_file_diff') return { scope: 'worktree', text: `--- a/${args.path}\n+++ b/${args.path}\n@@ -0,0 +1,35 @@\n${source.split('\n').map(line => '+' + line).join('\n')}` };
      throw new Error('unexpected synthetic native command: ' + command);
    } };
  }, taskEquivalents);
  let populated = false;
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
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), { pathname } = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (request.method() !== 'GET') { writes.push(pathname); return route.fulfill({ status: 405, headers, json: { detail: 'Fixture is read-only' } }); }
    const lists = { items: [], next_before: null, has_more: false };
    let json;
    if (pathname === '/health') json = { session: 'layout-fixture' };
    else if (pathname === '/api/conversation') json = { ...lists, messages: populated ? messages : [], router: {} };
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
  await page.mouse.move(0, 0);
  const home = { titlebar: await rect('.desktop-chrome'), workspace: await rect('.chat-workspace'), composer: await rect('.composer'), context: await rect('.composer-workspace-context'), banner: await rect('.empty-conversation h2') };
  check(home.titlebar.height === 54 && Math.abs(home.workspace.x - 424) <= 2 && Math.abs(home.workspace.y - 54) <= 2, '1626 home matches the reference outer frame and sidebar coordinates');
  check(home.composer.width >= 908 && home.composer.width <= 926 && Math.abs(home.composer.y - 892) <= 9 && home.composer.fills === 'rgb(47, 47, 47)', `home composer matches the reference width, baseline, and sampled dark fill: ${JSON.stringify(home.composer)}`);
  check(home.context.y < home.composer.y && Math.abs(home.context.x - home.composer.x - 16) <= 3 && Math.abs(home.banner.y - 495) <= 30, 'workspace context above the composer and the centered home heading match figure 1');
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
  measurements.push({ viewport: '2048x1104', ...work });
  await capture('windows-reference-source');
  await tree.locator('.changed-file[title="docs/HANDOFF.md"]').click();
  const showDiff = preview.getByRole('button', { name: '显示更改', exact: true });
  if (await showDiff.getAttribute('aria-pressed') === 'true') await showDiff.click();
  await preview.getByRole('heading', { name: 'FORGE 项目交接记录' }).waitFor();
  check(await preview.getByRole('tab', { name: 'analysis.py', exact: true }).count() === 1 && await preview.getByRole('tab', { name: 'HANDOFF.md', exact: true }).count() === 1, 'file tabs keep source and actual rendered Markdown open');
  await page.mouse.move(0, 0);
  await capture('windows-reference-markdown');
  await preview.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await page.locator('.titlebar').getByRole('menuitem', { name: '文件', exact: true }).click();
  await page.getByRole('menu', { name: '文件菜单' }).getByRole('menuitem', { name: /^设置…/ }).click();
  await page.getByRole('heading', { name: '常规', exact: true }).waitFor();
  await page.waitForFunction(() => Math.abs(document.querySelector('.settings-nav').getBoundingClientRect().left - 52) < 3);
  await page.mouse.move(0, 0);
  const settingsLayout = { navigation: await rect('.settings-nav'), heading: await rect('.settings-title'), group: await rect('.settings-group') };
  check(Math.abs(settingsLayout.navigation.x - 52) <= 3 && Math.abs(settingsLayout.navigation.width - 288) <= 3 && Math.abs(settingsLayout.group.width - 728) <= 3, `settings matches figure 4 rail, navigation and content widths: ${JSON.stringify(settingsLayout)}`);
  check(Math.abs(settingsLayout.heading.x - 823) <= 7 && Math.abs(settingsLayout.heading.y - 78) <= 10, 'settings heading aligns to the centered reference reading column');
  measurements.push({ viewport: '2048x1104-settings', ...settingsLayout });
  await capture('windows-reference-settings');
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.waitForFunction(() => !matchMedia('(prefers-color-scheme: dark)').matches &&
    getComputedStyle(document.querySelector('.settings-nav')).backgroundColor === 'rgb(244, 246, 247)');
  const lightContent = await rect('.settings-content');
  check(lightContent.fills === 'rgb(255, 255, 255)', `settings preserves a usable light system appearance: ${lightContent.fills}`);
  check(writes.length === 0 && unknown.length === 0, `no synthetic writes or unknown Core requests: ${JSON.stringify({ writes, unknown })}`);
  check(await page.evaluate(() => window.__layoutReferenceCalls.every(command => ['connection_get', 'runtime_status', 'workspace_operation_status', 'workspace_changes', 'workspace_files_list', 'workspace_file_read', 'workspace_file_diff'].includes(command))), 'only allowlisted synthetic runtime and file reads');
  return { checks, measurements };
}
