# 桌面能力审计与定向输入修复

日期：2026-10-13。基础版本：`pyproject.toml` 0.1.13。审计对象为当前源码和本地 `.venv-host` 内 Windows-MCP 0.8.5 的实际实现；没有操作真实桌面、调用 Remote Desktop Commander/BF 设备工具、修改依赖、重启服务或发布。

## 能力矩阵

| 用户需求 | 当前状态 | 证据与限制 |
| --- | --- | --- |
| 桌面单次截图 | 已支持 | 上游 Screenshot；可选屏幕、可缩放图片；adapter 校验图片尺寸/哈希。 |
| 单窗口后台截图 | 已支持 | `window_control.py:73`、`:816`：WGC 优先，PrintWindow 兜底，PNG 写任务私有目录。最小化需允许 staging；不能声称完全不改变窗口状态。 |
| 多屏与 DPI 信息 | 已支持信息读取，映射部分支持 | DisplayInventory 返回物理边界、工作区、DPI、scale；截图支持屏幕选择与负坐标。窗口截图缺少统一像素到桌面/客户区映射。 |
| 窗口发现与绑定 | 已支持 | WindowInventory 返回任务私有不透明 ID；HWND+PID 有效性校验；实际窗口首写声明由 SQLite 约束。 |
| 窗口移动/缩放/前台切换 | 部分支持 | 上游 App 按名字或当前前台窗操作；WindowControl 没有 window_id 绑定的移动、最小化、最大化、关闭接口。 |
| 鼠标单击、右键、中键、双击 | 已支持，已修复已证实语义错误 | 全局 Click；窗口坐标 click 走命中子窗转换。此次修复右/中键、多击被吞成 Invoke，以及后台 DBLCLK 消息缺失。 |
| 拖拽 | 前台已支持 | Move(drag=true, from_loc, duration) 是一次租约内明确起点拖拽；Windows-MCP DragDrop 已有异常释放左键。WindowControl 没有后台拖拽；只能左键拖拽。 |
| 滚轮与快捷键 | 部分支持 | 全局 Scroll/Shortcut 已支持；WindowControl 只有 scroll_into_view 与显式前台 shortcut，不支持窗口绑定滚轮量。 |
| 中文、字面文本、emoji | 已增加自有保护 | 窗口前台与 legacy Type 按 UTF-16 code unit 字面发送，覆盖大括号、中文和非 BMP；legacy Type 已绕过上游不可靠的共享剪贴板长文本路径。 |
| 剪贴板 | 共享纯文本已支持 | Clipboard(get/set)；无任务私有剪贴板、多格式复制或双向同步会话。 |
| 文件传输 | 通用二进制传输缺失 | 编码工具可读文本/修改文件；BrowserUpload/Download 是浏览器页面与服务端本地文件间操作，不能代替本机与远端间通用文件传输。 |
| 后台控件操作 | 已支持有限语义 | Invoke/Value/Toggle/Select/ScrollIntoView 与标准 Win32 按钮/Edit；Tk、最小化点击与无可用模式会明确拒绝或要求显式前台退路。 |
| 稳定定位 | 部分支持 | 窗口元素缓存 runtime_id 并重新获取；legacy Snapshot 标签现绑定任务、state identity、30 秒 TTL，并在其他观察/前台动作后失效。窗口元素仍缺显式 snapshot_id/几何版本，受控画面帧由主代理另行集成。 |
| 断线恢复 | 内部 BF 连接已设计支持，当前环境未通过运行验证 | generation-bound BFExecutor、journal request_id、unknown 原操作查询；不重放已分发写操作。尚无可视化会话的输入序列、失联释放键与画面恢复。 |
| 实时画面与直接操控 | 缺失 | 当前产品 GUI 是本地配置/状态工具；目标源码中未发现桌面 MJPEG/WebSocket/WebRTC 画面会话与互动控制端。 |

## 已复现并修复

1. **右键/双击 element 被替换为单次执行。** 原 `NativeWinApi.control` 先调用 `_standard_background_action`，随后 Invoke/BM_CLICK，无视 button/clicks。隔离 Button 假对象对 right/1 和 left/2 均实际调用 `(parent=100, WM_COMMAND=273, id=1002, child=101)`。现修复位置 `window_control.py:522`：语义执行仅适用于单次左键；其他组合没有客户区坐标时明确拒绝，有坐标时走鼠标消息。
2. **后台“双击”不产生 DBLCLK。** 原消息实测为 `[512,513,514,513,514]`。现 `window_control.py:631` 按左/右/中键发送 `DOWN,UP,DBLCLK,UP`。结果仍是 dispatch accepted，不替应用证明业务结果。
3. **连续前台输入不重新检查焦点。** 假对象第一次 click 把焦点从 100 改为 200，原实现仍发第二次 click 并 accepted=true；逐字输入也会继续。现 `window_control.py:678` 每次鼠标/Unicode/shortcut 调用前检查目标前台窗；clear 的 Ctrl+A 与 Back 分开发送并分别检查。部分动作已发送时失焦抛 outcome unknown，禁止把错误解释为安全重试。
4. **SetFocus 失败被忽略。** 原假控件 SetFocus 抛 OSError 后仍输入 abc。现 `window_control.py:670` 聚焦失败立即拒绝，未发送后续文字。

