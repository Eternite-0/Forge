# FORGE 桌面 UI 对照与下一轮 TODO

日期：2026-11-16。本清单已更新为本轮实施与验收台账，保留原 28 项及验收目标。[x] 表示本项实现已有对应验证；[ ] 表示部分完成或尚待验收，不等于完全没有实施。浏览器功能检查、原生观察与视觉还原程度分别记录。

## 对照结论

2026-11-16 源码移植续作：现已沿原始 DUYS/U5mJ/WindowShell、会话 footer 与 F4PH/jxU8/AWNZ 设置调用链核对并移植布局，删除前轮 660px/1424/1136 和分栏/展开不同树宽的自调规则。原文、SHA256 与偏移保存在 `output/reference-native-layout/{preview,shell,settings}-source.txt`。Root.H/Kg/g3 的 resize 收栏/恢复已移植，保留显式开关清记录、二次更窄断点覆盖记录及展开意图。用户最新两张截图对应的文件类型 SVG、层级线、状态框和明亮 Git 配色也已移植，证据为 `tree-source.txt`、`filetree-colors-source.txt`。整个应用仍不能声称 1:1：原应用 tab/route/owner 特例、原生 DPI/字体及专属编辑器启动入口仍有边界。下段是此前比例修订的历史记录，现值以交付记录顶部为准。

当前检查点：npm test 18/18，build 退出 0（307 modules，CSS 90.34kB、JS 527.29kB，保留 500kB 警告）。右栏最新 preview 78、tree 49、layout 184 及 smoke 通过；与前检查点未受影响的 settings-save 34、conversation 87 合计最新范围 432 项及 smoke。日志 `output/playwright/new-reference-*.json`；详细输出和未验收范围见交付记录顶部。

最新比例修订已统一普通/展开预览字号，修正 Markdown 样式被通用规则覆盖的问题：UI 14px、目录树 13px/30px、文档正文 16px/24px、文件工具栏 48px；设置主文字 14px、说明 13px、控件最小高 32px。预览按可用空间争取约 660px，窄窗自动收项目栏，完整预览的树不再占 44%/560px。实测与精确来源见交付记录顶部；未新增后端/原生能力，也未把浏览器检查计作 Windows DPI 验收。

最新续作按用户补充的会话与右侧工作区截图重新核对原始 bundle，补 Shell 右下状态、会话内联历史 diff、真实行号/增删色、完整预览模式及右侧变更/文件目录。模型设置参考 DSH 做分层与同页密钥入口；普通任务与技术记录默认折叠。最终检查另记于 [REFACTOR-REPORT.md](D:/Competition_this/Agent/desktop/REFACTOR-REPORT.md) 的续作段；下方 28 项保留前轮完整验收口径，不以功能检查冒充视觉 1:1。

本轮继续完成当前工作空间项目契约、不可变回执历史 diff 和浏览器缩放矩阵，并校准预览比例与设置说明颜色。当前 25/28 项已有实现与对应验证；01、23、27 保留部分验收状态。跨空间管理不在用户确认的当前空间范围内，不为此增加 CRUD。

主要参照：用户最新截图 `C:/Users/msi/AppData/Local/Temp/codex-clipboard-7915f3b6-76fd-4e22-ab91-820e985961ed.png`、此前的原生设置截图、`D:/crack/chatgpt-ui/` 的四份规格，以及 `desktop/output/playwright/rebuild-*.png` 的当前页面。

最新截图中的右侧是文件预览工作区。它应按需打开，与此前移除的常驻业务 inspector 区分：聊天默认完整显示，打开文件后分栏，关闭后恢复。设置打开时仍隐藏工作空间面板。

## 本轮落地状态与证据

