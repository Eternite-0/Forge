// Synthetic Chromium logical viewport resizing, not native Windows/Tauri validation.
// Synthetic read-only long thread; actual wheel events and responsive pane layout.
async (page, { outputDir }) => {
  const checks = [], missing = [], writes = [], external = [];
  const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const base = process.env.FORGE_TEST_URL || 'http://127.0.0.1:5173/';
  const origin = new URL(base).origin;
  const workspaceRoot = 'C:/Fixture/FORGE';
  const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (url.origin === origin && method === 'GET') return route.continue();
    if (url.origin !== 'http://127.0.0.1:8765') { external.push(`${method} ${url.origin}${url.pathname}`); return route.abort('blockedbyclient'); }
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (method !== 'GET') { writes.push(`${method} ${url.pathname}`); return route.fulfill({ status: 405, headers, json: { detail: 'Read-only resize fixture' } }); }
    let json;
    if (url.pathname === '/health') json = { session: 'resize-fixture' };
    else if (url.pathname === '/api/settings') json = { revision: 1, config: {}, workspace_id: 'resize-workspace', workspace_root: workspaceRoot, provider_configured: true, provider_id: 'synthetic-provider', router_model_id: 'synthetic-model', context_limit: 32768 };
    else if (url.pathname === '/api/restore-status') json = { fenced: false };
    else if (url.pathname === '/api/conversation') json = { messages: Array.from({length:60},(_,i)=>({id:`anchor-message-${i}`,role:i%2?"assistant":"user",content:`Known message ${i}. `+"Deterministic long thread content for actual reading anchor verification. ".repeat(8),created_at:`2026-11-16T08:${String(i).padStart(2,"0")}:00Z`})), router: {}, has_more: false };
    else if (['/api/quests', '/api/timeline', '/api/approvals/pending', '/api/activity'].includes(url.pathname)) json = { items: [], has_more: false };
    else if (url.pathname === '/api/conversations/main/subagents') json = { conversation_id: 'main', workspace_id: 'resize-workspace', items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/runtime/health') json = { components: [] };
    else if (url.pathname === '/api/projects/discovered') json = [];
    else if (url.pathname === '/api/budget') json = { token_limit: 10000, concurrency_limit: 2 };
    else { missing.push(url.pathname); return route.fulfill({ status: 404, headers, json: { detail: 'Unknown resize fixture route' } }); }
    return route.fulfill({ headers, json });
  });
  await page.setViewportSize({width:2048,height:1104});
  await page.goto(base);
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button',{name:'连接',exact:true}).click();
  await page.locator('.simple-connection').waitFor({state:'hidden'});
  const timeline=page.locator('.timeline');
  await page.getByLabel('消息内容',{exact:true}).fill('Unsent resize anchor verification draft');
  await page.getByRole('button',{name:'从输入栏打开工作空间文件',exact:true}).click();
  await page.waitForTimeout(500);
  const keys=await timeline.locator('[data-scroll-key]').evaluateAll(els=>els.map(el=>el.dataset.scrollKey));
  const key=keys.find(k=>k==='message:anchor-message-25');
  check(!!key,'known fixture message exists in actual transcript');
  const anchor=timeline.locator(`[data-scroll-key="${key}"]`);
  const box=await timeline.boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  await page.mouse.wheel(0,-500);
  await page.waitForTimeout(200);
  const delta=await anchor.evaluate(el=>el.getBoundingClientRect().top-document.querySelector('.timeline').getBoundingClientRect().top-60);
  await page.mouse.wheel(0,delta);
  await page.waitForTimeout(300);
  const measure=()=>anchor.evaluate(el=>{const t=document.querySelector('.timeline');return {key:el.dataset.scrollKey,text:el.textContent,offset:el.getBoundingClientRect().top-t.getBoundingClientRect().top,scroll:t.scrollTop,previewVisible:document.querySelector('.file-preview-workspace').getBoundingClientRect().width>0};});
  const before=await measure();
  check(Math.abs(before.offset-60)<=1,'wheel parks known message at the intended reading offset');
  check(before.previewVisible,'wide layout starts with the file pane open');
  await page.setViewportSize({width:900,height:600}); await page.waitForTimeout(500);
  const narrow=await measure();
  check(!narrow.previewVisible,'900 logical viewport auto-hides the split file pane');
  check(Math.abs(before.offset-narrow.offset)<=1,'narrow layout retains the same message reading offset');
  await page.setViewportSize({width:2048,height:1104}); await page.waitForTimeout(500);
  const after=await measure();
  await page.screenshot({path:`${outputDir}/anchor-restored-wide.png`});
  check(before.key===after.key&&before.text===after.text,'same known message retained');
  check(after.previewVisible,'returning to wide layout restores the file pane');
  check(Math.abs(before.offset-after.offset)<=1,`restored wide message offset: ${before.offset} -> ${after.offset}`);
  check(await page.getByLabel('消息内容',{exact:true}).inputValue()==='Unsent resize anchor verification draft','unsent draft retained');
  await page.getByRole('button',{name:/回到最新/}).click();
  const bottomDistance=()=>timeline.evaluate(el=>el.scrollHeight-el.clientHeight-el.scrollTop);
  await page.waitForTimeout(200);
  check(Math.abs(await bottomDistance())<=1,'return control restores actual latest-message following');
  for (const viewport of [{width:900,height:600},{width:2048,height:1104}]) {
    await page.setViewportSize(viewport); await page.waitForTimeout(500);
    check(Math.abs(await bottomDistance())<=1,`${viewport.width}: resize preserves follow-bottom rather than restoring old reading anchor`);
  }
  check(await page.getByLabel('消息内容',{exact:true}).inputValue()==='Unsent resize anchor verification draft','follow-bottom resize also preserves unsent draft');
  check(!writes.length&&!missing.length&&!external.length,'no writes/unmatched/external traffic');
  return {checks,before,narrow,after,evidence:"Synthetic Chromium logical viewport resize, not native Windows/Tauri."};
}
