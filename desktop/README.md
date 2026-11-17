# FORGE desktop

React 18、Tauri 2、TypeScript、Vite 与分层原生 CSS。保留每个工作空间的一条长期对话：Router 负责协调，Worker 使用私有执行上下文，后台任务和交付回到同一条对话。

```powershell
npm test
npm run build
npm run dev -- --port 5173
```

没有新增应用依赖。原生调试沿用现有 Core runtime 设置；当前锁定 Rust 依赖在本机使用已安装的 Rust 1.90 验证。Git 变更预览需要 PATH 中存在 Git。

## 导航、任务与设置

默认只有一条长期对话。应用内容区参考 Windows 桌面截图，采用常驻应用菜单、窄工具栏与项目栏，保留真实工作区、已发现的只读项目和任务入口。已有消息时，当前项目下显示这条对话的标题。窄屏用覆盖式导航；应用菜单支持左右方向键切换。窗口主题遵循系统，不模拟系统窗口按钮，也不创建虚构会话、跨空间切换或内嵌浏览器。组件心跳在“设置 → 常规 → 诊断”；Ctrl+,（macOS ⌘+,）打开设置。浏览器开发版仍需输入本地 Core 启动密钥。

任务列表先显示名称和状态，点击进入详情。宽屏侧栏式抽屉，1000px 及以下为整页，不露出后方对话。内部 ID 和版本号仅在技术详情等需要核对的界面显示。对话末尾还显示少量后台任务进展和需要用户处理的事项。同任务的真实成功文件回执可直接打开历史记录，不把它推断为最终交付。短列表直接显示任务，长列表按需展开搜索、筛选和历史；轮询保留选择、折叠和滚动。

`/api/settings` 与任务读取提供 version 1 的 `project_context`：当前 workspace 的 ID、名称、根目录及默认项目键，明确 `scope=current_workspace`。侧栏与任务共用 `projectContext.ts`，同时解析 Core 实际存储的 JSON 字符串 metadata 与对象。metadata 仅用于分组，不授予目录权限；契约明确不支持跨空间切换或项目 CRUD。

设置按现有功能分为三组九项：常规、键盘快捷键、模型库、运行配置、用量与预算、记忆、触发器、供应商凭据、备份与恢复。采用左导航与右内容两栏，窄屏切换为顶部横向导航。分类栏通常为 288px，内容列最大 768px（含内部 20px 边距），导航行 30px、实际标签 14px/21px，设置行标签 13px/18.5714px、说明 12px/16px。表单选择 compact 输入与 md 按钮变体，28px 高、13px 字号。模型档案保留新增、编辑、启停，能力、价格与技术记录按需展开，未知值不推断为支持。

模型发现与固定样例测量使用现有 Provider；只有点击按钮才会调用，测量会消耗预算且失败不自动重试。触发器提供事件源注册、事件接收、定时任务和文件监听语义表单。后端只提供 pending 事件列表，UI 不将其冒充全部源、定时任务或监听清单；创建回执仅保留于本次打开页面。

运行配置同页提供供应商连接、Router 与 API Key；已有连接的标识和能力声明默认折叠，首次启用且缺少必填标识时自动展开。预算、并发和网络范围在高级设置中。配置与密钥仍分别保存：配置保持版本确认，保存密钥沿用系统凭据库及重启流程；“检查是否存在”只检查本机凭据，不冒充模型连接测试。旧“供应商凭据”入口继续可用。API Key 仅存于密码输入控件，既不进入配置 JSON，也不进入浏览器存储。

## 对话与文件工作区

视觉层参考提供的桌面端设计测量：用中性明暗色、轻边线和系统字体统一界面；输入框仅用近处小阴影，浮层和任务抽屉才使用较明显的双层阴影。更多菜单用轻微位移和淡入淡出，不拉伸内容；任务抽屉、任务详情和设置区保持短入场。系统“减少动态效果”设置会关闭这些位移动画。更多菜单可用方向键打开，Escape 关闭并回到菜单按钮；从任务抽屉按 Escape 返回原对话和草稿。设计参考包中的代码、字体、图标、会话及截图均不随应用分发。

空白首页保留居中的欢迎语，底部输入栏最大 920px，上方显示真实工作空间和运行环境。进入对话后，文件变更、执行记录和输入栏共用最大 736px 的阅读列；文件面板初始约占工作区宽度的 47%，仍可拖动和展开。顶栏显示最近成功保留的用户消息、实际文件改动及任务入口；运行活动和执行详情放在更多菜单，真实任务状态仍在对话记录内。请求尚未返回时显示“等待响应”，不把它当作后台任务状态。保留同一个输入框、发送键、草稿、输入法组合、Shift+Enter 换行和原请求 ID 重试语义。模型入口只打开运行配置，不会立即切换模型；输入栏的审批入口只打开现有审批页面，不授予权限或提交审批。