| 范围 | 已落地内容 | 实际证据与边界 |
| --- | --- | --- |
| 项目导航与后台任务 | [WorkspaceSidebar.tsx](D:/Competition_this/Agent/desktop/src/WorkspaceSidebar.tsx)、[QuestWorkspace.tsx](D:/Competition_this/Agent/desktop/src/QuestWorkspace.tsx) 使用可展开项目组；缺少归属时回到真实当前工作空间，兼容明确项目 ID/名称/路径 metadata。需处理优先，已结束任务单独折叠。 | `ui-interactions.playwright.js` 82 项检查覆盖同名不同 ID 分离、嵌套/扁平 metadata、项目路径搜索、键盘选择及刷新保持折叠/非零滚动/当前详情。没有多项目 CRUD 或跨空间切换。 |
| 预览与文件联动 | [FilePreviewWorkspace.tsx](D:/Competition_this/Agent/desktop/src/FilePreviewWorkspace.tsx)、[CodePreview.tsx](D:/Competition_this/Agent/desktop/src/CodePreview.tsx)、[FileMarkdown.tsx](D:/Competition_this/Agent/desktop/src/FileMarkdown.tsx) 接入聊天文件入口、标签、分栏、文件树、查找、源码/渲染切换。 | [ui-preview.playwright.js](D:/Competition_this/Agent/desktop/tests/ui-preview.playwright.js) 使用真实浏览器 File API 导入，最新 36 项检查、5 张 `preview-*.png`，覆盖首 Enter 定位、刷新阅读位置、共同目录根剥离、文件树刷新、连续退出/快速反向/减少动效。Core 事件夹具仍不能替代真实 Core 文件读取证据。 |
| 原生只读文件与 Git | [workspaceFiles.ts](D:/Competition_this/Agent/desktop/src/workspaceFiles.ts) 对接 `workspace_files_list`、`workspace_file_read`、`workspace_changes`、`workspace_file_diff` 四条只读 Tauri 命令。 | [workspace_files.rs](D:/Competition_this/Agent/desktop/src-tauri/src/workspace_files.rs) 提供当前工作空间目录、文本/二进制、真实 Git 状态与 diff。浏览器导入源没有 Git 历史，明确显示该状态。用户已授权本轮所需只读原生/Core 数据通道，不涉及新增写入能力。 |
| PDF 与原生入口 | 真实 PDF 字节通过自有 Blob URL 进入内置查看器，提供下载和不可嵌入时的说明。 | 本轮通过 Computer Use 在 Windows WebView2 实际看到两页正文、页码、缩放与查找，Ctrl+, 设置入口正常；独立于浏览器 Blob/下载检查。系统 DPI、完整凭据/维护写流程未验收。 |
| 安全时间线投影 | [timeline.py](D:/Competition_this/Agent/backend/src/forge_core/timeline.py) 仅为 `file_revise` 增加安全工具名与现有过滤后的相对路径投影，没有增加 stdout、body 或文件正文。 | [test_timeline_file_preview.py](D:/Competition_this/Agent/backend/tests/test_timeline_file_preview.py) 使用真实临时 Store/ToolEngine 回执覆盖普通/中文路径、七类敏感路径、不泄漏原始 payload、failed/uncertain 保留；与 `test_desktop_api_m7.py` 合跑：最终复验 23 passed、7.06 秒；先前一次运行出现现有 Starlette 弃用警告，最终输出没有警告。 |
| 对话、设置与交互 | 长命令展开、活动计数与真实失败状态、文件行、Router/Worker身份、独立执行详情、瞬时反馈、浮层焦点；七页设置分组、运行配置拆组、模型摘要、预算16px内边距。 | 原五套浏览器回归通过：smoke单独报告，layout 167、workspace 72、interactions 82、conversation 85，共406项显式检查；活动文件组另14项覆盖成功live去重/筛选、首3/更多及轮询/重分组锚点。均无未捕获页面错误，已有local/session存储检查为0。 |
| 本轮新增证据 | 精确回执历史预览 29 项；当前项目契约与历史/安全时间线/Core 组合 46 passed；浏览器缩放 386 项/14 案例/57 图；原八套 UI 数值检查合计 485 项，另 smoke 通过。 | 分别使用合成 HTTP、真实 File API、真实 Store/ToolEngine 与隔离嵌入 Core；证据来源不混用。 |
| 构建与剩余验收 | 最终 npm test 18/18；build：305 modules、CSS 76.59 kB、JS 485.94 kB、2.70秒；Rust：17 passed、0 ignored，含嵌入 Core 握手/重启/隔离备份恢复。 | 最终构建输出、原生观察和文件说明见[REFACTOR-REPORT.md](D:/Competition_this/Agent/desktop/REFACTOR-REPORT.md)。功能通过不代表主观1:1视觉还原；01、23、27保持未全验收。 |

