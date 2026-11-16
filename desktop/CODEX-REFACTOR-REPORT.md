# FORGE 前端六阶段改造验收记录

日期：2026-11-16。工作目录：`D:/Competition_this/Agent/desktop`。目标是按本机 `PROMPT-for-forge-refactor.md` 将既有前端改为 Codex 风格，并保留每个工作空间的一条长期对话、现有 HTTP 与原生命令契约。

## 用户截图纠正后的当前行为

后续对话记录纠正以文末“对话执行过程纠正”一节为准。右侧 Agent 卡片沿用本节尺寸与资源；对话里的子智能体概览条目已替换为按原始事件排列的过程动态。

初版遗漏了右侧 Agent 概览及花形身份图标，单靠内联条目和 Worker 浮层未达到用户截图要求。当前版新增右侧 Agent 卡片、替换旧浮层；对话条目默认收起，模型与工具元数据移入展开内容，用户主动展开后不随轮询或完成收起。以下初版六阶段与 17 组验证属于历史记录，本次纠正的复验另列于文末。

卡片尺寸核对本机原生 `features-all/splitView/389065.fc198fe813__HWx4.js` 及 `Nwje` 组件：宽 300px、圆角 16px、左右内边距 20px；截图的 376px 宽来自 125% 缩放。深色底色 `#262626`，分割线 `#383838`。原生黄、粉、绿花形 SVG 已原样复制到 `public/agent-avatars/`，各含明暗版本，头像 16px；稳定 Worker ID 绑定同一颜色，状态图标独立。当前只选取截图三款，没有复制原生全套 28 款分配规则。

卡片在宽屏默认显示，1180px 以下由工具栏 Agent 按钮打开浮层，390px 保留可用 Composer；文件预览和任务抽屉与其互斥。变更、对话文件记录、来源继续调用既有入口；来源只从成功的真实 `file_read` 回执生成，后台进程只从正在运行的真实命令生成。接口没有提供的信息不以示例进程或虚构来源填充。失败、未知记录保留明确状态，活动读取失败清除陈旧的当前头像与状态。

## 六阶段结果

| 阶段 | 最终实现 | 阶段门禁 |
|---|---|---|
| 1 设计系统 | `light-dark()` 自动双主题、共享 token/reset、按功能拆分 CSS；保留全局 border-box 与减少动效支持 | 单测 18 项、生产构建通过 |
| 2 布局 | 260px 单栏侧栏、36px 标题栏、应用菜单、内存中的前进/后退、完全收起与窄屏浮层；App 拆为状态、行为 hooks 与呈现组件 | 单测 18 项、生产构建通过 |
| 3 会话 | MessageList/UserMessage/AssistantMessage/RunStatus/ToolItem、独立 Markdown 和 TurnDiff；保留安全链接、代码复制、命令滚动和不可变文件回执 | 单测 18 项、生产构建通过 |
| 4 Composer | 方案 B：保留 textarea、IME、草稿和请求 ID 重试；统一消息列宽、15px 圆角、上下文/权限/模型入口 | 单测 18 项、构建及发送回归通过 |
| 5 子智能体 | 精确 run ID 的内联角色/模型/状态/工具流；新运行无事件时可见，结束后自动收起；任务/团队/计划/审批详情改右侧抽屉 | 单测 24 项、构建及内联子智能体回归通过 |
| 6 设置与接口 | 两栏设置与窄屏横向导航；模型发现/观测/测量、预算、记忆、触发器、审批详情/创建/撤销、引用链、事件流、组件健康；补齐任务高级操作 | 单测 31 项、生产构建与浏览器验收通过 |

本轮未增加应用依赖，未修改 `api.ts` 的 `request<T>` 签名或 `src-tauri/`。后端文件及仓库中原有的后端/文档工作保持原状，没有通过改后端来满足前端验收。未创建提交或执行发布。

## 按实际契约实现

当前源码有 **50 条 `/api` 路由，加一条 `/health`**，原 Prompt 的“48”已过时。每条路由的 UI 路径、实现文件及限制见 [API-UI-COVERAGE.md](D:/Competition_this/Agent/desktop/API-UI-COVERAGE.md)。