对话里有已记录的真实委派时才显示「子智能体」入口。展开后查看每个代理的任务和执行状态，点选进入独立的只读详情；返回不会停止代理，不会清空草稿或新开主对话。运行成功仅表示该 Worker 的结果已核验，不代表整个任务已完成。工作空间级运行活动仍在更多菜单，审批按原流程处理。

执行详情默认隐藏，已有对话可从更多菜单打开。它承载本对话的子智能体概览、变更和来源，打开文件预览或任务时暂时隐藏。来源仅来自已加载、真实成功的文件读取回执。详见[子智能体设计说明](../docs/CONVERSATION_SUBAGENTS.md)。

对话按用户消息划分显示区间，执行过程在同一阅读列中交错呈现助手进度、灰色工具摘要和子智能体动态。相邻同状态的子智能体事件合并为多头像与姓名句子，工具摘要点击后才展开原始命令、文件和回执。历史动态按真实事件显示；`worker.result` 表示已返回结果，仍需区分后续验证；没有生命周期事件的 activity 快照明确显示“当前状态”，不伪造开始或完成时刻。工具保留原来的时间顺序，每个回执仅显示一次，不再集中搬到子智能体条目内。

出现可外置的尾回复后，未操作的过程默认收起，最终回复及文件变更总结保持可见。手动展开过程或内部详情后，轮询和完成不会将其收起。当前消息 API 没有明确的最终回复通道；外置使用尾部普通助手回复且本显示区间没有已知活动运行的规则，任务通知和标记为 commentary 的消息仍在过程内，这只是显示分组，不判断任务已完成。折叠头显示“用时 X 分钟 Y 秒”：从本显示区间的用户请求时间起算，缺失时使用最早有效记录时间；活动运行每秒更新，结束后固定为尾回复或最后持久记录的时间差。它表示该区间的墙钟时间，不是模型推理或计费时长。记录缺少有效起止时间时显示“用时 — 分钟 — 秒”，单条无终止时间的运行快照不填零。点击右侧运行会打开外层过程并定位精确 Run 的详情。任务、计划、团队、审批和活动仍使用右侧抽屉，聊天、草稿、回执缓存与阅读位置继续保留。失败、未知与模拟标记独立显示，不生成思考正文。

记忆页提供 Chain of Recall 引用卡，按真实引用读取内容和关联分页；活动抽屉可按事件序号读取事件流。设置中的心跳按服务端 Unix 秒和超时区分正常、过期、未知和读取失败。

命令卡按原始桌面 bundle 实现 Shell 标题、命令/输出分别复制、144px 输出独立滚动、右下真实退出状态；圆角 12.5px、边框 0.8px，长命令可完整展开。文件写入/修订展开后直接读取精确不可变回执，在会话内显示路径、增删数和 diff，不必先打开右栏。diff 为 12px/21.6px、单行号、4px 红绿色条与整行底色，最大高 240px；原/新行号来自实际 unified diff hunk。折叠时不读取，展开后在内存保留回执及双轴阅读位置，轮询和活动拆组不重置；连接/空间/代次变化清除缓存。完整历史入口继续保留。

活动组中真实成功的写入/修订按路径去重，使用独立 turn diff 块，默认显示 3 个文件，可展开更多并预览；增删数只来自实际加载的不可变回执。刷新、活动独立轮询插入和结束折叠保留阅读锚点；底部阅读时继续跟随。历史事件与最新执行状态区分，记录未加载时明确提示。

工具栏的文件按钮，或对话里的文件引用，可打开右侧预览。文档在左、文件树在右；树内同时提供已更改目录与所有文件，过滤针对已加载文件，状态来自真实 Git。支持文件标签、面包屑、预览/源码切换、键盘切换/关闭、可拖动和键盘调整的分隔条、独立滚动及刷新。展开按钮可将预览扩展为完整文件工作区，保留聊天 DOM、草稿和阅读位置。开合继续使用用户要求的 150ms 宽度/透明度过渡，退出保留文档 DOM 并禁用隐藏控件，支持快速反向切换与减少动效。窗口缩放按原生 Root.H/Kg/g3 的断点变化收起/恢复右栏：工作区（含可见左导航）跨到 960px 以下且项目栏打开，或跨到 720px 以下时收起，放宽后恢复一次；手动开关清除恢复记录，连续更窄的断点按原文覆盖当时开关状态。展开意图、文件标签和滚动继续保留。FORGE 没有原生的 route/owner lease/tab retain 特例；720px 以下主动打开文件仍为全宽单面板，水平导航也是 FORGE 适配。