## 后台任务的新设计

采用“项目 → 项目中的任务 → 当前任务详情”三层，项目是一级分组，状态只做组内筛选。采用连续列表，不做大型卡片网格。

下面是结构示意，不是已有项目或任务数据；当前已实现项目组、需处理优先与历史折叠，组头计数表示已加载任务数，不冒充全局运行统计：

```text
后台任务                         搜索项目或任务
▾ 项目 A                         3 项已加载
    任务一                运行中
    任务二                等待审批
    已结束任务 …          默认折叠
▸ 项目 B                         1 项已加载

选中任务 → 右侧：概览 / 计划 / 权限；任务标签切换团队 / 活动 / 审批
```

- 项目组头展示名称、目录提示、展开箭头和范围明确的计数；计数只能代表实际已加载记录。
- 当前任务行展示真实名称、状态与一行目标摘要；目标全文、ID、版本及技术信息放入详情。接口未提供最近进展文本时不伪造进度。
- 组内优先显示需处理及运行中的任务，已结束任务折叠；搜索时展开包含结果的组。
- 选中、筛选、组展开与阅读位置在轮询后保持；审批仍绑定当前任务与计划版本。
- 当前 Core 绑定一个工作空间，现有只读 `project_context` 明确空间 ID/名称/根目录及默认项目。任务具有明确项目字段或 metadata 时按真实归属分组，否则进入当前工作空间组；Core 实际 JSON 字符串 metadata 与对象共用解析。metadata 不授予文件权限。多项目 CRUD/跨空间列表不在授权范围，04 按单空间契约完成。
- 左侧导航中的项目分组与任务页的项目分组应共享同一归属，避免出现两套“项目”。此前确认的唯一长期对话入口继续保留，不因截图中有多个聊天而伪造会话。

## P0：先完成用户指出的结构与功能缺口