- 后端没有 `GET /api/quests/{id}/workers`。身份与提案来自 `GET /api/quests/{id}`，实时执行来自 `/api/activity`；新增 Worker 使用既有 POST 查询参数契约。
- Composer 选择模型会进入运行配置预填；保存并重启 Core 后生效。界面保留真实活动模型，没有把预填伪装成即时切换。未添加没有接口的分支/推理强度控制。
- 发现项目只读展示，不作为工作空间切换或目录访问授权。
- 定时任务、文件监听、事件源没有全量查询、编辑、删除接口。设置展示 pending 事件与本次创建回执，并明确范围。
- 团队重分配保留角色、输出合约、工具范围与预算；现有 TeamWire 无法表达的累计 Token 分配会被明确拒绝，避免静默降低约束。
- 审批决定绑定精确参数摘要和计划版本；读取详情失败后禁用旧操作，支持显式重试。写操作有同步重入锁，409 保留错误，不自动重放。输入中的非有限 JSON 数值被拒绝，避免序列化改写精确参数。
- 心跳按 Unix 秒及服务端 timeout 判断。过期、时间未知、读取失败不会显示正常。事件流明确为全部任务，按 seq 游标去重。

## 工程尺寸与样式取舍

| 文件/分层 | 大小 |
|---|---:|
| App.tsx | 3,742 B |
| ChatTimeline.tsx | 8,001 B |
| style.css 导入入口 | 347 B |
| 最大功能 CSS：conversation.css | 19,931 B |
| settings.css（含窄屏） | 19,779 B |

样式组织为 **2 个基础文件（tokens/base）＋6 个功能模块（layout/conversation/composer/settings/files/panels）**，每份均低于 20,000 B。侧栏与标题栏同属 layout。Prompt 一处明确要求 tokens/base 加至少五类功能文件，另一处要求总数不超过六份，二者按字面矛盾；本实现保留基础层与六个职责清晰的功能模块，没有为凑总文件数压缩源码或混合不相关规则。

Vite 将现有 Markdown 依赖独立分包。生产构建没有 TypeScript 错误或 500KB chunk 警告；分包没有新增依赖。

## 最终验证

`npm test`：**31/31 通过**。覆盖认证、请求 ID/IME、历史分页与代次防旧回包、项目归属、不可变回执、团队合约转换、合法任务流转、秒级心跳、触发器载荷与路径、模型维度和子智能体精确归属。

`npm run build`：**通过**。所有最终源文件经 TypeScript 检查并生成生产资源。构建产物由 Vite 生成，没有手工编辑 dist。

浏览器使用已安装的 Playwright 与 Edge，无新安装。**17 组浏览器回归全部通过，均无未捕获错误**。以下场景使用合成 HTTP；设置凭据回归使用合成 Tauri bridge；文件预览使用真实浏览器 File API 与两页 PDF 字节。均没有调用真实 Provider 或执行 Worker 工具。

| 浏览器用例 | 主要证据 | 结果 |
|---|---|---|
| ui-forge-refactor | 260/36 几何、菜单/前进后退、草稿保留、内联子智能体去重/完成收起、系统明暗、390px | 通过，0 未捕获错误 |
| ui-forge-controls | Composer 模型预填与真实当前模型、心跳过期/未知/失败、Recall 两类分页与关联、任务版本/计划控制、团队预算/权限、Worker POST、抽屉实际发送、审批创建/详情/决定/撤销、事件游标 | 17 项通过，0 未捕获错误 |
| ui-forge-settings | 触发器五路由、持久去重 ID、失败保留草稿、本地日期→Unix 秒、模型三路由、无自动测量、失败清旧观测、390px 内容≥340px、横向键盘导航 | 23 项通过，0 未捕获错误 |
| ui-activity-scroll | 独立 activity 轮询插入与完成收起时阅读锚点不变，底部阅读继续跟随 | 偏移 60px→60px，底部距离 0px |
| ui-approval-failure | 详情失败禁旧操作、关闭不恢复陈旧权限、显式重试、快速双击只有一次提交、409 保留状态与错误 | 10 项通过，0 未捕获错误 |
| ui-smoke | 发送冲突和同 ID 重试、保留新草稿、IME、安全 Markdown、代码复制、12.5 秒轮询滚动、无浏览器存储 | 通过，0 未捕获错误 |
| ui-inline-diff | 精确回执 diff、增删行号、加载/错误/展开、阅读位置 | 26 项通过 |
| ui-history | 历史回执与当前文件独立、多版本、未知/无效回执与来源跳转 | 29 项通过 |
| ui-activity-files | 真实成功写入筛选、路径去重、更多文件、折叠和阅读位置 | 14 项通过 |
| ui-settings-save | PATCH/配置版本绑定、隐藏字段保留、凭据独立保存与清空、无存储 | 34 项通过 |
| ui-risk-status | 未知/失败/模拟与退出码保持可见，长命令完整可读，无横向溢出 | 16 项通过 |
| ui-preview-tree | 文件树/源码/实际目录读取、键盘与窗口缩放、无自动写入 | 49 项通过 |
| ui-preview | File API 导入、两页 PDF/下载、查找/虚拟化、分隔拖动、开合过渡与窄窗恢复 | 78 项通过、11 张截图 |
| ui-workspace | 任务索引、计划与团队批准的版本/摘要、设置分类、模型与预算刷新、工作空间菜单 | 81 项通过、11 张截图 |
| ui-conversation | 消息/活动折叠、真实与模拟来源、历史定位、重新分组与阅读锚点、草稿与会话 DOM 保留 | 87 项通过、7 张截图 |
| ui-interactions | 任务筛选/搜索/IME/键盘选择、审批展开、草稿、延迟/失败/刷新/滚动、工作空间分组 | 85 项通过 |
| ui-layout | 标题/侧栏/消息几何、命令卡折叠、滚动、审批成功失败及版本、设置、系统主题与后台轮询 | 184 项通过 |

