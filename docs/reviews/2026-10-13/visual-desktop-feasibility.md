# 可视化桌面控制可行性核查

核查日期：2026-10-13。范围：当前 0.1.13 源码、OpenAI 官方 Plugins/MCP Apps/Secure MCP Tunnel 文档。本文是设计研究，没有启动、截图或操控生产桌面，没有修改服务、隧道、密钥或产品代码。原有 58 工具按任务给定基线保留；本次未实例化桌面服务来重新统计工具数。

## 结论

在支持 MCP Apps 的 ChatGPT 宿主里，**复用现有 MCP 隧道做“侧面板画面自动刷新 + 用户点击/输入 + 动作后回显”有官方协议依据，值得实施最小闭环并实测**。无需页面直接连接远端 `127.0.0.1`，也无需为远端机器开放公网端口。界面通过宿主 `tools/call` 调用私有 MCP 服务，截图随工具结果返回。

这条路径是截图轮询，不能称为连续视频。当前宿主支持、工具结果大小、隧道耗时、捕获耗时及最终刷新率均尚未测量；不能承诺实时视频、固定 FPS 或亚秒输入反馈。如果验收要求是流畅连续视频和持续鼠标键盘控制，需要另行设计捕获/编码/媒体传输及控制端，优先考虑由远端主动建立的受认证出站连接。现有隧道可继续承担会话创建/授权/状态等控制请求，但不是已验证的视频传输方案。

当前产品外层 HTTP 服务只实现工具接口。**不能只在内层 BF FastMCP 上注册 HTML 资源，就宣称现有插件会显示界面**；真正外层 host 必须公开资源能力、资源读取、工具 UI 元数据，并保留原协议及鉴权。

## 官方确认了什么

