// Synthetic Chromium logical viewport resizing, not native Windows/Tauri validation.
// Real File API documents; synthetic read-only Core; no providers or external writes.
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
    if (method !== 'GET') { writes.push(`${method} ${url.pathname}`); return route.fulfill({ status: 405, headers, json: { detail: 'Read-only resize fixture' } }); }
    let json;
    if (url.pathname === '/health') json = { session: 'resize-fixture' };
    else if (url.pathname === '/api/settings') json = { revision: 1, config: {}, workspace_id: 'resize-workspace', workspace_root: longWorkspace, provider_configured: true, provider_id: 'synthetic-provider', router_model_id: 'synthetic-model', context_limit: 32768 };
    else if (url.pathname === '/api/restore-status') json = { fenced: false };
    else if (url.pathname === '/api/conversation') json = { messages: [], router: {}, has_more: false };
    else if (['/api/quests', '/api/timeline', '/api/approvals/pending', '/api/activity'].includes(url.pathname)) json = { items: [], has_more: false };
    else if (url.pathname === '/api/runtime/health') json = { components: [] };
    else if (url.pathname === '/api/projects/discovered') json = [];
    else if (url.pathname === '/api/budget') json = { token_limit: 10000, concurrency_limit: 2 };
    else { missing.push(url.pathname); return route.fulfill({ status: 404, headers, json: { detail: 'Unknown resize fixture route' } }); }
    return route.fulfill({ headers, json });
  });
  const settle = async () => { await page.waitForTimeout(300); await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))); };
  await page.setViewportSize({ width:1440, height:900 });
  await page.goto(base);
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', {name:'连接',exact:true}).click();
  await page.locator('.simple-connection').waitFor({state:'hidden'});
  const draft = page.getByLabel('消息内容',{exact:true});
  await draft.fill('resize retained draft');
  const draftNode = await draft.elementHandle();
  const launcher = page.getByRole('button',{name:'从输入栏打开工作空间文件',exact:true});
  await launcher.click();
  const preview = page.getByLabel('工作空间文件预览',{exact:true});
  const chooserPromise = page.waitForEvent('filechooser');
  await preview.getByRole('button',{name:'打开文件',exact:true}).click();
  await (await chooserPromise).setFiles([
    {name:'README.md',mimeType:'text/markdown',buffer:Buffer.from('# Resize fixture\n\n'+Array.from({length:180},(_,i)=>`Paragraph ${i}: retained Markdown reading position.\n\n`).join(''))},
    {name:'source.ts',mimeType:'text/plain',buffer:Buffer.from(Array.from({length:1200},(_,i)=>`export const row${i} = ${i};`).join('\n'))},
  ]);
  await preview.locator('.file-tree-row').filter({hasText:'README.md'}).click();
  const markdown = preview.locator('.document-rendered:visible');
  await markdown.waitFor();
  await markdown.evaluate(el=>el.scrollTop=700);
  await preview.getByRole('button',{name:'查看源代码',exact:true}).click();
  check((await preview.locator('.source-line:visible').first().innerText()).includes('# Resize fixture'),'real Markdown source visible');
  await preview.getByRole('button',{name:'预览',exact:true}).click();
  check(Math.abs(await markdown.evaluate(el=>el.scrollTop)-700)<=1,'Markdown source toggle preserves reading');
  await preview.locator('.file-tree-row').filter({hasText:'source.ts'}).click();
  const source = preview.getByLabel('source.ts 源码',{exact:true});
  await source.waitFor();
  await source.evaluate(el=>el.scrollTop=8000);
  const sourceNode = await source.elementHandle();
  const divider = page.getByRole('separator',{name:'调整文件预览宽度'});
  const expand = preview.getByRole('button',{name:'展开文件预览',exact:true});
  const verify = async label => {
    await settle();
    const geometry = await page.evaluate(()=>{
      const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
      return {width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,rail:rect('.rail'),preview:rect('.file-preview-workspace'),workspace:rect('.chat-workspace')};
    });
    measurements.push({label,...geometry});
    check(geometry.documentWidth<=geometry.width+1,`${label}: no document horizontal overflow`);
    check(geometry.preview.right<=geometry.width+1 && geometry.preview.bottom<=geometry.height+1,`${label}: pane remains within viewport`);
    check(await draftNode.evaluate(el=>el.isConnected&&el.value==='resize retained draft'),`${label}: same draft DOM and content retained`);
    check(await sourceNode.evaluate(el=>el.isConnected&&Math.abs(el.scrollTop-8000)<=1),`${label}: same source DOM and exact reading retained`);
    check(await preview.getByRole('tab',{name:'source.ts',exact:true,includeHidden:true}).getAttribute('aria-selected')==='true',`${label}: active source tab retained`);
  };
  await verify('1440 split');
  await divider.focus(); await divider.press('ArrowLeft');
  check(await divider.evaluate(el=>document.activeElement===el),'keyboard splitter retains focus');
  await page.getByRole('button',{name:'收起工作空间侧栏',exact:true}).click();
  await verify('1440 left sidebar collapsed');
  await page.getByRole('button',{name:'展开工作空间侧栏',exact:true}).click();
  await verify('1440 left sidebar restored');
  for (const expanded of [false,true]) {
    if (expanded) await expand.click();
    await preview.getByRole('tab',{name:'source.ts',exact:true}).focus();
    await page.setViewportSize({width:900,height:600});
    await verify(`900x600 ${expanded?'expanded':'split'}`);
    if (!expanded) check(await page.evaluate(()=>{const el=document.activeElement,r=el?.getBoundingClientRect();return !!el?.closest('.conversation-shell')&&!el.closest('[inert]')&&r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight;}),'auto-hidden focused preview returns focus to visible conversation control');
    await page.screenshot({path:`${outputDir}/windows-resize-900-${expanded?'expanded':'autohidden'}.png`});
    check(await preview.isVisible()===expanded,`900 logical viewport ${expanded?'keeps expanded pane visible':'auto-hides split pane while retaining it'}`);
    if (expanded) {
    await source.focus(); await page.keyboard.press('Control+f');
    const find=preview.getByLabel('在文件中查找',{exact:true}).filter({visible:true});
    await find.waitFor();
    check(await find.evaluate(el=>document.activeElement===el),'900 file find receives keyboard focus');
    await find.press('Escape');
    }
    const narrowFocus = !expanded ? await page.evaluateHandle(()=>document.activeElement) : null;
    await page.setViewportSize({width:1440,height:900});
    await verify(`widened ${expanded?'expanded':'split'}`);
    check(await expand.getAttribute('aria-pressed')===String(expanded),'widening preserves expansion preference');
    if (narrowFocus) check(await narrowFocus.evaluate(el=>document.activeElement===el),'auto-restoring preview on widening does not steal conversation focus');
  }
  await preview.getByRole('tab',{name:'README.md',exact:true}).click();
  check(Math.abs(await markdown.evaluate(el=>el.scrollTop)-700)<=1,'Markdown reading survives source tabs and resize cycle');
  await preview.getByRole('tab',{name:'source.ts',exact:true}).click();
  await preview.getByRole('button',{name:'关闭文件预览',exact:true}).click();
  await preview.waitFor({state:'hidden'});
  check(await draft.inputValue()==='resize retained draft','closing expanded pane retains draft');
  await draft.focus(); await draft.press('End');
  check(await draft.evaluate(el=>document.activeElement===el&&!el.closest('[inert]')),'closed pane leaves composer keyboard usable');
  await launcher.click();
  await verify('reopened expanded');
  await expand.click();
  await verify('restored split');
  check(await divider.isVisible(),'restoring split exposes keyboard divider');
  const sourceTab=preview.getByRole('tab',{name:'source.ts',exact:true});
  await sourceTab.focus(); await page.keyboard.press('Delete'); await settle();
  const readmeTab=preview.getByRole('tab',{name:'README.md',exact:true});
  check(await readmeTab.evaluate(el=>document.activeElement===el),'Delete active tab focuses surviving tab');
  check(await preview.getByRole('tab').count()===1,'Delete removes only requested file tab');
  const closeReadme=preview.getByRole('button',{name:/^关闭 .*README\.md$/});
  await closeReadme.focus(); await page.keyboard.press('Enter'); await settle();
  check(await preview.getByRole('tab').count()===0,'keyboard close removes final tab');
  check(await preview.getByRole('button',{name:'打开文件',exact:true}).evaluate(el=>document.activeElement===el),'last tab close focuses Open File fallback');
  await preview.getByRole('button',{name:'关闭文件预览',exact:true}).click();
  await page.setViewportSize({width:900,height:600});
  await settle();
  await draft.focus(); await draft.press('End');
  check(await draft.evaluate(el=>document.activeElement===el&&el.getBoundingClientRect().bottom<=innerHeight),'900 closed-pane composer remains visible and keyboard usable');
  check(!writes.length&&!missing.length&&!external.length,`strict read-only fixture: ${JSON.stringify({writes,missing,external})}`);
  return {checks,measurements,evidence:'Synthetic Chromium logical viewport resize, not native Windows/Tauri.'};
}
