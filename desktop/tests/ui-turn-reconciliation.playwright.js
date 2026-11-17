// Fault-injected transport: no external Core, Provider or tool is contacted.
async (page, { outputDir }) => {
  const checks = [], posts = [], lookups = [], messages = [];
  const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const responses = new Map();
  const makeTurn = ({ content, request_id }, round) => ({
    user_message: { id: `user-${round}`, role: "user", content, created_at: `2026-01-01T00:00:0${round}Z`, metadata: { request_id } },
    assistant_message: { id: `assistant-${round}`, role: "assistant", content: `验证回执 ${round}`, created_at: `2026-01-01T00:00:1${round}Z`, metadata: { execution_mode: "mock" } },
  });
  await page.route("http://127.0.0.1:8765/**", async route => {
    const req = route.request(), url = new URL(req.url());
    const headers = { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "GET,POST,OPTIONS" };
    const json = (value, status = 200) => route.fulfill({ status, headers, json: value });
    if (req.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    if (url.pathname === "/health") return json({ session: "transport-test-only" });
    if (url.pathname === "/api/conversation/message" && req.method() === "POST") {
      const body = req.postDataJSON();
      posts.push(body);
      if (posts.length === 1 || posts.length === 3) {
        const turn = makeTurn(body, posts.length);
        responses.set(body.request_id, turn);
        messages.push(turn.user_message, turn.assistant_message);
        if (posts.length === 3) return json(turn);
      }
      return route.abort("failed");
    }
    if (url.pathname === "/api/conversation/turn") {
      const id = url.searchParams.get("request_id");
      lookups.push(id);
      return json(responses.has(id) ? { state: "committed", ...responses.get(id) } : { state: "unknown" });
    }
    if (url.pathname === "/api/conversation") return json({ messages, router: { conversation_id: "main" }, has_more: false });
    if (url.pathname === "/api/settings") return json({ workspace_id: "fixture", workspace_root: "/fixture", router_model_id: "fixture-model", provider_configured: true, restore_fenced: false });
    if (url.pathname === "/api/restore-status") return json({ fenced: false });
    if (url.pathname === "/api/budget") return json({});
    if (url.pathname === "/api/models") return json([]);
    return json({ items: [], has_more: false });
  });
  await page.goto(process.env.FORGE_TEST_URL || "http://127.0.0.1:5173/");
  await page.getByLabel("Core 启动密钥").fill("a1".repeat(32));
  await page.getByRole("button", { name: "连接", exact: true }).click();
  const composer = page.getByLabel("消息内容");
  const send = page.getByRole("button", { name: "发送消息", exact: true });
  await composer.fill("响应丢失但提交完成");
  await send.click();
  await page.getByText("验证回执 1", { exact: true }).waitFor();
  check(await page.getByText("验证回执 1", { exact: true }).count() === 1, "committed response recovers exactly once");
  check(posts.length === 1 && lookups[0] === posts[0].request_id, "lost response is resolved by read-only receipt lookup, never a second POST");
  await composer.fill("未确认的请求");
  await send.click();
  await page.getByText(/未能确认这条消息是否提交成功/).first().waitFor();
  check(posts.length === 2 && lookups.length === 2, "unknown turn remains unknown without an automatic retry");
  check(await composer.inputValue() === "未确认的请求", "unknown turn keeps the user's draft");
  await send.click();
  await page.getByText("验证回执 3", { exact: true }).waitFor();
  check(posts.length === 3 && posts[1].request_id === posts[2].request_id, "only explicit retry resubmits with the original idempotency key");
  check(await page.getByText("验证回执 3", { exact: true }).count() === 1, "explicit retry reconciles once");
  const screenshot = `${outputDir}/grokbot-turn-reconciliation.png`;
  await page.screenshot({ path: screenshot, fullPage: true });
  return { checks, screenshots: [screenshot] };
}