| 待办 | 实施状态与证据 | 完成条件（保留验收目标） |
| --- | --- | --- |
| [ ] 01. 定版同尺寸视觉基线 | 浏览器基线已完成：同内容/同滚动位置、明暗主题、DPR 1/1.25/1.5，共386项/14案例/57图；参考截图无DPI元数据，按侧栏推测约125%，不冒充已知。原生当前系统125%已观察，受控150%逐屏比较尚未验收。 | 在默认窗口与 125%/150% DPI 下，对同内容、同滚动状态逐屏比较。浏览器DPR与原生系统DPI分别记录，不能相互替代。 |
| [x] 02. 重做项目导航层级 | [WorkspaceSidebar.tsx](D:/Competition_this/Agent/desktop/src/WorkspaceSidebar.tsx) 已将真实当前项目作为可展开组，唯一对话入口置于组内，提供缩进、选中与 height/opacity/chevron 动效；无数据的最近/置顶项未伪造。 | 当前真实项目作为可展开组，对话归在组内；图标、缩进、选中条、长名称与折叠动效按截图统一。最近/置顶区域只在有真实对应记录时呈现。 |
| [x] 03. 后台任务改为按项目分组 | [QuestWorkspace.tsx](D:/Competition_this/Agent/desktop/src/QuestWorkspace.tsx) 已按项目归属分组，组内需处理优先、运行任务随后，历史折叠；82 项交互检查覆盖筛选、键盘、同名项目隔离和刷新保持状态/位置。 | 实现上面的项目树、组内任务列表及任务详情；不再用状态代替项目。搜索、键盘选择、加载更多、版本绑定和轮询位置仍有效。 |
| [x] 04. 补齐项目归属契约 | `project_context.py` 在设置与任务读取提供version1/current_workspace契约，含空间ID/名称/根目录/默认项目键及明确false的多空间/CRUD能力；侧栏与任务共用 `projectContext.ts`。修正Core JSON字符串metadata漏解析，独立单测及Core组合验证通过。 | 明确项目 ID、名称、根目录与任务归属；归属缺失按真实当前空间缺省。按用户选择“仅显示当前工作空间”完成；metadata分组不是可管理项目目录，也不扩大文件读取范围。 |
| [x] 05. 新建按需打开的右侧预览工作区 | [FilePreviewWorkspace.tsx](D:/Competition_this/Agent/desktop/src/FilePreviewWorkspace.tsx) 已接入按需开合、标签切换/关闭、鼠标与键盘调整宽度、独立滚动和窄窗单面板；开合/调整捕获聊天阅读锚点，草稿保持。真实 File API 回归覆盖。 | 支持打开/关闭、拖动宽度、标签切换与关闭、独立滚动；聊天最大 736px，在剩余空间内居中并可缩窄。窄窗口切到单面板，不能硬塞三栏。草稿和阅读位置保留。 |
| [x] 06. 定义真实文件读取与产物数据通道 | [workspaceFiles.ts](D:/Competition_this/Agent/desktop/src/workspaceFiles.ts) 已接原生四条只读命令；浏览器用用户明确选择的真实 File 对象。路径限当前工作空间，区分 UTF-8、缺失、格式、大小错误；文本上限 2 MB、二进制预览上限 24 MB。 | 获得工作空间范围内的目录、文件类型、正文/二进制和稳定文件引用；明确大小、编码、不存在、不可读等状态。不能靠测试夹具冒充真实文件接通。本轮已按获授权的只读原生命令确定边界。 |
| [x] 07. 打通聊天文件 → 预览 → 文件树联动 | [App.tsx](D:/Competition_this/Agent/desktop/src/App.tsx)、[ChatTimeline.tsx](D:/Competition_this/Agent/desktop/src/ChatTimeline.tsx) 已传递真实相对路径打开回调，重复打开复用标签，文件树跟随选中，连接/空间/代次身份隔离旧文档；文件缺失显示真实错误。 | 点击真实文件引用打开右侧同文件；重复点击复用标签；文件树同步选中；不同项目的同名文件不会混淆；文件不存在有明确提示。 |
| [x] 08. 代码预览 | [CodePreview.tsx](D:/Competition_this/Agent/desktop/src/CodePreview.tsx) 已提供只读源码、行号、轻量着色、虚拟滚动、查找定位、自动换行及完整复制。超过 5,000 行禁用换行并说明原因；源码不执行、无新依赖。 | 支持完整源码、行号、横向滚动/自动换行、复制、定位与只读状态；长文件不拖慢聊天。源码不执行。语言着色依据现有能力评估，不擅自新增依赖。 |
| [x] 09. Markdown 预览与源码切换 | [FileMarkdown.tsx](D:/Competition_this/Agent/desktop/src/FileMarkdown.tsx) 已实现安全渲染、相对链接打开与源码切换；两个视图保留阅读位置。真实导入回归验证 skipHtml、危险 URL、外链图片不请求及相对文件联动。 | Markdown 文件可在渲染视图/源码间切换，保留各自阅读位置；相对文件链接能打开同工作空间文件。继续保留 skipHtml、危险 URL 和外链图片限制。 |
| [x] 10. PDF 真正可读 | 真实 PDF 字节经自有 Blob URL 打开；Windows WebView2 已由 Computer Use 观察两页正文、页码、缩放、查找。浏览器另验下载与 Blob，未把空 object 当成功；更多原生场景见 27。 | 先验证 Windows WebView2 内置 PDF 查看器、现有 CSP 与可用文件传输方式，再实现真实 PDF 打开、多页阅读、缩放/适合宽度及错误状态。不能把空 iframe 或任意 file:// 地址当成功。若页码/搜索等依赖查看器能力，应实机确认后定案。 |
| [x] 11. 文件树与变更视图 | 已实现真实目录展开/筛选、文件选中与所有文件/更改视图，原生命令读取真实 Git 状态和文本 diff；二进制/过长差异有说明。导入文件没有 Git 历史时明确显示，不生成增删数或差异。 | 可筛选和展开真实目录；变更文件可打开源码或真实差异；只有真实 Git/产物数据存在时显示状态与增删数。目录树、标签与聊天引用使用同一文件标识。 |
| [x] 12. 真实数据与浏览器夹具对齐 | 按用户本轮授权补齐只读原生/Core 边界。时间线只新增 file_revise 安全名称/过滤路径，保持摘要白名单；正文由独立工作空间只读通道提供，仍不投影 stdout/body。新投影与 desktop_api 共 23 测试通过。 | 按真实返回数据验收工具、文件与产物显示；明确哪些正文可提供、哪些继续不提供。补数据契约验证，不能继续以内容更丰富的合成夹具证明真实预览完成。本轮已定只读文件契约，继续不绕过 Worker 审批工具读文件。 |

