# Unified Personal MCP

一个可配置的 Windows 程序，把编码、桌面、Chromium 和 WebView2 操作放到同一个 MCP 接口，通过 **OpenAI Secure MCP Tunnel** 连接自己的开发机。

组合的职责：

| 组件 | 在本项目中的用途 |
| --- | --- |
| coding-tools-mcp | 原有 HTTP/MCP 协议、认证与 18 个编码工具 |
| BF | 窗口操作、独立浏览器、WebView2、工作流资源管理 |
| DesktopCommanderMCP | 借用 MIT 源码中的 PATHEXT 修复与渐进搜索设计 |
| OpenAI tunnel-client | 唯一远程连接通道 |

统一接口含 49 个工具。每个工作流选择一个具体项目，拥有自己的编码进程、搜索、浏览器和由它启动的应用。结束时释放这些资源；附加到已有应用时保留原应用。

## 快速开始

Windows x64、CPython 3.13.14：

```powershell
python -m venv .venv-host
.venv-host\Scripts\python.exe -m pip install -r requirements-windows.lock
.venv-host\Scripts\python.exe -m pip install --no-deps -e .
.venv-host\Scripts\python.exe -m personal_mcp gui
```

启动浏览器和隧道前，按[构建说明](docs/personal/构建说明.md)准备 resources 中的固定版本资源。已有完整运行包时直接打开 UnifiedPersonalMCP.exe。

在配置窗口填写工作区父目录、私有目录、设备名称、隧道 ID 与运行密钥引用。密钥只保存在本机私有目录或指定环境变量中。另一台设备使用同一份程序，重新填写自己的配置。

完整[使用说明](docs/personal/使用说明.md)、[验证状态](verification.md)、[集成来源](third_party/README.md)。

## 客户端使用

1. UnifiedTask begin 指定工作区中的具体项目，保存仅返回一次的 workflow_id。
2. UnifiedTask activate，然后在编码、桌面和浏览器工具中使用同一个 workflow_id。
3. 每个写入操作使用唯一 request_id。结果为 unknown 时先观察，不自动重做。
4. UnifiedTask end 释放资源。

应用配置可随项目保存在 .bf/apps/*.json，并使用相对路径。示例在 [examples/personal](examples/personal)。桌面应用由 LaunchApplication 启动；App 用于切换和调整已有窗口。

## 边界

这是面向本人开发机的工具集合。可信模式中的命令具有当前 Windows 用户的权限，项目检查不是操作系统沙箱。不要接入不受信任的调用者或工作区。

不包含 Desktop Commander 的托管云配对、遥测服务、Cloudflare 通道或个人浏览器配置。各上游文件保留原许可证；第三方依赖适用自己的许可证。仓库中没有本机运行配置、密钥、状态数据库或截图。