| 能力 | 官方依据 | 可得出的结论 |
| --- | --- | --- |
| MCP App HTML 与 UI bridge | [Add UI](https://developers.openai.com/plugins/build/chatgpt-ui)、[Quickstart](https://developers.openai.com/plugins/build/app-quickstart) | UI 资源 MIME 为 `text/html;profile=mcp-app`；工具描述通过 `_meta.ui.resourceUri` 指向资源；iframe 初始化后可通过 bridge 的 `tools/call` 调用服务。 |
| 会话侧面板/全局入口 | [Extensions](https://developers.openai.com/plugins/build/extensions) | 工具 `_meta["openai/ui"].entrypoints` 使用 `[{"type":"thread"}]` 可声明会话侧面板；`global` 是全局侧边入口。入口声明须经目标宿主实际加载验证。 |
| 模型可见性与组件专用数据 | [Reference](https://developers.openai.com/plugins/reference) | `_meta.ui.visibility:["app"]` 声明仅 UI 可用。工具返回顶层 `_meta` 隐藏于模型；`content` 和 `structuredContent` 面向模型与组件。宿主的 `openai/widgetSessionId` 可用于挂载关联。 |
| 不开入站端口 | [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) | 客户端通过出站 HTTPS 拉取 MCP 请求、回传结果。官方也说明流式 connector 请求可转发中间 SSE；这没有证明 WebSocket、WebRTC 或任意视频流可穿过当前通道。 |
| 各宿主能力不可混同 | [Troubleshooting](https://developers.openai.com/plugins/deploy/troubleshooting) | 官方明确该页的 UI/组件/客户端认证检查描述 ChatGPT 行为。共享插件目录与工具可用，不构成所有 Codex 界面支持侧面板、UI bridge 的证明。 |
| MCP Events | [MCP Events](https://developers.openai.com/plugins/build/mcp-events) | 当前集成是 webhook 事件与任务触发，文档明确不支持其轮询或流式交付。不能拿它作为连续画面推送接口。 |

官方文档没有给出本用途的帧率、工具调用频率、画面延迟或视频带宽服务等级。插件扩展还有产品/账户投放限制；应检测所需能力，不按产品名称硬编码支持判断。Secure MCP Tunnel 适合私有连接；官方不将其作为公开插件发布端点。本项目可以继续是私人插件，不需要因加入 UI 而公开服务。

## 当前源码事实与接入点

| 当前代码 | 核查结果 | 必要接入 |
| --- | --- | --- |
| `personal_mcp/service.py:21,70,149` | `PersonalMCPHandler` 继承自定 HTTP handler；`ServiceHTTPServer` 才是隧道目标。 | 在外层资源读取路径沿用 bearer、Origin、envelope 和协议版本校验。资源不能绕到无鉴权静态 HTTP 路径。 |
| `coding_tools_mcp/server.py:1478,1494` | initialize/discover 只声明 `capabilities.tools`。 | 对个人服务声明实际实现的资源能力；不改变无 UI coding 服务的能力声明。 |
| `coding_tools_mcp/protocol.py:49,425,451` | modern/legacy dispatcher 无 `resources/list`、`resources/read`。虽然 mirrored-name 表列了 `resources/read`，代码注释说明它尚未实现。 | 增加经审核的固定资源 dispatch，覆盖两个协议路径及 modern `Mcp-Name`/版本元数据；不能把 UI bridge 版本当作 server MCP 版本。 |
| `coding_tools_mcp/server.py:4970,5533` | GET `/mcp` 明确拒绝 SSE；POST 返回完整 JSON，`no-store`。请求体上限 1 MiB。 | MVP 使用有界单次 JSON 工具结果。1 MiB 是请求上限，不能冒充截图响应上限。当前没有媒体 URL/二进制流服务。 |
| `personal_mcp/catalog.py:102` | 内层工具描述被重建，仅保留 name/description/schema/annotations，未传递内层 `_meta`。 | 在外层 catalog 为新工具显式加入 UI metadata，校验/哈希/展示加工须保留它。旧工具元数据不应无审核地透传。 |
| `personal_mcp/host.py:25,285` | `tool_result` 把整个 payload 写入 text 和 structuredContent；普通分派强制先取 `workflow_id`。 | 组件秘密必须单独进入返回顶层 `_meta`。UI 专用工具需要分支验证 UI capability，再解析到内部 workflow，不让 UI 持有原 workflow 凭据。 |
| `coding_tools_mcp/protocol.py:496` | `tools/call` 只把 name/arguments 与有限 RequestContext 送入 runtime，未保留全部请求 `_meta`。 | 如需使用宿主 metadata，须有意保留需要的字段并将它视为不可信输入。不能假设 runtime 已收到可信 widget 身份。 |
| `personal_mcp/tunnel.py:131` | 配置只映射本机 `/mcp`，backend Authorization 由客户端注入；健康端口 loopback。 | 资源和 UI tools 经同一 `/mcp` 走现有鉴权，无须新公网端口。不可把注入的 bearer 给 iframe。 |
| `bf_automation/window_control.py:73,786` | 现有截图每次单独建立 WGC 捕获，首帧到达即停止；PNG 编码。窗口截图写入 task captures 目录。 | 能复用有限截图逻辑，尚非持久视频采集。轮询须有容量/保留策略，避免每帧无界落盘；默认禁止最小化窗口 staging。 |
| `bf_automation/task_store.py:227,263`、`bf_automation/window_control.py:39,68` | 窗口绑定、`hwnd/pid` 检查、操作锁与写 claim 已存在。 | 新控制入口须复用这些约束，额外绑定画面版本、目标身份和坐标变换。 |
| `bf_automation/lease.py:12`、`personal_mcp/bf/bootstrap.py:9` | 前台输入共享独占 lease，连同一 workflow 的请求也串行。 | 新 UI 前台操作必须进入同一 lease。其锁不阻止物理用户或外部程序抢焦点，最终输入前仍须检查目标前台。 |
| `personal_mcp/operations.py`、`personal_mcp/host.py:299` | 持久 request_id 回执、不确定结果写入围栏与待完成操作检查已存在。 | UI 动作复用回执机制。lost/unknown 的动作只能查询原 request_id，不能换 ID 自动重放。 |

`pyproject.toml` 核对为 0.1.13，包含 FastMCP、mcp、Windows Capture、OpenCV 等既有依赖。当前没有找到产品内既有 MCP UI HTML/JS 资源。上表引用源码是本次静态读到的事实，没有以历史测试报告充当运行证明。

## 推荐接口与资源设计

以下命名/字段是拟议协议，不是已实现工具。保持原 58 工具原契约，追加明确边界的 UI 入口。

### 服务资源

- 静态固定 URI：`ui://unified/desktop-console/v1.html`。不接受任意文件路径/URL，不把凭据写入 URI；破坏性 UI 变更更换版本 URI。
- `resources/list` 返回这个固定资源的 name/URI/MIME；`resources/read` 只允许精确 URI，返回 `contents:[{uri,mimeType,text,_meta:{ui:{csp}}}]`。HTML/JS/CSS 随资源内联，不依赖第三方 CDN、远端 loopback 图片链接或公开图片存储。
- MVP 没有页面直连网络需求，CSP 精确收紧 `connectDomains/resourceDomains`；data/blob 图片实际可用性须在宿主测通，不能仅凭 CSP 声明就认为成功。必要时通过内存 Blob 解码到 canvas；校验返回 MIME、尺寸、大小上限。
- UI 有状态数据通过工具结果更新，不能缓存静态 HTML 中的 workflow 状态。资源 HTML 可以缓存；会话凭据及画面必须 `no-store`、短期保留。
- 当前 Python/custom host 不必为此替换整套传输。官方 TypeScript 示例使用 `@modelcontextprotocol/ext-apps/server` 的 `registerAppResource`、`registerAppTool`、`RESOURCE_MIME_TYPE`；这些是协议参考，并非必须新增 Node 后端。Python 可在真正外层实现等价资源/元数据，避免双 host、双鉴权。

### 工具与可见性

| 拟议工具 | 参数/结果 | 可见性与限制 |
| --- | --- | --- |
| `DesktopConsole` | 模型调用可传现有 `workflow_id` 与初始 target；打开只读控制台，返回公开状态摘要及组件专用会话能力。 | 入口关联资源；只模型入口可见或按需共享。配置 `openai/ui.entrypoints:[{type:"thread"}]`。全局入口如采用，须先显示无权限选择页，不暗中代选 ACTIVE workflow。 |
| `DesktopConsoleSession` | `action=status/select_target/arm/disarm/close/operation_status`；`ui_session`、target、原 request_id；返回会话状态。 | `_meta.ui.visibility:["app"]`。用户主动点击才 arm；arm 检查 write workflow，disarm/close/status 仍允许在写围栏存在时执行。 |
| `DesktopFrame` | `ui_session`、`after_frame_id`、有界视图尺寸/格式偏好；返回实际帧版本、捕获时刻、物理坐标、渲染变换、目标/显示布局版本及画面。 | app-only；不关联 UI template，避免每次轮询重建 iframe。只读轮询不得取得或续租写控制权。 |
| `DesktopAction` | `ui_session`、`request_id`、`expected_frame_id`、`target_id`、`layout_version`、有限动作及有界参数。 | app-only；同一会话只允许一项在途动作。服务端重新检查全部 scope、目标、前台/窗口锁、freshness，并返回可查询回执。 |

画面默认进入 tool result `_meta`，公开 `structuredContent` 只放精简状态/实际测量元数据。直接人控不必每次把屏幕发给模型。需要模型观察时单独调用原 Screenshot/WindowScreenshot，且应遵守当前授权范围。工具 `annotations` 只如实提示副作用，不替代内部 write 分类或鉴权。新的控制/会话写动作需要显式加入 server-owned `tool_policy`，不能被默认只读路径接纳。

UI bridge 根据官方 quickstart 顺序使用 `ui/initialize` → `ui/notifications/initialized` → `tools/call`，监听 `ui/notifications/tool-result`。宿主没有 bridge/工具调用能力时显示明确不支持状态，保留工具式操作。UI 初始化协议与当前外层 MCP legacy/modern 协议分别协商；不得把示例 UI protocolVersion 复制为本服务的 MCP 版本。可采用 SDK 客户端减少 bridge 手工维护，但须在实现前锁定实际官方包 API/版本并进行宿主验证。

### 注册元数据与打包

下面是协议字段示意，属于本文设计，不是产品实现：

```json
{
  "name": "DesktopConsole",
  "_meta": {
    "ui": { "resourceUri": "ui://unified/desktop-console/v1.html" },
    "openai/ui": { "entrypoints": [{ "type": "thread" }] }
  }
}
```

UI 私有数据/动作工具描述使用 `_meta:{"ui":{"visibility":["app"]}}`；帧/动作工具不要再次加 resourceUri，保持当前 iframe 实例。成功 opener 结果应分别返回模型可见的状态和组件专用数据：

```json
{
  "content": [{ "type": "text", "text": "操控台已打开，当前仅观察。" }],
  "structuredContent": { "ok": true, "mode": "observe" },
  "_meta": { "desktopConsole": { "uiSession": "短期会话能力", "expiresAt": "到期时间" } }
}
```

[Package your plugin](https://developers.openai.com/plugins/build/plugins) 将插件 manifest、MCP wiring 与 registered-app mapping 分开：现有私人连接继续使用其既有 app mapping/隧道关联；UI 入口是**服务器工具描述元数据**，不把整段 HTML 或密钥写入 `plugin.json`、`.app.json` 或 `.mcp.json`。如确需打包个人 Codex plugin，兼容布局由 `.codex-plugin/plugin.json` 的 `apps:"./.app.json"` 引用实际 registered MCP app mapping；portable manifest 则通过 `extensions.com.openai.apps` 引用它。实际 app ID 必须从当前已注册插件取得，本文未读取私人插件注册配置，不能从旧记忆或猜测填入。

HTML bundle 需随 portable/update 产物带入 `app/personal_mcp` 的明确资源路径，并进入现有 `manifest-sha256.json` 及完整更新/运行验收。不需要额外公网 MCP endpoint；不能为 UI 添加一个没有鉴权的本机 GET 页面以绕过 resource 注册。更新后刷新私人插件 metadata，在新会话验证资源引用/侧面板入口。仅修改源码无法更新正在运行的安装包。

### 秘密与 app-only 的边界

官方 Reference 的顶层结果 `_meta` 是模型不可见的数据通道，**不是加密保险箱或服务器权限证明**。推荐路径：

1. `DesktopConsole` 在服务器验证现有 workflow 与 owner 后，签发高熵短期 `ui_session`；服务器仅保留 capability 哈希及其映射。映射绑定内部 workflow key/ref、`credential_generation`、允许的 view/target、read/control scope、有效期及会话实例。
2. 返回 `structuredContent:{ok:true,mode:"observe",...}`，在顶层 `_meta:{desktopConsole:{uiSession:...,expiresAt:...}}` 放组件秘密。不要把该对象送入现有 `tool_result(payload)`，否则原 helper 会把它序列化给模型。
3. UI 只在组件闭包内存保存 ui_session；不要放 `window.*` 全局、DOM dataset、widgetState、localStorage/sessionStorage、URL/fragment、日志、错误追踪、资源 HTML 或 descriptor metadata。后续 UI 工具使用 ui_session，由服务端映射至原 workflow；原 workflow_id/backend.key/control.key/tunnel API key 全部留在已有服务边界。
4. 参数中的 ui_session 在转入 BF、operations、诊断前剥离；审计只记录非秘密 session ref。动作记录的核心请求、回执 owner、scope 与目标保持可审计。服务停机、workflow end/resume/generation 改变、页面关闭/失联/过期须撤销能力。
5. `_meta.ui.visibility:["app"]` 防止普通模型选用 UI 私有工具，仍必须验证短期 capability、workflow 存活与当前权限。宿主拒绝 app-only 调用时安全失败；不要为绕过宿主限制把控制工具改成 model-visible。
6. `openai/widgetSessionId` 是关联 ID，不当作 bearer 或签名。当前运行时 principal 是单个 `local-owner`；没有可信人类手势证明。`userGesture:true`、客户端 `_meta`、UI origin、隐藏按钮都不是独立鉴权。若验收要求可证明物理用户授权，需要补上可信宿主/本地配对确认；不能将当前单 owner 通道描绘成多用户隔离。

上述避免模型/全局泄漏是设计建议；**是否组件-only `_meta` 在当前宿主/隧道往返完整保存、app-only 工具能调用且不进入模型目录，必须实测**。保持组件无第三方脚本、使用受限 CSP、控制 capability 生命周期比传递长寿命 workflow 秘密更容易约束风险。

需要另外验证调用输入的隐私：官方对 result `_meta` 的隐藏说明，不能扩张成“UI 工具 arguments 永远不出现在模型/记录里”。若目标 bridge 支持，可把 capability 放在经审核的 `tools/call.params._meta` 私有命名字段，由外层 handler/context 专门提取、立即剥离并遮蔽日志；当前 RequestContext 不具备这条路径，必须实现并测试。**`params._meta` 也没有自动秘密保证**，需核对真实宿主透传/记录行为。若临时以 `arguments.ui_session` 携带能力，必须明确测试宿主 transcript/model 可见性和服务端诊断，不能把 app-only 标签当作已完成秘密保护；验证不成立时改用可信配对/宿主 broker 方案，禁止降级传递原 workflow_id。

## 操控正确性与安全闭环

- 进入控制台默认只观察。显示设备、目标、捕获时间/画面年龄、控制状态；用户点击“开始控制”后才接受写动作。切换目标、重连、恢复页面不自动恢复控制；关闭页面停止轮询/撤销或等待短期过期。
- 窗口模式优先：以 task-bound window_id 绑定 `hwnd/pid` 并 claim，默认 `allow_staging=false`、`allow_foreground_fallback=false`。最小化窗口需要额外明确选择，不可自动轮询时反复恢复/移动窗口。
- 全屏桌面模式要绑定显示布局/physical bounds/DPI；缩放后的 image/canvas 坐标须根据实际 returned transform 转换；黑边和画面外点击拒绝。窗口 surface 与 client area 未必同坐标，服务端必须提供/验证变换；不能靠 width 比例猜测。
- 新 UI 要为每帧/目标/布局分配版本，动作必须携带用户所见的版本，服务端验证仍可用/未超时。窗口关闭/重建、PID 不符、分辨率/DPI变化、目标焦点不符、新模态窗口或其他操作者造成前台变化时拒绝旧动作并要求刷新。帧号只证明映射到某次捕获，不证明应用内容此刻没有变化。
- 前台写沿用全局 DesktopLease，后台写沿用 WindowController 操作锁与 TaskStore claim；UI 控制与自动化同一目标也需串行，不可只靠 workflow 相同而跳过互斥。输入前再检查实际前台身份。锁保护插件内并发，不能阻止真实用户操作，因此检测异常后立即 disarm。
- 点击、有限文本、有限快捷键、有限滚动/拖动均应作为一次有界工具动作；不通过每个 mousemove 或 keydown 向隧道发请求。文本输入采用明确提交，禁止只因 iframe 焦点改变就向远端发送。
- 持续按键/按钮状态需要严格 key-up/button-up 清理、会话截止和取消；最小闭环优先使用完整有限动作，避免断线后远端卡键。禁止后台剪贴板自动同步和无授权凭据输入。
- 动作 request_id 首次派发后落回执；超时保留未知/处理中状态，查询原回执并观察新帧。没有确定失败证据不能重新执行。重连只重新获取画面/状态，不重放队列。关键业务行为的成功需要应用状态/回显证据，不能把“鼠标事件已发送”当作业务成功。
- 不确定 native worker 仍在运行时保持写围栏，轮询/status/disarm 可用。所有状态都有界错误与恢复入口；不通过“继续”按钮绕过 unknown fence。

## 画面大小、刷新率与验证界限

当前 screenshot backend 是单帧 PNG，捕获包括 native 启停、编码、可能的文件写入。之后还有 JSON/base64 序列化、隧道排队/上传、宿主返回及页面解码/绘制。顺序轮询的实际周期至少等于这些阶段的合计再加配置等待时间；定时器写 1000 ms 不能证明每秒一帧。

原始图像字节为 `S` 时，base64 字节约为 `4*ceil(S/3)`。只计算 payload 的例子如下，均为**数学估算，不是测试数据**，不包含协议/TLS/重试开销：

| 假设压缩后单帧 | base64 约大小 | 假设每秒 1 帧的 payload 流量 |
| --- | --- | --- |
| 100 KiB | 133.3 KiB | 1.09 Mbit/s |
| 250 KiB | 333.3 KiB | 2.73 Mbit/s |
| 500 KiB | 666.7 KiB | 5.46 Mbit/s |

刷新 2 帧/秒时上述流量约加倍，画面内容/分辨率/PNG 或 JPEG 质量都会显著改变实际 S。直接截图轮询不能达到视频编码的帧间压缩收益。已有请求上限不回答响应/隧道/宿主上限；应针对允许的画面测试响应大小，超过策略上限则缩小/降低质量或返回明确错误，不能悄悄截断 base64。

建议首次实测采用“前一请求完成后再等待可配置间隔”的单在途轮询，保守初始间隔可以是 2 秒；这是策略起点，**不是已证明刷新率**。隐藏/失焦/断线暂停；错误指数退避；新动作完成后优先刷新。应保留抓取开始/完成、编码耗时、实际 bytes、服务处理时间、UI 往返/绘制时间、丢帧/错误数和 frame age。跨端时钟未同步时不得直接相减宣称端到端单向延迟；可记录本地 RTT 与各端本地耗时。

在受控夹具中至少测试：静态文字页面、滚动、目标切换；不同窗口尺寸/DPI与多显示器；最小化拒绝、前台抢占、窗口关闭/PID变化；lost response/unknown 不重放；隐藏暂停、会话过期与 generation 撤销；大小上限/高压缩噪声画面；连续 5–10 分钟轮询确认 captures/内存/日志不会无界增长。报告实际成功率、各阶段 p50/p95、显示画面年龄及 FPS，且区分本机私有服务测试与真实 OpenAI 隧道/目标宿主测试。

## 推荐验收顺序及需要新控制端的条件

1. **资源协议**：用隔离 fake runtime 校验外层 authenticated `/mcp` 的 initialize/discover/resources/list/resources/read，固定 URI、未知 URI、无鉴权拒绝、modern header/meta、旧工具原契约与 descriptor `_meta` 保真。内层 FastMCP 单测不能替代这项。
2. **宿主加载**：当前私人插件更新目录后，通过现有隧道在目标宿主打开侧面板，确认 HTML/bridge、组件-only 元数据/app-only 调用、图片解码、CSP、close/隐藏行为真实可用。若 host 不支持入口，记录明确限制，而非以普通 JSON 截图冒充操控台。
3. **受控应用闭环**：观察夹具窗口 → 用户 arm → 单次点击/文本动作 → 回执 → 画面回显 → disarm/close。复用窗口 claim/前台 lease/operations；记录新鲜帧到具体动作与回显的证据。补齐有限滚轮、快捷键、拖动后分别验收其有界行为。
4. **性能与故障**：按上述场景测真实隧道链路，得到可发布的截图刷新范围和限制。未测之前产品文案采用“画面自动刷新/按画面操作”，避免“实时视频/低延迟”承诺。
5. **连续视频若为硬验收**：另行捕获持续帧、编码器、流式播放器、背压/重连/丢帧、端到端加密和配对授权，确认宿主是否允许对应媒体连接。可由远端主动连接 HTTPS/WebSocket relay 或采用经过验证的 WebRTC/TURN，从而不开放主机入站端口；但 relay/媒体连接是新增传输和信任边界，不能声称自动复用 Secure MCP Tunnel 的 API key/权限。

若独立控制端也位于另一台机器，它仅打开 `http://127.0.0.1` 不会访问远端主机。除 OpenAI 兼容宿主之外，本文没有找到当前隧道供任意浏览器直接连接的正式 API 保证；独立浏览器/桌面控制端必须采用另一个经过验证的认证转发途径或明确的支持接口。不能把 loopback UI 加上后称为已实现远程控制。

MVP 选择复用现有通道最少改动、鉴权与安装边界最清楚；连续视频路径可以保留在架构中，但只有用户明确选定性能验收并完成媒体传输验证后才能作出性能结论。
