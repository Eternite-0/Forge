# Forge

Forge 项目开发仓库，后续开发涉及 Python、JSON、前端和 Tauri 桌面端。

## 获取代码

```powershell
git clone https://github.com/Eternite-0/Forge.git
cd Forge
```

当前仓库包含 Git、协作约定和环境检查工具。应用功能、前端框架及 Python 与 Tauri 的交互方式确定后，再加入应用代码和对应的依赖配置。

## 检查开发环境

在 Windows PowerShell 或 PowerShell 7 中运行：

```powershell
.\scripts\check-env.ps1
.\scripts\check-env.ps1 -NativeSmoke
```

第二条命令会编译并运行一个最小 Rust 程序，用于检查 Windows MSVC 原生编译链。临时产物位于已忽略的 `.local/native-check/` 中。详细说明见 [开发指南](docs/DEVELOPMENT.md)。

## 开发协作

- 开发前阅读 [AGENTS.md](AGENTS.md)。
- `main` 作为主分支，新功能建议使用 `codex/<功能名>` 分支。
- 提交前检查改动并运行与改动相关的验证。
- 环境变量示例可以提交为 `.env.example`，实际密钥保存在本地。

## 基础配置

- `.editorconfig`：UTF-8、缩进和行尾格式。
- `.gitattributes`：文本文件默认使用 LF，Windows 批处理文件使用 CRLF。
- `.gitignore`：排除本地环境变量、临时文件和编辑器个人配置。
- `.vscode/extensions.json`：推荐 Python、Rust 和 Tauri 编辑器扩展。
