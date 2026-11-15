# FORGE 桌面重构交付记录

## 2026-11-16：从 dump 移植布局调用链，并按最新截图修正右栏

本节是当前实现与验证口径。下方“统一尺度”等段落保留历史记录，其中 660px、1424px、1136px、展开树 28% 等值已被替换。当前没有完全 1:1 的验收结论。

之前的问题是只取局部样式，外层尺寸算法、rem 基准、布局状态与组件覆盖规则没有一起移植。本轮沿原始分包源码的组件调用链修改前端，并用用户最新两张截图交叉核对右侧文件树。原始 bundle 只读，没有执行；它依赖原应用的模块运行时和 MessagePort/RPC，因此移植其布局、SVG 和状态逻辑，业务数据继续使用 FORGE 现有接口。未生成图片、安装依赖或修改 API/后端/原生命令。

### 本轮文件与具体变化

| 文件（相对 desktop/） | 当前变化 |
| --- | --- |
| `src/usePreviewLayout.ts` | 直接移植 DUYS 的默认宽度、regular 上下限、归一化比例与还原函数；分隔器覆盖布局，占宽改为 0。继续以现有阅读锚点保持聊天滚动。 |
| `src/App.tsx` | 测量实际主区宽度和扣除 36px 应用栏后的高度；首次真实测量冻结默认比例，后续缩放沿用。移植 Root.H/Kg/g3 的 960/720 断点收起与恢复、显式开关清记录、连续更窄断点覆盖记录。拖到 160px 以下关闭，越界到聊天不足 160px 时展开。输入栏高度写入滚动区预留。 |
| `src/FilePreviewWorkspace.tsx` | 分栏和全宽共用初始 250px 文件树，最小 200px、最大父区 60%，支持拖到 100px 以下关闭。移植 DMvp 中 20 类文件 SVG、目录 chevron、层级线、20px 增删改状态框；原增删计数保留为无障碍文本。文件树开关移到工具栏右侧，标签栏“＋”接入已有文件选择能力。 |
| `src/CodePreview.tsx` | 源码查找按需展开，工具栏按钮或源码区 Ctrl+F 打开；Enter/Shift+Enter 定位、Escape 收起并回到源码。保留虚拟化、21.6px 行高、横纵滚动和查找定位。 |
| `src/SettingsView.tsx` | 按 BiIz/b5fQ 结构拆出固定标题与搜索、独立滚动的导航列表；保留 FORGE 的七个真实功能入口和全部原有可访问名称。 |
| `src/style.css` | rem 基准 16px、UI 14px；页面上/右/下内缩 8/4/4px。会话输入栏浮动到滚动区底部，正文预留实测高度。设置列最大 768px，内部 20px；标题行 32px、搜索 36px、导航行和组标题 30px。按原生行组件恢复标签 13px/18.5714px、说明 12px/16px、紧凑控件 28px、组边框 1px。修正文件树源覆盖规则及 Git/选中色。 |
| `tests/ui-layout.playwright.js`、`ui-settings-save.playwright.js` | 按原生最终结构验证设置尺寸；保存测试等待实际 revision 刷新后再检查，保留版本和安全 payload 检查。 |
| `tests/ui-preview.playwright.js`、`ui-preview-tree.playwright.js` | 验证源宽度算法、分隔条、断点收起/恢复、全宽/分栏阅读保持，以及本轮文件图标、状态色、树开关、文件选择与按需查找。 |
| `README.md`、`UI-TODO.md`、本文 | 更新当前值、原始证据、适配与未验收范围；历史结果不作当前代码的验收替代。 |

### 直接来源与当前数值

源码摘录、哈希、模块名和偏移保存在 `output/reference-native-layout/`：`preview-source.txt`、`shell-source.txt`、`settings-source.txt`、`tree-source.txt`、`filetree-colors-source.txt`。各文件注明偏移采用的 Unicode/UTF16/字节口径。原始资产位于 `D:/crack/chatgpt-ui/mirror/webview/assets/async/`；参考 server 的复刻页与标为 replica 的结构 JSON 仅作交叉参考。