## P1：统一对话、执行信息与设置的使用方式

| 待办 | 实施状态与证据 | 完成条件（保留验收目标） |
| --- | --- | --- |
| [x] 13. 聊天文件变更清单 | 文件行、共用引导线与首3/更多已实现；本轮补精确回执“记录的更改”入口、历史diff及真实增删数回填。同路径多版本与当前文件各自独立标签，来源标“关联任务的请求”，模拟来源明确。历史基线取不可变正文/前驱回执，绝不回退当前文件/Git；29项历史UI及21项Core历史测试通过。 | 铅笔图标、文件名链接、目录/文件名层次、真实增删数、共用引导线；多文件可汇总并展开更多。增删数按需读取对应回执后显示；不编造精确聊天轮次归属或将任务全部历史修改算作本轮修改。 |
| [x] 14. 变更汇总入口 | 输入框上方显示已加载对话中成功文件回执的去重路径数；Worker 概览显示实际工作空间 Git 变更数/查看入口。二者点击进入各自范围，分别标明口径，不混用统计。 | 底部显示当前对话/轮次范围，顶部显示工作空间范围，分别标明统计口径；点击进入对应变更视图，不能混用两套数字。 |
| [x] 15. Router/Worker 信息层级 | Router状态可读，Worker真实角色/名称/模型来源、运行概览与对应任务入口保留；新增按run ID精确匹配的内联/浮层只读详情，历史事件与最近状态区分，真实步数/工具次数缺失保持未知，不以新run替代旧run。85项对话回归含状态变化/失败/缺失/键盘/短窗/展开保持；完整原生矩阵仍见27。 | 真实的回复中、不可用、运行、待审批与完成状态可读；内联Worker使用真实角色/名称并可进入详情。最新图概览的变更/子智能体分区按实际能力实现，不把“已加载数量”当全局总数。 |
| [x] 16. 长命令完整阅读 | 长命令默认两行，现用可聚焦按钮按鼠标/键盘展开全文，aria-expanded 对应状态；保留原命令可访问名称与“复制”，复制始终取完整字符串。 | 鼠标与键盘均能查看完整长命令；默认仍紧凑，复制仍完整，原“复制”名称保持。 |
| [x] 17. 活动聚合与详情排版 | 活动汇总按真实工具调用计数，单/多文件摘要与直接拼接保留；详情统一 14px/21px。失败、uncertain 和模拟标记不被成功摘要掩盖，三级展开及轮询阅读锚点已有回归。 | 同类调用次数与单/多个文件按真实记录表达；多段摘要仍不加分隔符。逐层缩进、行距、无详情活动和失败/未知状态逐项对照。 |
| [x] 18. 设置七页使用统一内容层级 | [SettingsView.tsx](D:/Competition_this/Agent/desktop/src/SettingsView.tsx) 共用 SettingsSection/SettingsRow；预算、运行配置、模型详情和其余设置统一页标题、框外组标题、说明与操作层级；完整字段继续可达。 | 共用页标题、组标题、设置行、说明与操作布局；保留完整字段和所有功能。不能只把常规页调好就称设置完成。 |
| [x] 19. 拆分运行配置与统一控件 | [OperatorSettings.tsx](D:/Competition_this/Agent/desktop/src/OperatorSettings.tsx) 拆为供应商、Router、预算并发、网络四组；CSS 开关 32×20px、thumb 16px，select 统一箭头。原 input 类型、标签、版本检查与保存确认原样。 | 按真实功能分组；通过 CSS 统一控件大小、开关外观、下拉箭头、禁用/聚焦/错误态。保留 input 类型、可访问名称、版本检查和原保存流程。 |
| [x] 20. 模型库默认视图减负 | [ModelProfileSummary.tsx](D:/Competition_this/Agent/desktop/src/ModelProfileSummary.tsx) 首层显示名称、供应商、状态及主要支持能力，完整档案/测量折叠；72 项工作空间回归显式展开并验证支持 true/false、未知、来源与采样时间。 | 首层显示名称、供应商、状态和主要能力；完整字段进入可展开详情，编辑/启停操作更好定位；未知值仍未知。 |
| [x] 21. 修复预算页明确的对齐问题 | [BudgetOverview.tsx](D:/Competition_this/Agent/desktop/src/BudgetOverview.tsx) 三组框外标题统一，未结算记录用明确高特异性选择器保持 16px 内边距；原预算语义、未知/零值与请求状态通过回归。 | 记录文字与同组标签保留一致的 16px 内边距；标题位置、组间距与其他设置页统一。预算含义及原始状态不因美化而改变。 |
| [x] 22. 设置导航 hover/active 分开 | 设置导航 hover 使用 control-subtle（约 .03），active 使用 nav-active（约 .055）；当前分类与悬停分开，亮暗主题共享对应令牌，未改变导航可访问名称。 | 按参考区分 hover 约 .03 与 active 约 .055；鼠标移动时当前分类始终明确，亮暗色分别检查。 |
| [ ] 23. 字体、颜色、边界与空态构图校准 | 已量测参考：标题28px/33.6px、设置行13px/18px及12px16px padding、活动21px/16px图标等一致。说明颜色校准为secondary .65；大窗预览56%→41%，<=1280px保留56%，树clamp(170px,28%,244px)。[对照报告](D:/Competition_this/Agent/desktop/output/scale-matrix/visual-comparison.md)记录具体差值及理由；尚未确认逐像素1:1。 | 以01同尺度基线校准。参考原图DPI未知、原生150%及主观确认仍未完成，不将浏览器矩阵通过等同完全还原。 |