预览宽度直接移植原生 DUYS 的默认值和归一化比例算法。以实际主区宽 W 和 workspace 行高 H 计算 `max(320,min(1.6H,W−500),min(640,W−352))`，regular 区间为 320 至 `max(320,W−352)`。首个真实测量后冻结比例，窗口缩放继续使用同一比例；展开时全宽，恢复后沿用原比例。分隔器覆盖布局，不占额外列宽；双击恢复当前默认宽，拖到 160px 以下关闭，越界至聊天不足 160px 时展开。移除了此前 660px/41–64%、1424px 自动收栏和 1136px 单面板的自行校准规则。

文件树按原生 U5mJ/gbrq 共用一个初始 250px 宽度，夹在 200px 与父宽 60% 之间，CSS 额外限制 max-width 60%。左边缘可拖动，拖至 100px 以下隐藏；普通与完整工作区共享该状态。为支持现有“文件”入口，FORGE 的文件请求仍自动显示树。普通与展开状态采用 14px 标签/操作文字、13px 文件名、30px 树行、16px 文件图标、48px 文件工具栏。BaseFileTree 的实际覆盖为横向 padding 3px、gap 5px、层级间距 8.75px；移植 20 类 DMvp 内联文件 SVG、目录 chevron、悬停层级线和 20px 状态框。新增/修改/删除分别为原生 sectioned 树的 #00a240/#0285ff/#e02e2a，普通选中文字为正文色。原增删计数保留在无障碍文本中。

文件树开关位于工具栏最右侧，标签栏的“＋”使用现有文件选择能力：浏览器打开文件选择器，原生展开目录树并聚焦过滤。源码查找默认收起，通过“查找文件内容”或源码区 Ctrl+F 展开；Enter/Shift+Enter 定位，Escape 关闭并回到源码。窄预览在 560px 以下将工具栏文字折为图标，保留原可访问名称；这些入口安排是结合用户截图的 FORGE 适配，未增加原应用专属的编辑器启动接口。

- 源码：只读、行号、基本词法着色、按需展开的查找、复制、横向滚动/换行。非换行视图虚拟化；超过 5000 行关闭换行，避免长文件拖慢界面。
- Markdown：16px/24px 正文，文档内边距 12px 16px；预览/源码切换保留阅读位置，相对链接打开同空间文件。HTML 不执行，外链图片不加载，危险 URL 不渲染。
- PDF：使用实际读取字节创建并回收 Blob URL，交给内置查看器提供多页、缩放、查找和页面适配；不支持内嵌时提供明确说明与下载。
- Git：显示当前工作空间的真实变更与差异，支持子目录、重命名、删除、新文件、无 HEAD 仓库；工作树与 HEAD 相同但暂存区有修改时明确显示暂存差异。Git 读取不写索引。
- 工作空间变更与对话文件记录分别标明口径；对话汇总仅含已加载记录中真实成功的文件写入。
- 历史更改：展开成功文件写入/修订记录后选择“记录的更改”，按精确工具回执读取历史 diff、增删数与基础版本。历史标签与当前文件标签独立；相同文件的多个回执可并排切换。加载后增删数回填对应活动行。刷新、关闭重开保留阅读位置，连接/空间/代次变化清除旧内容。

历史数据由独立认证只读接口 `GET /api/artifacts/receipts/{receipt_id}/diff` 提供，验证完整执行/版本回执链，使用不可变 `action_intents` 中保存的正文，绝不以当前文件或 Git 补作历史基线。单版本上限 256 KiB/2000 行，最多 64 个版本、累计 4 MiB，diff 上限 512 KiB；过大返回 422，不可用/敏感路径/损坏回执返回不含内部细节的 404。模拟来源始终标注。来源跳转表示“关联任务的请求”，不证明该请求直接导致每个历史 run；未加载消息有明确提示，不冒充精确聊天轮次归属。

浏览器开发模式点击“文件”，选择本地目录或文件；不会把浏览器测试数据当作工作空间文件。目录导入移除共同顶层目录，保留相对文件引用。导入文件没有 Git 历史。

## 只读原生命令

四个命令只允许 main 窗口，绑定 CoreHost 启动时固定的工作空间根目录：

| 命令 | 参数 | 返回 |
| --- | --- | --- |
| workspace_files_list | path，根目录为空字符串 | 当前目录条目 |
| workspace_file_read | 工作空间相对 path | 文本或 PDF/图片字节与元数据 |
| workspace_changes | 无 | 当前范围 Git 状态及可用增删数 |
| workspace_file_diff | 相对 path | 真实差异、二进制/截断标志及 scope |