新增 `tests_bf/test_window_input_safety.py` 11 个参数化回归；其中最初 10 个在修复前确切失败，新增坐标右键用例验证不会调用 Invoke。与现有前台输入、子窗命中、焦点保护、原生语义、UIA identity 和原生公共对话框回归合计 **36 passed in 0.12s**；修改文件 Ruff 检查通过。

## 审计发现与仍存在的边界

### 桌面标签跨任务复用和截图失配

原 `personal_mcp/bf/bootstrap.py` 复用一个 Desktop；上游 `desktop/service.py:268` 每次观察会改写同一个 desktop_state。`tools/input.py:23` 的 label 解析只读取这份缓存，没有 task、snapshot、age 约束。lease 只串行一调用，不能保护 A 的 Snapshot → 后续 Click 之间的整体观察链。隔离对象实证：同一个 label=0 在另一快照改写后从 `[10,20]` 变成 `[500,600]`。后续授权已增加 LegacyDesktopGuard，在同一 lease 内核对任务、快照对象身份与 30 秒 TTL，并冻结标签坐标后再转发；错误以 STALE_TARGET 返回。

Screenshot(use_ui_tree=false) 和 WaitFor 都会更新 desktop_state；截图可能清空原 UI tree，WaitFor 也可能改变编号。现 guard 在 Snapshot/Screenshot/WaitFor 开始先失效旧标签；失败 Snapshot 不复活旧缓存，前台变更同样失效。独立可视化输入和 BF connection generation 恢复应调用 `mcp._bf_desktop_guard.invalidate()`；跨组件真实帧的 window/topology epoch 仍需主代理整合，不能仅用时间戳代替这些绑定。

### 窗口图片与客户区坐标缺少合同

`WindowControl.loc` 是客户区坐标。`WindowScreenshot` WGC 与 PrintWindow 表面由后端决定，PrintWindow 使用 GetWindowRect/GetWindowDC；`include_frame` 仅兼容保留并被丢弃。CAPTURE schema (`bf_automation/tool_schemas.py:49`) 只有尺寸/路径/哈希等，没有 capture_rect、client_origin、image_to_client、DPI 或 coordinate_space；无法安全直接点击标题栏、边框与缩放后的画面。

上游全桌面图片的缩放文案只提示“乘 scale”；被截取屏幕原点或虚拟桌面负原点还需加 offset。整桌面无 display 选择时 screenshot_region=None (`desktop/service.py:124`)。不要解析自然语言做控制端坐标映射。建议每帧结构化给出原始与输出尺寸、虚拟/所选屏幕 bounds、client/window rect、DPI、映射矩阵、frame_id/geometry_revision，并在服务端接受归一化画面坐标后转换和越界检查。

原 WindowSnapshot UIA 树采集锁在图片采集前释放，两者可能来自不同界面状态。后续授权修复已把树和图像采集合并到同一窗口锁内，并在每个阶段前后重新校验 HWND/PID。应用自身仍能更新，因此这不等价于原子业务画面，后续 frame_id/geometry_revision 控制仍有必要。

### 聚焦保护仍有限

新 guard 在每个调用前校验前台窗，不能消除检查到系统注入之间的竞态；UIA helper 内部一次组合快捷键仍可能改变焦点。全局上游 Click/Type/Shortcut 原本也没有 window_id 绑定的焦点 guard。`SetFocus` 成功不等价于已证明目标编辑控件拥有键盘焦点。控制端需把输入目的窗、实际命中目标与会话归属绑定；重要文字输入优先后台 ValuePattern 回读，避免靠坐标推测业务成功。

原 `_window` 在等待 operation_lock 前校验 HWND/PID，锁等候期间目标可能销毁复用。后续授权修复已在拿到锁后、等待前台 lease 后和窗口图像/树读取后重验。仍建议在可行时记录 process creation time/窗口身份，而不是只比较 PID；同一进程内的 HWND 复用尚无法仅靠此检验识别。runtime_id 尚存但语义内容改变的元素也可能继续被操控，建议绑定 snapshot generation 及最小身份。

### 长文本粘贴错误留下共享内容