## P2：补齐交互连续性与最终验收

| 待办 | 实施状态与证据 | 完成条件（保留验收目标） |
| --- | --- | --- |
| [x] 24. 浮层键盘与材质 | [DisclosurePopover.tsx](D:/Competition_this/Agent/desktop/src/DisclosurePopover.tsx) 已补打开聚焦、方向键、Tab/Shift+Tab 离开关闭、Escape 焦点返回及外点关闭；常驻 DOM/inert，背景为实色表面，保留非模态语义。 | 定义并测试打开、Tab/Shift+Tab、选择、Escape/外点关闭与焦点返回；保持非模态语义。材质以文字清晰为准，对照亮暗色。 |
| [x] 25. 输入框与视图切换动效 | textarea增高/缩回、工作空间菜单常驻DOM的height/opacity 150ms收起、项目折叠与侧栏过渡已接入；预览现用同曲线150ms连续宽度/透明度，退出保留DOM并立即inert，支持反向切换与减少动效，每帧恢复阅读锚点。36项真实文件预览与85项对话回归覆盖开合、草稿、嵌套滚动及逐帧锚点；IME原检查通过。 | 输入增高/缩回、侧栏开合、菜单收起和预览开合具有连续过渡；采用既定曲线，减少动效时立即完成；不能破坏阅读锚点和IME。 |
| [x] 26. 复制与操作反馈统一 | 命令/回复复制共用 2 秒瞬时反馈，命令反馈绝对定位、不撑高卡片；预览反馈约 2.2 秒并提供 role=status，失败可感知，加载/禁用/错误状态继续真实显示。 | 反馈出现和消退一致；键盘可感知，不长期挤占内容；错误、加载、禁用和断连状态统一。 |
| [ ] 27. 原生页面与长内容实测 | 新建隔离runtime/真实ToolEngine历史工作空间，实际WebView2在Windows125%（AppliedDPI120）连接Core、读取目录/PDF并显示正文；修复debug地址与Vite/导航白名单不一致。Rust17项含真实嵌入Core握手、重启、隔离目录备份恢复/写入锁。后续窗口操作被用户Esc停止，未继续自动操作；原生七页/最小窗/150%/下载落盘/全部长内容组合仍未全验收。 | 在原生中只打开相应页面验证；覆盖默认 1440×900、最小窗口、DPI、长文件名、加载失败、刷新与身份变化清理。无需为视觉验收操作真实凭据、备份或恢复；已测维护流程只涉及独立测试目录。 |
| [x] 28. 将视觉与功能验收分开 | 最终八套UI：485项数值检查另smoke，页面异常0；缩放矩阵386项/14案例/57图；Core46测试、Rust17测试、npm18测试/build通过。各自区分HTTP夹具、真实File API、真实Store/ToolEngine、嵌入Core与原生观察；主观还原及原生缺口单独记录。 | 关键状态有同尺寸截图与动态交互检查；另跑 npm test/build/smoke。预览接通使用真实文件及API样本；不凭断言数量声称逐像素一致。 |