- **预览**：DUYS 默认宽为 `max(320,min(1.6H,W−500),min(640,W−352))`，regular 区间为 320 到 `max(320,W−352)`。640 并非最大上限；W=1600、H=900 时初值为 1100。此处 W 是扣除左栏后的实际主区宽，H 是工作区行高。全宽使用整主区，恢复分栏沿用同一比例。
- **缩放状态**：Root.H 使用主 surface 加可见左栏 slot 的宽度，与 DUYS 的 W 区分。FORGE 通过实际主区及动画导航宽度映射。960 以下且项目栏打开，或跨到 720 以下时保存当前开关并收起；放宽后恢复一次。原文会在第二个更窄断点覆盖快照，因此 1100→900→700→1100 可能保持关闭，未擅自改成始终恢复最初状态。
- **文件树**：沿 U5mJ/gbrq 共享 250px 宽度。BaseFileTree 的实际 unsafeCSS 覆盖为横向 padding 3px、gap 5px、层级增量 8.75px；默认行 30px、文件字 13px、图标 16px、状态槽 20px。层级线为 1px，悬停透明度 .75。该覆盖修正了前轮只读到库默认 8px/6px 的结论。
- **颜色**：用户最新截图的纯色像素与原生 sectioned tree 分支相符：新增 `#00a240`、修改/重命名 `#0285ff`、删除 `#e02e2a`。普通文件选中为正文色；选中背景 light `rgba(13,13,13,.049)`、dark `rgba(252,252,252,.052)`。此前新增 `#008b35`、修改借用 `#014f99` 偏暗，已替换。JSON、图片、Python、Markdown 等图标使用原 sprite 的独立配色。
- **设置**：沿 F4PH→jxU8/AWNZ/ZEx5 和 BiIz/b5fQ/okCa 调用链；内容 768px 包含内边距 20px，导航外字号 13px、内标签实际为 14px/21px。导航标题 18px/24px/600；标题与搜索组合 gap 0，搜索下 8px、列表上 4px、组距 16px、组内 4px、列表项距 1px。组圆角不支持 superellipse 时 16px，支持时 20px；导航项相应为 10/12.5px。28px 是明确选用的 compact/md 变体，不代表原生所有控件都为 28px。

### FORGE 适配、观感处理与边界

1. 导航保留用户确认的 52px 图标栏＋288px 项目面板、单一长期对话；设置保留七项实际能力。没有添加原应用专属账户、云服务等空入口。
2. 文件树、预览比例仅用 React 内存状态，遵守不使用 localStorage/sessionStorage 的要求；原生持久化适配没有复制。文件请求主动显示树、720px 以下主动打开为全宽、移动水平导航属于 FORGE 适配。原应用 route/owner lease/tab retain 特例没有对应产品概念。
3. 用户最新截图中标签 32→约 40px、工具栏 48→约 60px、树行 30→约 37.5px，与约 125% 缩放相符；这是图像尺寸交叉判断，没有将物理像素直接当作 CSS 默认值，也没有新增 CSS zoom。Windows DPI 与字体渲染仍需独立原生对照验收。
4. 工具栏“＋”、查找折叠和右侧树开关按截图安排，继续使用现有只读文件源。窄预览在 560px 以下只收起按钮文字，保留原可访问名称；这是避免工具栏挤成两行的响应式适配。截图里的 VS Code“打开”及编辑器选项菜单尚未接入，不显示无实际能力的同名按钮。
5. SVG 移植为常见 20 类，其他扩展名使用原生通用页图标；FORGE 继续使用递归目录结构，未复制原完整虚拟化树或编辑器引擎。层级线对 focus-within 也显示，便于键盘操作。原文本 chevron 保留以维持按钮名称。
6. 动效遵循用户先前明确要求的 150ms `cubic-bezier(0,0,.2,1)`；没有将其宣称为原生 .5/.35 spring 的完整复现。活动摘要 21px、命令卡 12.5px/.8px 保持。

### 实际检查

最终 `npm test` 退出 **0**，原始输出 `output/reference-sidebar-unit.txt`：

```text
> forge-desktop@0.2.0 test
> node --test tests/*.test.mjs
ℹ tests 18
ℹ suites 0
ℹ pass 18
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 753.5047
```

最终 `npm run build` 退出 **0**，原始输出 `output/reference-sidebar-build.txt`：

```text
> forge-desktop@0.2.0 build
> tsc && vite build
vite v6.4.6 building for production...
✓ 307 modules transformed.
dist/index.html                   0.35 kB │ gzip:   0.26 kB
dist/assets/index-CTM8ijHb.css   90.34 kB │ gzip:  15.95 kB
dist/assets/index-DxPvRBKk.js   527.29 kB │ gzip: 164.10 kB
(!) Some chunks are larger than 500 kB after minification.
✓ built in 2.17s
```

保留超过 500kB 的构建警告。验证期间发现本机 PATH 的 `net.py` 抢先匹配 Vite 的只读 `net use` 探测，导致构建打印完成后仍等待；最终仅在构建进程内将 System32 放在 PATH 前面，进程正常退出 0，没有修改系统配置或项目依赖。

源码布局移植阶段的六套浏览器回归全部通过：layout 184、settings-save 34、conversation 87、preview-tree 39、preview 72，合计 **416 项检查及 smoke**。日志为 `output/playwright/source-port-*.json`，验证来源为合成 HTTP/桥接和真实 File API，未作为真实 Core/供应商或原生 DPI 验收。

最新右栏调整后重跑受影响的 **preview 78 项、tree 49 项、layout 184 项和 smoke**，全部退出 0、未捕获页面错误 0；日志为 `output/playwright/new-reference-{preview,tree,layout,smoke}.json`。新增检查覆盖标签栏“＋”真实唤起文件选择、查找默认收起及 Ctrl+F/Enter/Escape、图标与层级线、精确 Git/选中色、960px 视口实测完整工作区 956px 的收起/恢复。Tree 记录 26 次模拟桥接读取、0 写入；Preview 使用真实 File API 与两页 PDF 字节并验证下载，浏览器截图实际看到 PDF 正文。全宽分隔器不再显示，保留源代码 8000px 阅读位置、聊天 DOM、草稿、快速反向和减少动效检查。设置保存 34 项及会话 87 项在前一检查点通过，右栏专项后未重复运行；将各范围最新结果合并为 **432 项检查及 smoke**，不称为同一批全套重跑。检查过代码、PDF、窄窗、1280px 分栏和全宽树截图，未发现遮挡或整页溢出。