既有夹具只更新了已被新规格替代的 340/52px、正文与布局断言，并补新增只读路由的响应。业务断言继续保留。没有为了旧截图恢复独立图标栏。

最后回归修复了两项实际布局问题：浮动侧栏不再重复计入文件预览断点宽度，720→640px 不会错误关闭手动打开的文件；390px 设置改顶部横向导航，内容宽约 386px。引用摘要的双层 grid 也经截图发现并修复。进入设置会关闭工作空间选择菜单，并将焦点交给设置搜索。

## 可查看的截图

- [会话浅色](D:/Competition_this/Agent/desktop/output/playwright/forge-refactor-light.png)、[深色](D:/Competition_this/Agent/desktop/output/playwright/forge-refactor-dark.png)、[390px](D:/Competition_this/Agent/desktop/output/playwright/forge-refactor-narrow.png)
- [活动抽屉与引用](D:/Competition_this/Agent/desktop/output/playwright/forge-controls-activity.png)
- [触发器浅色](D:/Competition_this/Agent/desktop/output/playwright/forge-triggers-light.png)、[窄屏深色](D:/Competition_this/Agent/desktop/output/playwright/forge-triggers-narrow-dark.png)
- [模型浅色](D:/Competition_this/Agent/desktop/output/playwright/forge-models-light.png)、[窄屏深色](D:/Competition_this/Agent/desktop/output/playwright/forge-models-narrow-dark.png)
- [审批故障恢复](D:/Competition_this/Agent/desktop/output/playwright/forge-approval-failure.png)

## 本轮验证边界

接口全量映射经过源码核对；上述浏览器场景验证了实际 UI 请求、载荷、状态变化与错误处理。它们不能替代真实 Provider 的质量、计费或长时间运行验收。本轮没有执行付费模型测量、真实 Quest 工具执行、原生 release 打包、新 Windows DPI 矩阵或原生凭据库操作。既有原生命令与后端能力继续沿用此前实现；这些范围未被本报告声明为新增验收通过。

运行与复验命令见 [README.md](D:/Competition_this/Agent/desktop/README.md)。

## 右侧 Agent 纠正复验

本节对应用户截图纠正后的版本，取代初版关于 Worker 浮层及默认展开/完成收起的描述。`npm test` 31/31 通过，`npm run build` 通过；App.tsx 为 3,742 B，各 CSS 仍低于 20,000 B。未新增依赖或修改后端、Tauri 契约。

本轮复验 8 组浏览器场景，均无未捕获异常：Agent 卡片 28 项、整体布局 19 项、活动阅读锚点、发送 smoke、任务控制 17 项、文件树 49 项、预览 78 项、会话交互 87 项。预览第一次并行运行漏采了 150ms 过渡的中间帧；串行完整复验通过，未弱化断言或修改动画。

Agent 专项验证包括：右侧宽 300px、聊天间隔 16px；卡片与内联头像一致；明暗原生资源完整加载；默认收起、精确运行定位、完成后保留手动展开；成功与未知状态并存；活动读取失败清除陈旧状态；来源和变更打开既有文件预览并隐藏 Agent 卡；菜单 Escape/外部点击；开关与缩放保留草稿；1024px 和 390px 输入/发送按钮不被遮挡，无页面横向溢出。会话组将原 Worker 浮层断言更新为真实的右卡交互，继续验证精确 Run、历史与当前来源、任务/活动访问、失败恢复、阅读锚点与代次清理。