## 能力边界与不应误列的事项

- 用户已授权预览所必需的只读Core/原生命令；本轮补充04当前空间项目契约与13历史只读接口。该授权不代表多项目CRUD、空间切换或任意写入能力；这些不在本轮范围。没有更换React/Tauri、添加依赖或生图。
- Tauri 现新增四条 UI 专用只读文件/目录/Git 命令，限制在当前工作空间、执行路径与敏感资源过滤。UI 不调用 Worker 的审批工具来绕过执行授权，也不接受任意 file:// 地址。
- 已验证现有 WebView2 内置 PDF 查看器可阅读两页正文并使用页码/缩放/查找，不需要迁移 Electron。代码使用轻量着色，未增加 PDF 引擎或语法高亮依赖。
- 思考正文、用时、压缩中状态与停止执行按钮先核实真实事件/取消协议。有数据才显示；不为追求截图外观编造过程或添加无效停止按钮。
- 已完成并应继续保护：52＋288px 基础骨架、设置隐藏工作空间面板、对话最大 736px、21px 活动摘要、12.5px/0.8px 命令卡、真实溢出渐隐、Markdown 安全、IME、请求 ID 重试和轮询展开/滚动保持。
- 三组七项设置对应现有功能，不因参考条目更多就补假开关。侧边栏项目分组已重做；“只显示当前工作空间、唯一长期对话”的真实能力边界继续保留。

## 后续验收顺序

1. 补齐 01、23 的默认窗口与 125%/150% DPI 同内容逐屏对照，记录具体差值和用户主观反馈。
2. 补齐 27 的原生长文件、失败/刷新、身份切换与设置完整页面矩阵；视觉验收无需执行删除凭据、备份或恢复。
3. 当前空间项目契约与精确回执历史diff已完成。只有新增明确需求后才扩展多空间/项目CRUD；不把metadata分组视为项目管理。
4. 历史来源仅表示关联任务请求，精确聊天轮次因果关系尚无契约；继续保持真实口径及阅读锚点。

剩余重点是01/23/27的原生/DPI逐屏验收。浏览器矩阵与功能检查已完成，不能据此宣称视觉逐像素一致。
