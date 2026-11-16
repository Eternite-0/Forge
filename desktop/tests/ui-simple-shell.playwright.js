// Messenger-first default path. All backend responses here are synthetic.
async (page) => {
  const checks = [], posts = [], messages = [];
  const check = (ok, label) => { if (!ok) throw new Error(label); checks.push(label); };
  const id = '1234567890abcdef1234567890abcdef';
  const quest = { id, title: id, goal: '整理项目资料', status: 'running', version: 1 };
  const time = '2026-11-16T08:00:00Z';
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request(), { pathname } = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    let json = { items: [], has_more: false };
    if (pathname === '/health') json = { session: 'simple-shell' };
    else if (pathname === '/api/settings') json = { revision: 1, config: {}, workspace_root: '/fixture/FORGE', provider_configured: true };
    else if (pathname === '/api/restore-status') json = { fenced: false };
    else if (pathname === '/api/conversation') json = { messages, router: {}, has_more: false };
    else if (pathname === '/api/quests') json = { items: [quest], has_more: false };
    else if (pathname === `/api/quests/${id}`) json = { quest, workers: [] };
    else if (pathname.endsWith('/grants') || pathname.endsWith('/plan/history')) json = [];
    else if (pathname === '/api/conversation/message') {
      const posted = request.postDataJSON();
      posts.push(posted);
      const user_message = { id: 'user-fixture', role: 'user', content: posted.content, created_at: time, metadata: { request_id: posted.request_id } };
      const assistant_message = { id: 'assistant-fixture', role: 'assistant', content: '整理好了。', created_at: '2026-11-16T08:00:01Z' };
      messages.push(user_message, assistant_message);
      json = { user_message, assistant_message };
    }
    return route.fulfill({ headers, json });
  });
  await page.goto('http://127.0.0.1:5173/');
  const composer = page.getByLabel('消息内容');
  const send = page.getByRole('button', { name: '发送消息', exact: true });
  const tasks = page.getByRole('button', { name: '查看任务', exact: true });
  const more = page.getByRole('button', { name: '更多对话操作' });
  check(await page.locator('.rail, .desktop-chrome').count() === 0, 'default has no sidebar or secondary titlebar');
  check(await composer.count() === 1 && await send.isDisabled(), 'single composer cannot send empty messages');
  check(await page.locator('.composer button').count() === 1, 'only send is in the composer');
  const ready = page.waitForResponse(response => new URL(response.url()).pathname === '/api/quests');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await ready;
  await composer.fill('替我整理项目资料');
  await tasks.click();
  const drawer = page.locator('.task-inspector');
  const taskRow = drawer.getByRole('button', { name: /^整理项目资料，/ });
  await taskRow.waitFor();
  check(!(await drawer.innerText()).includes(id), 'task list shows a human goal instead of the internal ID');
  check(await drawer.locator('.quest-detail').count() === 0, 'opening Tasks shows the list first');
  await taskRow.click();
  await drawer.locator('.quest-detail').waitFor();
  check(!(await drawer.locator('.quest-technical').evaluate(element => element.open)), 'task technical details are closed by default');
  await drawer.getByRole('button', { name: /返回任务列表/ }).click();
  await page.getByRole('button', { name: '关闭任务详情' }).click();
  check(await composer.inputValue() === '替我整理项目资料', 'task detail navigation preserves the draft');
  await more.click();
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  check(await composer.inputValue() === '替我整理项目资料', 'settings navigation preserves the draft');
  await composer.press('Enter');
  await page.getByText('整理好了。', { exact: true }).waitFor();
  check(posts.length === 1 && posts[0].content === '替我整理项目资料' && await composer.inputValue() === '', 'one send shows the response and clears draft');
  await page.setViewportSize({ width: 390, height: 650 });
  check(await composer.isVisible() && await tasks.isVisible(), 'narrow windows keep the two main actions available');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'no horizontal page overflow');
  return { checks };
}