`git diff --check` 通过；`api.ts`、`logic.ts`、`timeline.ts`、package/lock 和已有 `*.test.mjs` 均未修改。Markdown 保护段与 HEAD 一致，SHA256 为 `1137d5d0000cc0931319c8b09e1e5267e35e209b3fecf5bb0887f8751b75eab2`；`src/` 没有 localStorage/sessionStorage。IME、重试 request ID、版本/摘要审批、轮询滚动与过期响应隔离继续由原测试及浏览器回归验证。其他现有后端/原生脏改动未纳入本轮布局修改。

## 2026-11-16：按原生布局统一尺度

针对“整体太小、布局不协调”的反馈，本轮直接修改前端代码。没有生成图片、增加依赖或修改 API/后端/原生命令；下方历史记录中的旧比例不再代表当前默认值。

### 本轮修改文件

| 文件 | 具体改动 |
| --- | --- |
| `src/App.tsx` | 预览按实际可用宽度计算，在 41–64% 范围内争取约 660px；手动调整的 34–76% 比例优先。小于 1424px 时打开预览自动收项目面板，关闭恢复原状态；不超过 1136px 时使用单面板并将隐藏聊天设为 inert。保留原聊天 DOM、草稿与阅读锚点。 |
| `src/style.css` | 普通/展开预览统一 14px 控件文字；目录树 13px/30px、16px 图标、6px 圆角，工具栏实际 48px。Markdown 正文 16px/24px、内边距 12px 16px；提高专用选择器优先级，修复通用 `.markdown` 覆盖字号和标题的问题。设置导航/主标签 14px/21px、说明 13px/20px、输入/按钮至少 32px；侧栏项目/对话行 14px/32px。 |
| `src/CodePreview.tsx` | 普通源码行高从 22px 统一为 21.6px，同时修改虚拟行高计算，保持滚动与查找定位正确。代码默认字号继续为 12px。 |
| `tests/ui-layout.playwright.js`、`ui-settings-save.playwright.js` | 校验设置最终实际字号、行高和控件大小；保留版本确认与隐藏字段保存检查，补 1280px 对照截图。 |
| `tests/ui-preview.playwright.js`、`ui-preview-tree.playwright.js` | 检查普通/展开模式同字号、实际工具栏高度、文档字号、五档窗宽的阅读空间和文件名完整性，以及自动收栏/单面板、DOM/草稿/滚动保持。 |
| `README.md`、`UI-TODO.md`、本文 | 同步当前尺寸、适配规则、实际输出及未验收范围。此表路径均相对 desktop/。 |

### 原生依据与观感调整

直接只读核对已有 `D:/crack/chatgpt-ui/mirror/webview/assets/async`，没有执行 dump。`907538.0572f09d40.js` offset 2674042 定义默认 UI 14px、代码 12px；`172613.062b16766b.js` offset 7510/17686/180776 与应用调用链确认树默认 13px/30px、16px 图标、8px 水平 padding、6px gap/圆角。`907538...js` offset 7226201、`956370.aff5cce818.js` offset 1155400、`2e662a5288e2.css` offset 138335 确认文件工具栏为 32px 控件加上下各 8px，即 48px。`110114.4b0349efa9.js` offset 29619 确认 Markdown 使用聊天正文字号及 12px/16px 内边距；本轮与已有助手正文统一为 16px/24px。源码 12px/21.6px 来自 `66582da7ee6b.css` offset 1397700。

以下为结合 FORGE 功能与本轮目视对比的调整，并非声称原生固定参数：

- 普通文件区目标 660px、树 `clamp(220px,30%,280px)`；完整工作区树改为 `clamp(260px,28%,360px)`。原 41% 默认在 1440px 窗口仅给文件区约 449px，树占去 170px 后正文拥挤；前一个 600px/180px 候选仍截断短文件名。最终 220px 树可显示 README.md/deep.ts，完整模式也不再默认分配 560px 空白树列。
- 1424px 收项目栏与 1136px 单面板断点用于保留聊天和文件的阅读空间；用户手动分隔条选择继续优先。未使用整页 CSS zoom。
- 设置主文字从 13px/18px 调为 14px/21px、说明从 12px/16px 调为 13px/20px、导航/控件从 30/28px 调为 32px，减小与 28px 页面标题、16px 正文的尺度落差。Markdown 标题采用 28px/36px、24px/32px、20px/28px。
- 精确活动摘要 21px、16px 图标、命令卡 12.5px 圆角/0.8px 边框、diff 12px/21.6px 和 150ms 曲线继续保留。

### 本轮实际验证

`npm test` 退出 0，原始输出保存在 `output/native-scale-unit.txt`：

```text
> forge-desktop@0.2.0 test
> node --test tests/*.test.mjs
ℹ tests 18
ℹ suites 0
ℹ pass 18
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 415.6938
```