路径穿越、绝对路径、符号链接/重解析目录被拒绝；Windows 文件打开后再次核验句柄路径。文本仅 UTF-8、最大 2 MiB；PDF/图片最大 24 MiB；目录最多 5000 项；Git 输出最大 4 MiB、读取时限 8 秒。错误明确显示。新命令不执行 Worker 工具，也不改变审批、授权、维护和写入流程。Core 时间线仅补充 file_revise 的工具名和同规则过滤后的相对路径，不返回原始正文或输出。

## 回归与证据

复用已安装的 Playwright，保持 Vite 在 5173 运行。默认 fixture 检查单对话的发送、任务详情、返回草稿和窄窗口。其他 fixture 含历史布局验收，其中依赖固定侧栏或旧输入栏控件的断言不代表当前默认界面：

```powershell
$env:FORGE_PLAYWRIGHT_MODULE = 'C:/path/to/existing/playwright/module'
node tests/run-ui-smoke.mjs
node tests/run-ui-smoke.mjs ui-windows-reference.playwright.js
node tests/run-ui-smoke.mjs ui-layout.playwright.js
node tests/run-ui-smoke.mjs ui-workspace.playwright.js
node tests/run-ui-smoke.mjs ui-interactions.playwright.js
node tests/run-ui-smoke.mjs ui-conversation.playwright.js
node tests/run-ui-smoke.mjs ui-activity-files.playwright.js
node tests/run-ui-smoke.mjs ui-history.playwright.js
node tests/run-ui-smoke.mjs ui-settings-save.playwright.js
node tests/run-ui-smoke.mjs ui-risk-status.playwright.js
node tests/run-ui-smoke.mjs ui-inline-diff.playwright.js
node tests/run-ui-smoke.mjs ui-preview-tree.playwright.js
node tests/run-ui-smoke.mjs ui-forge-refactor.playwright.js
node tests/run-ui-smoke.mjs ui-agent-panel.playwright.js
node tests/run-ui-smoke.mjs ui-process-transcript.playwright.js
node tests/run-ui-smoke.mjs ui-forge-controls.playwright.js
node tests/run-ui-smoke.mjs ui-forge-settings.playwright.js
node tests/run-ui-smoke.mjs ui-activity-scroll.playwright.js
node tests/run-ui-smoke.mjs ui-approval-failure.playwright.js
$env:FORGE_PLAYWRIGHT_CHANNEL = 'msedge'
node tests/run-ui-smoke.mjs ui-preview.playwright.js
node tests/run-ui-scale.mjs
```

Windows 参考验收使用 1626×1038 和 2048×1104 两个视口，检查首页、对话、源码、Markdown 与设置的关键坐标、宽度和背景色。`FORGE_CAPTURE_UI=1` 生成截图。测试数据与原生文件调用均为合成，只验证 Chromium 中的前端，不代表 Windows 原生窗口、字体、系统 DPI、真实 Core 或整图逐像素相同。历史组使用合成历史 HTTP 和真实 File API 当前文件；预览组使用真实浏览器 File API、文本与两页 PDF 字节。历史验收用 Edge 验证内置 PDF；bundled headless Chromium 的 `pdfViewerEnabled=false`，产品会明确提示下载阅读。缩放矩阵需参考服务 8890，覆盖明暗主题及浏览器 DPR 1/1.25/1.5，不替代 Windows 系统 DPI。Rust 测试使用临时目录和真实 Git 仓库；含独立嵌入 Core 的 17 项历史测试通过。

原生开发地址固定为 `http://127.0.0.1:5173`，与 Vite 脚本一致；debug 导航仅允许本机 5173，release 顶层导航限制保留。本轮隔离窗口连接真实嵌入 Core，Windows AppliedDPI=120（125%），实际读取文件并显示 PDF 正文；后续窗口自动操作被用户 Esc 停止，没有继续操作用户窗口。

本轮六阶段改造见 [CODEX-REFACTOR-REPORT.md](D:/Competition_this/Agent/desktop/CODEX-REFACTOR-REPORT.md)，全部 50 条 `/api` 路由及 `/health` 的真实 UI 入口见 [API-UI-COVERAGE.md](D:/Competition_this/Agent/desktop/API-UI-COVERAGE.md)。旧文件预览与原生验证记录见 [REFACTOR-REPORT.md](D:/Competition_this/Agent/desktop/REFACTOR-REPORT.md)。没有 localStorage/sessionStorage。本轮没有执行真实付费 Provider、原生 release 打包或新增 Windows DPI 矩阵；既有 Tauri 契约保持不变。
