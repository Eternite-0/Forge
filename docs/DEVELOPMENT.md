# 开发环境

开发方向为 Python、JSON、前端和 Tauri 桌面端。当前准备基础工具和协作配置，应用框架和业务结构将在明确功能后确定。

## Windows 工具

| 工具 | 用途 |
| --- | --- |
| Git | 版本管理和远端同步 |
| Python 与 pip | Python 开发与依赖管理 |
| Node.js、npm、pnpm | 前端依赖和构建工具 |
| Rust、Cargo 与 MSVC target | Tauri 原生代码编译 |
| Visual Studio C++ 工具与 Windows SDK | Windows 编译、链接 |
| WebView2 Runtime | Tauri Windows 界面运行环境 |

运行以下命令检查工具：

```powershell
.\scripts\check-env.ps1
.\scripts\check-env.ps1 -NativeSmoke
```

`-NativeSmoke` 使用本机 Rust 标准库编译并运行最小程序，不下载依赖。它验证 Rust 和 Windows 链接工具的基本可用性；完整 Tauri 应用的构建需要在加入应用代码和依赖后另外验证。

检查脚本失败时会返回非零退出码。生成文件位于 `.local/native-check/`，不会提交到 Git。

## 后续加入应用代码时

- 明确前端框架、Python 的职责以及 Python 与 Tauri 的通信方式。
- Python 使用项目独立的虚拟环境，并提交实际使用的依赖清单或锁文件。
- 前端确定一个包管理器并提交对应锁文件。
- Tauri 应用提交 `Cargo.lock`，按应用需要配置原生命令和权限。
- 把实际可运行的安装、开发、构建和验证命令补充到 README。

## Git 协作

主分支为 `main`。新功能可从最新主分支建立 `codex/<功能名>` 分支。提交前检查 `git diff --check`，并完成相关验证。

本地仓库配置启用了远端分支清理和仅快进拉取。文本文件行尾由 `.gitattributes` 管理，编辑器格式由 `.editorconfig` 管理。