`npm run build` 退出 0，原始输出保存在 `output/native-scale-build.txt`：

```text
> forge-desktop@0.2.0 build
> tsc && vite build
vite v6.4.6 building for production...
✓ 307 modules transformed.
dist/index.html                   0.35 kB │ gzip:   0.27 kB
dist/assets/index-DS4xZZmj.css   86.22 kB │ gzip:  15.19 kB
dist/assets/index-BT3qf1YB.js   503.01 kB │ gzip: 155.50 kB
(!) Some chunks are larger than 500 kB after minification.
✓ built in 1.57s
```

保留并如实记录 JS 超过 500kB 的 Vite 警告，未通过提高阈值隐藏它。

使用已安装的 Playwright 和 Edge，不新增依赖；6 套浏览器回归为 **393 项数值/功能检查及 smoke**，全部未捕获页面错误为 0：layout 171、settings-save 34、conversation 87、preview-tree 39、preview 62，另 smoke 通过（2 次发送、复用 request ID、单对话与 local/session 存储均为 0）。相关结果为 `output/playwright/native-scale-*.json`。最后宽度调整后重跑受影响的 preview、preview-tree、conversation 和 smoke；layout/settings-save 的最终字号未再次变化。

| 窗口宽 | 普通预览宽 | 文档列宽 | 树宽 | 导航宽 |
| ---: | ---: | ---: | ---: | ---: |
| 1152 | 658.78 | 438.78 | 220 | 52 |
| 1280 | 658.91 | 438.91 | 220 | 52 |
| 1400 | 659.02 | 439.02 | 220 | 52 |
| 1440 | 658.78 | 438.78 | 220 | 340 |
| 1920 | 659.16 | 439.16 | 220 | 340 |

以上均无页面横向溢出，两组目录中的 README.md/deep.ts 不截短。完整预览 1440px 下文档/树为 1022/360px，1280px 下为 879.84/342.16px；1136px 单区及聊天 inert、640px 无溢出通过。真实浏览器 File API、PDF 字节与下载、源码滚动/查找、Markdown 切换、展开/还原/关闭、草稿与 DOM 保留、150ms 动画/减少动效均通过；Core/Tauri 交互部分使用明确标记的合成夹具。目视对照截图：`preview-markdown-split-1440.png`、`preview-markdown-expanded-1440.png`、`scale-preview-tree-1440.png`、`rebuild-forge-settings-general.png`（均在 output/playwright）。

`git diff --check`、浏览器夹具 `node --check` 通过。`api.ts`、`logic.ts`、`timeline.ts`、package/lock 与已跟踪 `*.test.mjs` 无改动。ChatTimeline 受保护 Markdown 片段与 HEAD 字节相同，SHA256 为 `1137d5d0000cc0931319c8b09e1e5267e35e209b3fecf5bb0887f8751b75eab2`；src 中无 localStorage/sessionStorage 引用。

本轮仍未重新验收 Windows 150% 原生 DPI、原生下载落盘及完整原生页面矩阵；浏览器结果不能替代这些项目。原有 28 项中的 01/23/27 因此保持未全验收。没有把真实模型调用、凭据或维护操作作为本轮测试的一部分。

## 2026-11-15 续作：交互减负与原始源码对照

本段记录最新续作；下方原生、Core 与缩放结果属于前轮，未作为本轮重新执行的结果。没有生图、新依赖、API 或后端修改。