以上仍使用合成 HTTP 数据与真实浏览器渲染，未执行付费 Provider 或真实 Worker 工具。截图是当前前端验收页：

- [默认收起深色](D:/Competition_this/Agent/desktop/output/playwright/forge-agent-panel-default-dark.png)
- [完成状态深色](D:/Competition_this/Agent/desktop/output/playwright/forge-agent-panel-dark.png)
- [浅色展开](D:/Competition_this/Agent/desktop/output/playwright/forge-agent-panel-light.png)
- [1024px](D:/Competition_this/Agent/desktop/output/playwright/forge-agent-panel-1024-dark.png)、[390px](D:/Competition_this/Agent/desktop/output/playwright/forge-agent-panel-390-dark.png)

## 对话执行过程纠正

用户补充两张原生对话参考图后，本轮将过程记录改为同一列中的工具摘要、助手进度及多头像子智能体动态，去掉默认露出的状态卡与多层详情。对照本机原生 `AQ20` 的多头像生命周期句子、`SISf/QypQ` 的过程与最终回复分离、`XSEO/getw` 的折叠头和 `IgqH` 的工具摘要。工具行与子智能体行均使用 16px 图标、普通字重及低对比度文字，鼠标悬停或键盘聚焦后出现展开箭头。

`processTimeline.ts` 仅建立按用户消息划分的显示区间，逐条保留持久化事件位置；工具不再按最新 Worker 快照移动到运行起点，历史事件不被当前状态重写。相邻同状态的生命周期事件合并头像与姓名；`worker.result` 显示“已返回结果”，不会把等待验证冒充最终完成。仅有 activity 快照时显示“当前状态”，模型、供应商、步数、事件时间和任务入口在详情内。右栏点击运行可自动打开过程并定位精确 Run；过程或内部记录被用户操作后，完成与轮询保留手动展开。

尾部普通助手回复在当前显示区间没有已知活动运行时外置，最终回复后保留真实成功写入的文件总结；任务通知及 commentary 标记的消息不提升为最终回复。当前后端没有 final 通道或真实轮次耗时，因此这属于显示启发式，不能证明任务完成或精确轮次归属。折叠头显示“执行过程”，不根据首末工具时间拼造用时。文件增删总数仅在各文件回执数据完整时显示，没有添加无效的撤销功能。

已修复在右栏打开运行后、该运行结束时父过程误收起的问题。新增浏览器场景检查顺序、完成收起与手动保留、单次精确回执读取、内部详情缓存和 DOM、历史前插与草稿、390px 输入，以及迟到进度插入时阅读锚点 20px→20px。现有工具风险回归继续检查失败、未知、模拟、非零退出码及完整长命令。

当前验收截图（合成 HTTP 数据）：[展开过程](D:/Competition_this/Agent/desktop/output/playwright/forge-process-transcript-default-dark.png)、[过程收起与最终回复](D:/Competition_this/Agent/desktop/output/playwright/forge-process-transcript-collapsed-dark.png)、[详情展开](D:/Competition_this/Agent/desktop/output/playwright/forge-process-transcript-expanded-dark.png)、[浅色](D:/Competition_this/Agent/desktop/output/playwright/forge-process-transcript-light.png)、[390px](D:/Competition_this/Agent/desktop/output/playwright/forge-process-transcript-390-dark.png)。

最终复验：`npm test` **37/37 通过**（含 6 项新过程模型测试），`npm run build` 通过，`git diff --check` 通过。本轮 **11 组浏览器回归通过，均无未捕获异常**：过程展示 28 项、Agent 面板 36 项、会话 88 项、整体布局 20 项、任务控制 17 项、精确内联 diff 27 项、风险 17 项、文件总结 15 项、历史版本 29 项，以及发送 smoke 与活动阅读锚点。此前入口因新增过程折叠而隐藏的测试已适配为真实展开操作，保留业务断言。

独立检查发现并修复两处保留问题：隐藏右卡且过程收起时，仅在 activity 快照出现的当前失败/未知也必须提示；迟到进度拆分连续子智能体动态后，各 Run 内层执行详情的手动开关继续按精确事件保存。两项均补充浏览器强断言并通过。运行中的多头像记录另见[过程截图](D:/Competition_this/Agent/desktop/output/playwright/forge-process-transcript-active-dark.png)。当前 App.tsx 3,742 B，各 CSS 最大 19,779 B；没有新增依赖，没有修改后端或 Tauri。真实付费模型及原生 release 打包仍不在本轮验收范围。