本地依赖 `windows_mcp/desktop/service.py:716` `_paste_text` 在 SendKeys(Ctrl+V) 后才恢复，没有 finally。隔离假对象将 prior='original text'，Ctrl+V 抛异常后 clipboard='new sensitive text'。无 prior 纯文本时新文本也被留住；已有图片/文件等格式不会完整恢复。后续授权已由产品自有 Type wrapper 完全绕过此路径，逐 UTF-16 发送字面文字，不读写共享剪贴板；没有修改 site-packages。Clipboard(get/set) 显式工具仍依约操作共享纯文本，这不是多格式剪贴板同步功能。

### 拖拽、窗口与画面会话

上游左键 DragDrop (`uia/core.py:506`) 已在异常中尝试 ReleaseMouse；有 from_loc 与 duration 时是一次调用内动作，不必重复构建这条路径。缺的是窗口绑定拖拽/滚轮、右/中键拖拽、长按键和 mouse-down/up 流式会话。新可视化输入若引入分离 down/up，必须有限会话租约、按键状态表、断连/超时 finally release，并避免把一个拖拽拆成可被其他任务夹入的无归属调用。

`WindowCaptureBackend._stage_minimized` 会临时 ShowWindow/移动到 (-20000,-20000)，finally 恢复，但没有返回 placement/foreground 恢复验证结果；不能称“完全后台无扰动”。应显式返回恢复是否成功与 staging 结果，不应继续在恢复不明时发送输入。

### 等待与重连边界

上游 `tools/input.py:132` element_enabled 分支与 element_exists 一样，只检查节点匹配，没有额外 enabled 判断；其 interactive tree 构建通常排除了 disabled 控件，但这不足以证明所有 provider/scroll nodes 均可作为 enabled 证据。后续授权已改由自有 wrapper 有界遍历 live UIA 的实际 IsEnabled，False 会继续等待，True 才满足；DOM 或没有可靠属性的 provider 返回 UNSUPPORTED_OPERATION。BFExecutor 有 session generation、unknown outcome fence 和不重放设计；重连复用 server/store，也会保留 Desktop label 状态，恢复时必须失效 guard 并重新获取可控画面。

当前普通 sandbox 下 tmp_path 测试默认临时目录创建失败；不涉及 tmp_path 的 BF 重连单测在 BFExecutor 初始化 generation.ready 的 20 秒等待上 TimeoutError。13 个纯隔离旧输入测试成功；上述失败不能归类为已证实产品重连 bug。最终需主代理使用确定可用的 Python runtime、项目内隔离 temp 或授权环境重跑整组。此审计未重启运行中的服务。

## 最小可靠增强顺序与验收

1. 先合入上述输入语义与失焦拒绝修复；补共享 snapshot/task 归属与自有文本输入保护。验收：两任务轮流截图、图像专用 Screenshot/WaitFor 插入、窗口关闭复用、失焦发生在第 2 次 click/第 2 个字符，全部拒绝错误目标且不重复已分发动作。
2. 新增结构化 CaptureFrame 与窗口操作合同，保留现有 MCP 工具。验收：负坐标副屏、100/150/200% 混合 DPI、屏幕拔插/分辨率变化、窗口移动/大小变化、WGC/PrintWindow fallback、非客户区点击边界，按截图画面点位映射到 fixture 命中标记；旧 frame/geometry 全拒绝。
3. 提供通用分块二进制传输：项目允许根内明确目标、大小上限、offset/session_id、每块及最终 SHA-256、临时文件原子完成、取消/恢复状态查询。BrowserUpload/Download 留作页面功能。验收：二进制/空文件/大文件、多次相同块、断线恢复、路径逃逸与覆盖冲突。
4. 在同一服务上增加经过认证的可视化会话：有界帧生产器、只保留最新帧、客户端背压、无观看者停止抓图、frame_id 绑定输入、单控制者租约、会话失联自动释放输入。先采用低帧率增量截图或 MJPEG 即可验证闭环，量测后再决定是否需 WebRTC。不要每帧启动新的 WGC 3 秒捕获或每帧持久化 PNG：现有一次 WGC 与磁盘 PNG 适用于 MCP 单次观测。
5. 运行专用隔离窗口的实机验收，逐项记录“已请求/已发送/已回读”而不是仅 accepted。覆盖拖拽取消、clipboard 外部更新、焦点抢占、画面消费变慢、断线时鼠标/修饰键按住、同 request_id 重连查询，以及输入流不自动重放。

## 后续授权的窗口稳定性与可映射截图修复

新增 `tests_bf/test_window_capture_safety.py` 18 项纯隔离回归：窗口等待锁期间变更身份、同锁树/截图、树读取时身份变更、负坐标窗口、几何变更、位图尺寸不匹配、DPI 意识不明确、WGC origin 不可证实、指定映射时 PrintWindow 失败/最小化拒绝，以及 GDI 生命周期。窗口/输入相关组合当前 **54 passed in 0.16s**，相关实现、Schema 和新测试 Ruff 通过。