先通过 You.com 阅读官方资料，再核对 DSH `deepseek-ai/deepseek-harness` 固定提交 `39163dd30f703a79079e2d2446545cff3c453738`。其供应商记录、单个编辑器、API Key 首层和技术选项折叠层级用于减负；FORGE 没有其模型发现接口或配置/密钥原子保存能力，继续使用现有版本确认、独立密钥命令与重启流程，不新增虚假的“连接测试”。参考 [ProviderEditor.tsx](https://github.com/deepseek-ai/deepseek-harness/blob/39163dd30f703a79079e2d2446545cff3c453738/packages/client/ui-settings-models/src/client/ProviderEditor.tsx#L480)、[ModelsSection.tsx](https://github.com/deepseek-ai/deepseek-harness/blob/39163dd30f703a79079e2d2446545cff3c453738/packages/client/ui-settings-models/src/client/ModelsSection.tsx#L247)。[Meta Muse](https://ai.meta.com/muse/) 与 [Grok Bot](https://help.x.com/en/using-x/about-grok) 仅作为“交代目标、后台执行、回报结果”的官方交互说明，未声称观察其登录后界面。

用户补充命令/修改和右侧文件工作区截图后，直接读取已有 `D:/crack/chatgpt-ui/mirror/webview/assets/async` 原始 bundle；示例 `app/public/app.js` 遗漏内联 diff 和 Shell footer，因此这部分以原始实现为准，无需重新提取或运行 dump：

| 原始证据 | 实现依据 |
| --- | --- |
| `461952.bb4069593d.js:3`，offset 265106、268319 | Shell 标题、命令/输出复制、右下状态；footer padding 2px 10px 4px，14px/21px。仅真实退出码 0 显示成功，模拟来源单独标明。 |
| 同文件 offset 362000、371652、373538 | 文件行展开成“已编辑的文件”，内联 diff 边框、12.5px 圆角、6px 顶边距；header 2px 10px/13px；正文最大 240px。 |
| `66582da7ee6b.css:2`、`872923.60d7a963e2.js:1` | diff 12px/21.6px、统一视图单行号、sticky gutter、4px 状态色条、独立双轴滚动；行号按真实 hunk 的旧/新行计算。 |
| `61942.22f64304dc.js:1`、`366084.40f42f94b0.js:1` | Codex 亮/暗代码主题；增删色 light #00a240/#ba2623，dark #40c977/#fa423e；整行与 gutter 背景按源码 lab 混色比例。 |

| 本轮文件 | 具体改动 |
| --- | --- |
| `src/ConversationComposer.tsx`、`ConversationToolbar.tsx` | 输入框移除常驻上下文条、文件计数条；计数和口径说明放入 Worker 浮层，Router 保持可见。 |
| `src/ChatTimeline.tsx` | 摘要保留实际动作，技术字段默认收起；普通任务折叠，审批/需处理直接展示；失败、未知、模拟不被长摘要裁掉；Shell footer、独立输出复制；文件展开直接显示历史 diff。 |
| `src/InlineArtifactDiff.tsx` | 按精确回执读取并校验路径；折叠不读取；真实统计、模拟标识、加载/错误/重试/复制及完整历史入口；内存缓存与代次隔离，保留拆组前的双轴阅读位置。 |
| `src/OperatorSettings.tsx`、`ProviderCredentials.tsx`、`App.tsx` | 连接与密钥同页；技术连接字段、高级预算/网络折叠，首次缺字段及错误自动展开；已有 ID/ref 自动填入，密钥仅保留于密码输入框；原 POST/PATCH/invoke 与确认契约保留。 |
| `src/ModelProfileSummary.tsx`、`App.tsx` | 模型身份/状态/编辑作为列表首层，能力、价格、测量和技术信息逐层展开；新档案自动 ID、默认供应商，未知能力不推断。 |
| `src/FilePreviewWorkspace.tsx`、`usePreviewLayout.ts`、`App.tsx` | 文档左/文件树右，树内分已更改目录与所有文件；标签/面包屑/预览源码工具条；可扩展完整文件工作区并恢复分栏，聊天 DOM、草稿和位置保留。 |
| `src/CodePreview.tsx` | 同一真实 unified diff 渲染用于会话与右栏；hunk 行号、红绿整行、4px 色条、行内实际替换段高亮、240px compact；保留虚拟滚动、查找与阅读位置。 |
| `src/style.css` | 单一 CSS 中落实上述源码数值、明暗配色和 150ms 动效；不增加 CSS/图标库。 |
| `tests/ui-*.playwright.js`、本文、`README.md`、`UI-TODO.md` | 调整浏览器操作路径，补设置保存/风险/内联 diff/完整预览检查；既有 `.test.mjs` 断言未改。 |

按观感/项目功能调整：完整预览的树使用 `clamp(260px,44%,560px)`，分栏保持 `clamp(170px,28%,244px)`，避免聊天仍显示时挤压正文；行内替换高亮使用源增删色 24% 与 2px 圆角，是轻量字符比较的可读性调整。代码高亮为现有无依赖词法渲染，未复制第三方编辑器引擎；没有分支比较/外部编辑器接口的操作不伪造。

本段交互续作完成后，12 套浏览器夹具共 600 项检查及 smoke 通过，全部未捕获页面错误为 0；结果位于 `output/playwright/simple-*.json`。后续比例修订与最终构建以本文最上方 2026-11-16 记录为准；前轮真实原生/DPI 验收的缺口继续保留。

日期：2026-11-15。按用户最新截图与现有 UI-TODO 直接修改代码；未调用生图、未新增应用依赖。用户已允许必要的只读 Tauri/Core 接口，本轮据此接通真实文件，不更换 React 18/Tauri/纯 CSS 技术栈。

本轮续作完成项目归属契约与不可变回执历史更改，并按同内容缩放矩阵校准比例。TODO现为25/28有对应实现/验证，01/23/27仍有原生DPI及完整页面验收缺口。功能回归通过不等于逐像素1:1。

## 本轮结果与文件改动

| 文件 | 改动与目的 |
| --- | --- |
| src/App.tsx | 接通文件分栏、历史/当前文件独立入口；本轮按连接/空间/代次隔离历史增删数，读取后回填对应活动，跳转真实已加载来源消息；预览初始比例改为大窗41%、<=1280px为56%。原IME、重试、审批与版本检查保留。 |
| src/WorkspaceSidebar.tsx、DesktopNavigation.tsx | 当前真实工作空间作为可折叠项目，唯一对话缩进其下；紧凑模式复用同 DOM；空间菜单保留关闭过渡与键盘操作。 |
| src/QuestWorkspace.tsx | 项目→任务→详情，显式 project/workspace/metadata 归属与当前空间 fallback；同名不同 ID 不合并；需处理/运行优先、已结束折叠，项目/任务搜索、键盘与轮询保持。 |
| src/FilePreviewWorkspace.tsx | 新文件工作区：标签、树、独立滚动、分隔条、复制/刷新、Markdown/源码/diff 视图、图片、PDF 字节和 Blob 生命周期；错误、大小、编码与无 Git 状态明确。 |
| src/workspaceFiles.ts | 类型化 FileSource 契约、4 个 native invoke、浏览器真实 File API 导入；目录共同根剥离、相对路径规范和读取上限。 |
| src/projectContext.ts、backend/src/forge_core/project_context.py、backend/src/forge_api/main.py | 设置/任务读取补version1的current_workspace契约与明确能力边界，侧栏/任务共用解析。修正Core JSON字符串metadata漏解析，保持列表字段与分页；metadata不授予文件权限。main.py另注册独立认证历史只读GET接口。 |
| src/artifactHistory.ts、ArtifactHistoryPreview.tsx | 精确回执类型/契约校验，真实diff/增删数/来源/模拟标记；独立身份键、Abort/30秒超时/序号隔离，错误或加载不显示旧内容，刷新/关闭重开保持DOM与滚动。 |
| backend/src/forge_core/artifact_diff.py | 从不可变action_intents正文与前驱回执核验历史链，生成历史diff；不读取当前文件或Git作为历史版本，不改变写入/审批。敏感路径、损坏/不可用、体积超限分别处理。 |
| backend/tests/test_artifact_diff_ui.py、test_project_context.py、seed_ui_artifact_history.py | 历史链/安全/错误/来源边界及单空间契约；独立工作空间用真实Store/ToolEngine创建/修订文件，模型协议明确Mock。 |
| src/CodePreview.tsx、FileMarkdown.tsx | 只读行号/词法着色/虚拟滚动/查找/换行；安全 Markdown 渲染及相对文件链接；源码不执行。 |
| src/ChatTimeline.tsx、AnimatedDetails.tsx | 紧凑可点文件行及预览回调、完整长命令展开、真实调用次数、失败/模拟/未知状态口径、短暂复制反馈；写入/修订用铅笔图标，活动组成功 live 写入路径去重，首 3 条/更多展开；已有安全 Markdown 保护区字节不变。 |
| src/ConversationRuntime.tsx | 按 run ID 匹配的 Worker 只读详情，角色/供应商/模型/当前状态/真实计数；历史事件与最近执行状态分开，缺失详情不以新 run 替代。保留原任务跳转。 |
| src/ConversationToolbar.tsx、ConversationComposer.tsx、DisclosurePopover.tsx | 可读 Router 状态、文件/变更入口、对话文件汇总；浮层初始焦点、方向键（含 summary）、Tab 离开、Escape/外点关闭；Worker 浮层长内容独立滚动，反馈不长期占高度。 |
| src/usePreviewLayout.ts | 150ms 测量宽度/透明度开合，退出保留 DOM 并设 inert；每帧保护聊天锚点，支持快速反向与减少动效。文件组拆分也使用稳定文件锚点。 |
| src/SettingsView.tsx、OperatorSettings.tsx、ModelProfileSummary.tsx、BudgetOverview.tsx | 共用分组与设置行；运行配置按功能拆组，模型首层减负、完整字段展开，预算记录 16px 内边距；所有输入名称及原提交确认保留。 |
| src/style.css | 唯一纯CSS样式；本轮新增历史diff层级、回填增删数，树列clamp(170px,28%,244px)，设置说明颜色从tertiary约.494校准secondary .65。 |
| src-tauri/src/workspace_files.rs | 新增受工作空间限制的目录、文本/二进制、Git status/diff；Windows 句柄再核验、符号链接/重解析拒绝、固定 Git 参数/时限/输出上限；真实临时 Git 测试验证索引不写。 |
| src-tauri/src/main.rs、core_host.rs、lib.rs、tauri.conf.json | 注册4个只读命令、固定启动根目录，CSP允许blob图片/PDF。本轮devUrl改127.0.0.1:5173与Vite脚本一致，debug导航白名单同时接受localhost/127.0.0.1端口5173；release顶层导航范围保留。 |
| backend/src/forge_core/timeline.py | 仅将 file_revise 加入已有安全工具及相对路径投影集合（2 行）；继续过滤敏感路径，不返回 stdout/正文/原始 payload。 |
| backend/tests/test_timeline_file_preview.py | 真实 Store/ToolEngine 回执验证安全投影、敏感路径过滤及 failed/uncertain 保留。 |
| tests/ui-preview.playwright.js、run-ui-smoke.mjs | 真实本地 File API＋两页 PDF 的 36 项验收，含连续宽度、退出 inert、快速重开与减少动效；可选择完整 Chromium 通道。 |
| tests/ui-conversation.playwright.js、ui-activity-files.playwright.js | 对话新增 Worker 详情/历史与当前状态/短窗滚动/预览逐帧锚点；文件组新增 14 项投影验收，覆盖筛选、去重、更多、真实路径、轮询与重分组阅读位置。 |
| tests/ui-interactions.playwright.js、ui-workspace.playwright.js | 适配项目内真实可见排序、已结束折叠与模型详情展开，并补充多项目/轮询行为断言。未修改 *.test.mjs 断言。 |
| tests/artifact-history.test.mjs、project-context.test.mjs、ui-history.playwright.js | 新增契约单测6项、独立历史UI29项；既有.test.mjs断言未修改。新preview脚本改用操作前宽度验证+2/拖动，避免将旧默认56%硬编码成58。 |
| tests/run-ui-scale.mjs | 同内容/同滚动/明暗主题的DPR1/1.25/1.5矩阵，386项/14案例/57图；量测参考computed style并明确不替代Windows系统DPI。 |
| README.md、desktop/README.md、UI-TODO.md | 启动/边界/契约、已完成与未验收状态及本记录。 |

以上路径除 backend 外均相对 desktop/。仓库其他已有后端、文档改动保留，未在本轮重写。

## 按规格实现与功能适配

| 类型 | 实际数值/行为与理由 |
| --- | --- |
| 按参考 | 52px 图标栏＋288px 项目面板；聊天最大 736px，在分栏剩余宽度内可缩；设置分类 288px/内容最大 760px。 |
| 按参考 | 活动行 21px、14px 文字、16px 图标、gap 4px，tertiary→默认色 hover；命令卡圆角 12.5px、边框 0.8px rgba(13,13,13,.118)、输出最高 144px。 |
| 按参考 | 颜色、height/opacity、chevron 使用 150ms cubic-bezier(0,0,.2,1)；展开 0→scrollHeight→auto，减少动效立即完成。textarea 在 40–180px 变化；预览宽度与透明度用同曲线逐帧更新，关闭到 0 后隐藏，反向按剩余距离缩短时长。 |
| 按截图适配 | 壳层底色从 #f5f5f5 调为 #eef4f9，与用户最新 Windows 截图浅蓝外壳一致；主区仍白色。系统字体用 400/500 回退，未新增字体。 |
| 按截图校准 | 大窗预览56%→41%：2237CSS窗口实测1058.95→775.30CSS，按推测125%栅格约969.12物理，参考截图约959物理；起点约1820.88物理，参考约1837物理。原图无DPI元数据，因此仅记录约16px差距，不称逐像素一致。 |
| 按功能适配 | <=1280px初始窗口保留56%，1152CSS下源码从41%时170.45扩为291.36CSS；手动34–76%，1000px以下单面板。树宽clamp(170px,28%,244px)，中档160px、小窗220/160px；为源码阅读保留空间。 |
| 按参考校准 | 设置说明颜色从rgba(13,13,13,.494)改为secondary .65，匹配参考说明层级；活动摘要继续tertiary。设置标题28px/33.6px，标签13px/18px/500、行padding12px16px均匹配参考computed style。 |
| 按功能适配 | Markdown 文件 h1=26px/34px、正文14px/22px、padding20px24px；源码12px/22px、160行窗口。超过5000行关闭换行，单行超过20000字符跳过词法分段，避免大文件卡顿。 |
| 按功能适配 | 设置保持已有三组七页；当前工作空间单组与唯一长期对话；不补无接口的多空间/聊天或假开关。任务说明仅一行，目标全文留详情。 |
| 按功能适配 | 顶部变更为当前工作空间 Git，底部为已加载对话成功 live 写入记录；失败/模拟写入不算已编辑。暂存区单独差异明确标注 scope。 |

## 实际验证输出

最终 npm test（退出 0）：

```text
> forge-desktop@0.2.0 test
> node --test tests/*.test.mjs
ℹ tests 18
ℹ suites 0
ℹ pass 18
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 5020.548
```

最终 npm run build（退出 0）：

```text
> forge-desktop@0.2.0 build
> tsc && vite build
vite v6.4.6 building for production...
✓ 305 modules transformed.
dist/index.html                   0.35 kB │ gzip:   0.26 kB
dist/assets/index-CzHVRWda.css   76.59 kB │ gzip:  13.67 kB
dist/assets/index-DMRxYxDO.js   485.94 kB │ gzip: 150.82 kB
✓ built in 2.70s
```

完整日志：[npm test](D:/Competition_this/Agent/desktop/output/playwright/final-npm-test.txt)、[build](D:/Competition_this/Agent/desktop/output/playwright/final-npm-build.txt)。

| 验证 | 实际结果与边界 |
| --- | --- |
| ui-smoke.playwright.js | passed；2次发送、同request ID重试、singleChat=true。最终活动原摘要定位保持，模拟标记单独显示。 |
| ui-layout.playwright.js | 167项，包含尺寸、安全、IME、轮询、审批、明暗/小窗口。 |
| ui-workspace.playwright.js | 72项，原计划与团队版本绑定。 |
| ui-interactions.playwright.js | 82项，新增多项目独立归属、筛选/折叠/键盘与刷新位置保持。 |
| ui-conversation.playwright.js | 85项，Router/Worker、来源、详情精确匹配/缺失、历史与当前状态、短窗/键盘、活动重新分组及预览逐帧阅读锚点。 |
| ui-preview.playwright.js | 36项/5截图；真实File API、1200行源码、Markdown安全/相对链接/切换位置、两页PDF字节/下载事件、分栏/草稿/刷新/动效。最终使用系统Edge；bundled headless Chromium的pdfViewerEnabled=false，产品明确降级为下载阅读。 |
| ui-activity-files.playwright.js | 14项/1截图；合成只读投影，成功 live 筛选、路径验证/去重、首3/更多、点击、轮询和组拆分展开/阅读锚点。没有伪造文件字节。 |
| ui-history.playwright.js | 29项，0写请求/0storage/0页面异常；同路径多回执/当前文件隔离、真实增删数、虚拟化、刷新/重开滚动、来源导航/未加载提示、404/422/非法契约、旧代次迟到响应拒绝。HTTP为显式夹具，当前文件为真实File API。 |
| run-ui-scale.mjs | 386项/14案例/57截图/0页面异常，包含明暗、1200行源码、长设置与相同滚动状态。DPR模拟不替代系统DPI。 |
| Rust lib | cargo +1.90.0 test --lib -- --include-ignored：17 passed; 0 failed; 0 ignored；13.39s。使用独立offline嵌入runtime与测试目录，含Core握手/重启/退出/隔离备份恢复/写入锁及后代进程清理。 |
| Native build | cargo +1.90.0 build --bin forge-desktop 通过；debug exe位于 src-tauri/target/debug/forge-desktop.exe。 |
| Core | python -m pytest -q tests/test_artifact_diff_ui.py tests/test_project_context.py tests/test_timeline_file_preview.py tests/test_desktop_api_m7.py：46 passed in45.50s。独立复核未发现历史安全链/stale隔离的实质问题。 |
| 差异/安全 | git diff --check 通过；Markdown保护区 SHA256 与HEAD一致；api.ts/logic.ts/timeline.ts/依赖及锁文件未改；无新增浏览器storage。 |

最终八组UI合计485项数值检查，另smoke通过；各组页面异常0。HTTP夹具、File API与Core测试分开记录。首次高并发运行smoke点击与layout轮询超时，单独重跑通过；Edge预览动画采样早期运行失败，最终完整36项通过，未放宽动效断言。保留失败诊断，最终日志为output/playwright/final-*.json。

前一轮原生观察采用缺失runtime，仅测文件桥和PDF控件，见[native-preview-evidence.txt](D:/Competition_this/Agent/desktop/output/native-preview-evidence.txt)。本轮离线准备独立Python3.13.17 runtime，真实嵌入Core与隔离历史工作空间启动成功；Windows AppliedDPI=120（125%），WebView2实测innerWidth1440/height900/devicePixelRatio1.25，实际目录和19.4KB PDF正文已显示。模型协议为Mock，文件/SQLite/ToolEngine回执真实；没有调用外部模型。后续窗口操作被用户物理Esc停止，已停止继续操作窗口，未将未测原生组合记为通过。

原生build通过，debug exe与Vite的IPv4地址/导航白名单已同步。runtime安装只使用现有offline wheel缓存并写独立输出目录；pip报告了全局已有impacket/frida相关依赖冲突，但target安装与隔离导入/执行验证成功，没有更改全局Python环境。

代表截图：[代码](D:/Competition_this/Agent/desktop/output/playwright/preview-code-light.png)、[Markdown](D:/Competition_this/Agent/desktop/output/playwright/preview-markdown-light.png)、[两页PDF](D:/Competition_this/Agent/desktop/output/playwright/preview-pdf-light.png)、[暗色](D:/Competition_this/Agent/desktop/output/playwright/preview-markdown-dark.png)、[项目任务](D:/Competition_this/Agent/desktop/output/playwright/rebuild-forge-interactions-project-groups.png)。

## 当前限制与仍未验收

- 未实现跨工作空间管理/切换或多聊天。真实Core无独立多项目CRUD契约；UI只消费显式归属并回退当前空间。
- 文件树过滤仅针对已加载目录；文本仅UTF-8/2MiB，PDF图片24MiB，单目录5000项，Git4MiB/8秒。词法着色为轻量实现，没有LSP/编辑器引擎。
- 历史diff单版本256KiB/2000行，链64版本/累计4MiB，diff512KiB。过大422，不可用/敏感/损坏404；不返回内部细节。历史来源仅为“关联任务的请求”，不证明精确聊天轮次因果关系。模拟回执可有真实工具文件，但不证明真实模型执行。
- PDF可读已在WebView2实测；原生下载落盘/系统保存流程未完成验收，浏览器下载事件与文件名已验证。
- 系统当前125%已观察，受控Windows150%、原生七页/最小窗/全部长内容、下载落盘与打包版未全验收，不能称逐像素1:1。浏览器缩放证据及实际差值见[对照报告](D:/Competition_this/Agent/desktop/output/scale-matrix/visual-comparison.md)。
- 原生Core握手/重启与独立测试目录备份恢复通过Rust17项；真实供应商、真实凭据写入及用户工作空间维护没有执行。已有写入审批保留原流程。
- 当前空间项目与历史基线已落地；多项目CRUD/空间切换不在用户确认范围。文件树为手动展开/刷新，活动聚合按实际相邻事件组，绝不以任务历史全部修改冒充最新聊天轮次。28项状态见 [UI-TODO.md](D:/Competition_this/Agent/desktop/UI-TODO.md)。
