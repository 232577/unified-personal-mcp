# Unified Personal MCP

一个可配置的 Windows 程序，把编码、桌面、Chromium 和 WebView2 操作放到同一个 MCP 接口，通过 **OpenAI Secure MCP Tunnel** 连接自己的开发机。

组合的职责：

| 组件 | 在本项目中的用途 |
| --- | --- |
| coding-tools-mcp | 原有 HTTP/MCP 协议、认证与 18 个编码工具 |
| BF | 窗口操作、独立浏览器、WebView2、工作流资源管理 |
| DesktopCommanderMCP | 借用 MIT 源码中的 PATHEXT 修复与渐进搜索设计 |
| OpenAI tunnel-client | 唯一远程连接通道 |

统一接口含 49 个工具。每个工作流选择一个默认项目，拥有自己的编码进程、搜索、浏览器和由它启动的应用。结束时释放这些资源；附加到已有应用时保留原应用。

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

需要调用项目外的共用构建工具或跨目录工作时，在“代码操作权限”选择“完全控制本机”（配置值 `full_control`）。该模式允许当前 Windows 用户权限范围内的绝对路径读写和命令目录；原有“允许项目内执行”和“限制执行”继续限制在各自项目内。安装配置必须由机主选择模式，调用方不能用工具参数临时扩大权限。

完整[使用说明](docs/personal/使用说明.md)、[验证状态](verification.md)、[集成来源](third_party/README.md)。

## 客户端使用

1. UnifiedTask begin 指定工作区中的具体项目，保存仅返回一次的 workflow_id。
2. UnifiedTask activate，然后在编码、桌面和浏览器工具中使用同一个 workflow_id。
3. 每个写入操作使用唯一 request_id。结果为 unknown 时先观察，不自动重做。
4. UnifiedTask end 释放资源。

完全控制模式下，`exec_command.workdir` 可指定绝对目录，每次调用独立生效；相对路径仍以工作流默认项目为基准。调用共享构建脚本时，保持目标项目的工作目录，通过绝对路径指定脚本，并把目标项目配置作为参数。切换一次命令目录不会改变其他任务的目录，也不会改变后续文件工具的默认项目。Git 查询和 SearchSession 默认关联所选项目；在其他仓库工作时使用该仓库的新工作流，或显式指定命令目录。

应用配置可随项目保存在 .bf/apps/*.json，并使用相对路径。示例在 [examples/personal](examples/personal)。桌面应用由 LaunchApplication 启动；App 用于切换和调整已有窗口。

## 多项目并行

不同项目可以同时执行命令，各自拥有工作目录和资源。单个工作流最多同时运行 16 条命令；没有固定的项目数量上限，实际容量取决于机器资源。

0.1.2 增加会话占用详情和空闲回收。遇到 `PROJECT_BUSY` 时，返回占用项目及目录重叠关系；`UnifiedTask {"action":"list"}` 可列出本账号会话。遗失工作流令牌后，可用列表中的 `workflow_ref` 调用 `release_idle`，它只回收超过空闲期限、没有在途操作及运行资源的会话。默认空闲期为 30 分钟，也会自动回收；状态查询不会延长空闲期。仍在运行命令、应用、浏览器或搜索的会话会保留。

配置窗口可调整浏览器总会话数（默认 8，最多 32）、WebView2 受管理实例数（默认 4，最多 16）、保留的搜索数（默认 16，最多 64）与工作流空闲期。保存后重启服务生效。前台键鼠仍一次只交给一个任务。同一项目或父子目录的写入会互斥；需要同时修改同一项目时，使用独立 Git 工作副本。共享脚本使用绝对路径调用，无需占用整个工作区父目录。

## 边界

这是面向本人开发机的工具集合。完全控制模式允许操作项目外的本机文件和工具，权限等同运行程序的 Windows 用户，不会绕过 Windows 权限或管理员确认。可信模式中的项目检查也不是操作系统沙箱。不要接入不受信任的调用者或工作区。

任务仍保留进程、浏览器、请求记录和默认项目写入租约。完全控制模式中的跨目录访问不会自动隔离共享文件、端口或数据库；并行任务应使用不同工作副本并协调共享资源。子进程默认继承核心运行环境，敏感环境变量仍过滤，必要的工具环境可明确提供。

不包含 Desktop Commander 的托管云配对、遥测服务、Cloudflare 通道或个人浏览器配置。各上游文件保留原许可证；第三方依赖适用自己的许可证。仓库中没有本机运行配置、密钥、状态数据库或截图。