新增可选接口：`WindowController.screenshot(token, window_id, *, allow_staging=True, prefer_coordinate_mapping=False)` 与 MCP `WindowScreenshot` 同名 boolean。默认保留 WGC；true 只执行 PrintWindow 且要求完整可验证几何，不成功抛 `CaptureGeometryUnavailable`，错误消息前缀 `COORDINATE_MAPPING_UNAVAILABLE`，不偷偷改用 WGC。

CAPTURE 新增 `capture_rect`、`client_origin`、`client_size`、`source_width`、`source_height`、`coordinate_space='window_surface_pixels'`、`geometry_verified`。其中 source 尺寸是未缩放的真实捕获帧；capture_rect 是该像素表面对应的绝对物理屏幕矩形，client_origin 是绝对客户区原点。仅 PrintWindow 帧的确切矩形、尺寸与前后物理几何均相符时 geometry_verified=true。没有证明 WGC 表面的 origin，因此即使宽高恰好相同，也返回 capture_rect=null、geometry_verified=false；最小化 staging 也不允许据图转换坐标。未知/DPI 非物理线程的 client 元数据返回 null，拒绝伪造物理坐标。

控制端应要求 geometry_verified=true 后按 `screen = capture_rect.origin + source_image_xy`、`client = screen - client_origin` 转换，验证客户区边界并在动作前检查当前几何是否仍匹配。UI 中缩放需先把展示像素转换成 source_image_xy。默认 WGC 图只允许观察；全桌面帧有另一条可靠原点映射链，不受此窗口图限制。

PrintWindow 的 bitmap 原本仍被 DC 选中时先删除，且 CreateCompatibleBitmap/SelectObject 异常在 try/finally 外发生。现用 ExitStack 覆盖所有成功分配的资源，先恢复原位图再删除新位图和 DC，最后释放窗口 DC，并给原生 PrintWindow 显式 HWND/HDC/UINT ctypes 类型。该顺序与微软 [SelectObject](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-selectobject) 和 [DeleteObject](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-deleteobject) 的资源规则相符；DC 原点为整个窗口左上角的依据为 [GetWindowDC](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getwindowdc)。

限制：PrintWindow 是应用参与渲染的同步原生调用，可能阻塞，且返回非零不保证其画面内容代表业务成功。不能将其作为高频无界画面请求；控制端应串行请求、有超时和背压，不在 native 操作未退出时启动替代生成或重放。依据：[PrintWindow 官方文档](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-printwindow)。

## 后续授权的 legacy 桌面工具保护

新增 `tests_personal/test_legacy_desktop_guard.py` 24 项隔离回归。三个核心问题在修改前有 11 项确切行为失败；修复后覆盖跨任务标签借用、另一观察替换、TTL、外部 state 替换、失败 Snapshot、合法标签冻结、长文本无剪贴板、字面大括号/中文/emoji、逐字失焦拒绝、caret/clear/换行/Tab/提交兼容、实际 IsEnabled 的 False→True，以及真实 FastMCP TransformedTool 输出和错误合同。额外实证上游 ordinary-string capture error 即使改变 desktop_state 也不能生成新标签，已单独完成失败→通过回归。

当前定向组合包括前两轮所有隔离窗口用例、完整 WindowControl 原用例、BF factory、App 归属和 tool contracts，结果 **127 passed in 4.91s**。新 guard、合同映射与测试 Ruff 通过。运行使用项目内全新随机临时目录，未操作真实桌面或服务。

沙箱内 async 测试超时已取得具体栈证据：Windows asyncio ProactorEventLoop 的 `_make_self_pipe` 经 `socket._fallback_socketpair` 阻塞于 accept。定向测试在已授权的沙箱外运行后正常完成。因此先前 BFExecutor 初始化 20 秒超时不能作为产品重连故障结论。

Legacy Type 在 lease 内先确定点位所属根窗口、点击并核对实际前台，再在每次字面 Unicode/按键调用前重新核对；不回显所输文字。保留原 Enter/Tab 键语义、CR 处理与 caret/clear/press_enter 行为。它证明的是前台根窗口归属，不能保证某个具体编辑控件已接收全部字符；敏感或关键字段仍应优先用窗口语义 ValuePattern 回读。默认每字符短暂异步让出执行权，避免阻塞整个 BF 事件循环，实际复杂应用输入效果仍需专用 fixture 的实机验收。

`tool_contracts.py` 已增加 CaptureGeometryUnavailable→COORDINATE_MAPPING_UNAVAILABLE 和无法核实操作→UNSUPPORTED_OPERATION；STALE_TARGET 恢复说明现在分别指向 desktop Snapshot 和 window WindowInventory/WindowSnapshot。
