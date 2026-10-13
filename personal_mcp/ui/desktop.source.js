import { App, PostMessageTransport } from "@modelcontextprotocol/ext-apps";

// 凭据、画面与输入只保存在组件闭包；不持久化或发送至模型上下文。
(() => {
  const app = new App({ name: "Unified Desktop Console", version: "0.1.14" }, {});
  const $ = (id) => document.getElementById(id);
  const canvas = $("desktop-canvas"), context = canvas.getContext("2d");
  const state = { session: null, expires: 0, ready: false, connected: false, armed: false, arming: false,
    paused: false, busy: false, pending: null, frame: null, image: null, drawing: null,
    pointer: null, gesture: null, generation: 0, refreshing: false, timer: null,
    selection: null, inventory: { displays: [], windows: [] }, latency: null,
    transfer: null, transferring: false, stopped: false, composing: false,
    maxFrameAge: 10000, refreshInterval: 2000, controlExpires: 0, pendingSince: 0, pendingFrame: null, pendingKind: "desktop" };
  let clickTimer = null, clickPoint = null, wheelTimer = null, wheel = null, controlEpoch = 0;
  const errorText = { UI_SESSION_EXPIRED: "操控会话已到期，请重新打开操控台。",
    UI_SESSION_REVOKED: "操控会话已失效，请重新打开操控台。", FRAME_STALE: "画面已过期，请刷新后再操作。",
    TARGET_CHANGED: "目标发生变化，请刷新后重新开启操控。", GEOMETRY_UNVERIFIED: "当前画面无法确定操作位置。",
    FOREGROUND_CHANGED: "当前窗口已变化，已停止操控。", WINDOW_OWNED: "该窗口正在由其他任务操作。",
    OUTCOME_UNKNOWN: "操作结果待确认，请查询原操作。", OPERATION_RUNNING: "操作仍在进行，请查询原操作。",
    REQUEST_CONFLICT: "操作标识冲突，请查询原操作。", FILE_EXISTS: "目标文件已存在，请填写新的路径。",
    FILE_TOO_LARGE: "文件超过允许大小。", REQUEST_REFUSED: "操作未被接受，请检查目标与权限。",
    DESKTOP_FRAME_STALE: "画面已过期，请刷新后再操作。", DESKTOP_NOT_ARMED: "操控已停止，请重新开启。",
    NATIVE_STALE_GEOMETRY: "窗口位置或显示布局已变化，请刷新画面。", NATIVE_FOCUS_CHANGED: "前台窗口已变化，已停止操控。",
    NATIVE_STALE_TARGET: "目标窗口已变化，请刷新画面。", NATIVE_TARGET_NOT_BOUND: "目标窗口尚未绑定，请选择应用窗口后操作。",
    NATIVE_SECURE_DESKTOP: "当前桌面不允许读取或操控。", NATIVE_GEOMETRY_UNAVAILABLE: "当前画面无法确定操作位置。",
    NATIVE_INPUT_OUTCOME_UNKNOWN: "输入可能已生效，请查询原操作并查看最新画面。",
    NOT_SUPPORTED: "当前操作不受支持。拖动只支持同一窗口内容区，不支持跨窗或标题栏。" };
  const feedback = (message, error = false) => { $("feedback-text").textContent = message; $("feedback-text").parentElement.classList.toggle("error", error); };
  function message(error) { return errorText[error?.code] || "操作未完成，请检查连接或查询操作状态。"; }
  function unknown(error) { return !error?.definitive || error?.outcome === "unknown" || error?.accepted === true || ["OUTCOME_UNKNOWN", "OPERATION_RUNNING", "BF_CALL_OUTCOME_UNKNOWN", "NATIVE_INPUT_OUTCOME_UNKNOWN"].includes(error?.code); }
  function markPending(id, kind = "desktop") { if (state.pending !== id) { state.pendingSince = Date.now(); state.pendingFrame = state.frame?.frame_id; state.pendingKind = kind; } state.pending = id; }
  function inspectedFrameAvailable() { return Boolean(state.pending && state.pendingKind === "desktop" && fresh() && state.frame.frame_id !== state.pendingFrame && timeValue(state.frame.captured_at) >= state.pendingSince); }
  function timeValue(value) { if (typeof value === "number") return value < 1e12 ? value * 1000 : value; const parsed = Date.parse(value); return Number.isFinite(parsed) ? parsed : 0; }
  function frameAge() { return state.frame ? Math.max(0, Date.now() - timeValue(state.frame.captured_at)) : Infinity; }
  function sessionAlive() { return Boolean(state.session && state.connected && (!state.expires || Date.now() < state.expires)); }
  function fresh() { return state.frame?.geometry_verified === true && state.image && frameAge() <= state.maxFrameAge && timeValue(state.frame.captured_at) <= Date.now() + 2000; }
  function canWrite() { return sessionAlive() && state.armed && fresh() && !state.busy && !state.refreshing && !state.pending && !document.hidden; }
  function updateControls() {
    const alive = sessionAlive();
    $("control-toggle").checked = state.armed || state.arming;
    $("control-toggle").disabled = !alive || (!state.armed && !state.arming && (!fresh() || state.busy || Boolean(state.pending)));
    $("control-label").textContent = state.arming ? "开启中" : state.armed ? "允许操控" : "仅查看";
    $("frame-badge").textContent = state.armed ? "允许操控" : "仅查看";
    canvas.classList.toggle("armed", canWrite());
    document.querySelectorAll("[data-write]").forEach((element) => { element.disabled = !canWrite(); });
    ["pause-refresh", "refresh-frame", "read-clipboard", "refresh-targets"].forEach((id) => { $(id).disabled = !alive; });
    ["upload-button", "download-button"].forEach((id) => { $(id).disabled = !alive || state.transferring || Boolean(state.pending) || (id === "download-button" && !app.getHostCapabilities()?.downloadFile); });
    $("pause-refresh").textContent = state.paused ? "继续刷新" : "暂停刷新";
    $("check-action").hidden = !state.pending;
    $("acknowledge-action").hidden = !state.pending || state.pendingKind !== "desktop";
    $("acknowledge-action").disabled = !inspectedFrameAvailable();
    $("reconnect").hidden = state.connected || !state.session;
    $("status-dot").className = "status-dot" + (state.connected ? " online" : state.session ? " error" : "");
    $("connection-label").textContent = state.connected ? "已连接" : state.session ? "连接中断" : "等待设备";
  }
  function clearGestures() { state.gesture = null; clearTimeout(clickTimer); clearTimeout(wheelTimer); clickPoint = wheel = null; }
  function localDisarm() { controlEpoch++; state.armed = state.arming = false; clearGestures(); updateControls(); }
  function disconnected() { state.connected = false; localDisarm(); clearTimeout(state.timer); feedback("连接中断，已停止操控。恢复后需要重新开启。", true); updateControls(); }
  async function call(name, args = {}, timeout = 15000) {
    if (!state.session || !state.ready || (state.expires && Date.now() >= state.expires)) throw { code: "UI_SESSION_EXPIRED", definitive: true, accepted: false };
    const started = performance.now();
    try {
      const result = await app.callServerTool({ name, arguments: args, _meta: { "unified/ui_session": state.session } }, { timeout });
      state.latency = performance.now() - started;
      state.connected = true;
      const payload = result.structuredContent || {};
      if (result.isError || payload.ok === false) throw Object.assign({}, payload.error || { code: "REQUEST_REFUSED" }, { definitive: true, accepted: payload.accepted === true, result });
      if (payload.armed === false) localDisarm();
      if (name === "DesktopAction" && args.action !== "disarm") state.controlExpires = Date.now() + (payload.control_idle_seconds || 30) * 1000;
      updateControls();
      return result;
    } catch (error) {
      if (!error?.definitive) disconnected();
      if (["UI_SESSION_EXPIRED", "UI_SESSION_REVOKED", "FRAME_STALE", "TARGET_CHANGED", "FOREGROUND_CHANGED"].includes(error?.code)) localDisarm();
      throw error;
    }
  }
  function currentTarget() { return { kind: $("target-kind").value, id: $("target-id").value }; }
  function chooseOptions() {
    const kind = $("target-kind").value, previous = $("target-id").value;
    const items = kind === "display" ? state.inventory.displays : state.inventory.windows;
    $("target-id").replaceChildren();
    for (const item of items) { const option = document.createElement("option"); option.value = String(item.id); option.textContent = kind === "display" ? `${item.name || "显示器"}${item.primary ? " · 主屏幕" : ""} · ${item.width} × ${item.height}` : item.title; $("target-id").append(option); }
    if (items.some((item) => String(item.id) === previous)) $("target-id").value = previous;
    if (!items.length) { const empty = document.createElement("option"); empty.value = ""; empty.textContent = kind === "display" ? "没有可用显示器" : "没有可用窗口"; $("target-id").append(empty); }
    $("target-id").disabled = !items.length;
  }
  async function inventory() {
    const previous = state.selection ? { ...state.selection } : null;
    // 列表刷新也是目标边界：先停止，避免 select 自动选中另一窗口时继承授权。
    const stopping = disarm();
    state.generation++; state.frame = state.image = null; state.pointer = null;
    clearGestures(); paint(); updateControls();
    showEmpty("正在读取画面列表", "当前仅查看，列表刷新后需要重新开启操控。");
    try {
      await stopping;
      const result = await call("DesktopView", { action: "inventory" });
      state.inventory = { displays: result.structuredContent?.displays || [], windows: result.structuredContent?.windows || [] };
      $("device-label").textContent = result.structuredContent?.device_label || "桌面操控台";
      chooseOptions();
      const changed = previous?.id && JSON.stringify(previous) !== JSON.stringify(currentTarget());
      await changeTarget();
      if (changed) feedback("原目标已关闭或变化，当前仅查看新目标。请确认画面后重新开启操控。", true);
      else if (previous?.id) feedback("画面列表已刷新，当前仅查看。需要操作时请重新开启操控。");
    } catch (error) { feedback(message(error), true); }
  }
  function showEmpty(title, detail) { $("empty-state").hidden = false; $("empty-title").textContent = title; $("empty-detail").textContent = detail; }
  function paint() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(rect.width * dpr)); canvas.height = Math.max(1, Math.round(rect.height * dpr));
    context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, rect.width, rect.height);
    if (!state.image || !state.frame) { state.drawing = null; return; }
    const scale = Math.min(rect.width / state.frame.width, rect.height / state.frame.height);
    const width = state.frame.width * scale, height = state.frame.height * scale;
    const left = (rect.width - width) / 2, top = (rect.height - height) / 2;
    context.drawImage(state.image, left, top, width, height);
    state.drawing = { left, top, width, height, scale };
    paintPointer();
  }
  function paintPointer() {
    const point = state.pointer, drawing = state.drawing;
    $("pointer-marker").hidden = !point || !drawing;
    if (!point || !drawing) return;
    $("pointer-marker").style.left = `${drawing.left + point.x * drawing.scale}px`;
    $("pointer-marker").style.top = `${drawing.top + point.y * drawing.scale}px`;
    $("pointer-position").textContent = `操作点 ${point.x}, ${point.y}`;
  }
  function pointAt(event) {
    const rect = canvas.getBoundingClientRect(), d = state.drawing;
    if (!d || !state.frame) return null;
    const x = event.clientX - rect.left - d.left, y = event.clientY - rect.top - d.top;
    if (x < 0 || y < 0 || x >= d.width || y >= d.height) return null;
    return { x: Math.floor(x / d.scale), y: Math.floor(y / d.scale) };
  }
  async function acceptFrame(frame, generation) {
    if (!frame || !Number.isInteger(frame.width) || !Number.isInteger(frame.height) || frame.width < 1 || frame.height < 1 || frame.width > 16384 || frame.height > 16384 || !frame.frame_id || typeof frame.data_url !== "string" || frame.data_url.length > 16 * 1024 * 1024 || !/^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(frame.data_url)) throw { code: "GEOMETRY_UNVERIFIED", definitive: true };
    const image = new Image(); image.src = frame.data_url; await image.decode();
    if (generation !== state.generation) return;
    if (image.naturalWidth !== frame.width || image.naturalHeight !== frame.height) throw { code: "GEOMETRY_UNVERIFIED", definitive: true };
    state.frame = { ...frame }; state.image = image;
    if (!state.pointer || state.pointer.x >= frame.width || state.pointer.y >= frame.height) state.pointer = { x: Math.floor(frame.width / 2), y: Math.floor(frame.height / 2) };
    $("empty-state").hidden = true; $("frame-badge").hidden = false;
    paint(); updateControls();
  }
  function schedule() { clearTimeout(state.timer); if (sessionAlive() && !state.paused && !document.hidden) state.timer = setTimeout(refresh, state.refreshInterval); }
  async function refresh() {
    if (state.refreshing || !state.selection?.id || document.hidden || state.gesture || clickPoint || wheel || state.busy) { schedule(); return; }
    const generation = state.generation, target = { ...state.selection };
    state.refreshing = true;
    try {
      const result = await call("DesktopView", { action: "frame", target });
      await acceptFrame(result._meta?.desktop_frame, generation);
    } catch (error) { feedback(message(error), true); if (!state.frame) showEmpty("暂时无法读取画面", "检查设备连接，再点击刷新。"); }
    finally { state.refreshing = false; schedule(); }
  }
  async function changeTarget() {
    await disarm(); state.generation++; state.selection = currentTarget(); state.frame = state.image = null; state.pointer = null; paint();
    showEmpty(state.selection.id ? "正在读取画面" : "没有可用画面", "选择显示器或打开应用窗口后刷新。"); updateControls(); await refresh();
  }
  async function disarm() {
    const wasArmed = state.armed; localDisarm();
    if (wasArmed && sessionAlive()) { const id = crypto.randomUUID();
      try { await call("DesktopAction", { action: "disarm", request_id: id }); }
      catch (error) { if (unknown(error) && !state.pending) markPending(id); updateControls(); }
    }
  }
  async function queryPending() {
    if (!state.pending) return;
    try {
      const result = await call("DesktopStatus", { request_id: state.pending });
      const payload = result.structuredContent || {}, receipt = payload.operation || payload;
      const status = receipt.state || payload.state;
      const returned = receipt.result?.structuredContent || payload.result?.structuredContent || receipt.result || payload.result || {};
      const ambiguous = status === "unknown" || returned.error?.outcome === "unknown" || (returned.accepted === true && (returned.ok === false || returned.error)) || ["OUTCOME_UNKNOWN", "NATIVE_INPUT_OUTCOME_UNKNOWN", "BF_CALL_OUTCOME_UNKNOWN"].includes(returned.error?.code);
      if (ambiguous) { localDisarm(); feedback(state.pendingKind === "file" ? "文件操作结果仍未确认，请继续查询原操作。" : "旧操作结果仍未确认。请刷新并检查画面，再选择保持停止。", true); return; }
      if (["completed", "failed"].includes(status)) {
        state.pending = null;
        const completed = receipt.result?.structuredContent || payload.result?.structuredContent || {};
        if (!state.transfer && completed.transfer_id) state.transfer = completed.transfer_id;
        await releaseTransfer();
        feedback(status === "completed" ? "操作已完成，请查看最新画面。" : "操作未完成，已停止操控。", status === "failed"); localDisarm(); await refresh();
      }
      else feedback("原操作仍待确认，已保留操作标识。", true);
    } catch (error) { feedback(message(error), true); }
    updateControls();
  }
  async function action(actionName, arguments_ = {}, frameId = state.frame?.frame_id) {
    if (!canWrite()) { feedback(state.pending ? "请先查询原操作结果。" : "请刷新画面并开启操控。", true); return false; }
    const requestId = crypto.randomUUID(); state.busy = true; updateControls();
    try {
      await call("DesktopAction", { action: actionName, request_id: requestId, frame_id: frameId, ...arguments_ });
      feedback("操作已发送，正在读取回显。"); await refreshAfterAction(); return true;
    } catch (error) {
      if (unknown(error)) markPending(requestId);
      localDisarm(); feedback(message(error), true); return false;
    } finally { state.busy = false; updateControls(); schedule(); }
  }
  async function refreshAfterAction() { state.busy = false; await refresh(); }
  $("control-toggle").addEventListener("change", async (event) => {
    if (!event.target.checked) { await disarm(); feedback("已停止后续操控，画面继续刷新。已发送的输入不会撤回。"); return; }
    if (!sessionAlive() || !fresh() || state.pending) { localDisarm(); feedback("请刷新画面后再开启操控。", true); return; }
    state.busy = true; state.arming = true; updateControls(); const id = crypto.randomUUID(), epoch = controlEpoch;
    try { const result = await call("DesktopAction", { action: "arm", request_id: id, frame_id: state.frame.frame_id });
      if (result.structuredContent?.armed === false) { localDisarm(); feedback("操控未开启，请刷新画面后重试。", true); return; }
      if (epoch !== controlEpoch || document.hidden) { await call("DesktopAction", { action: "disarm", request_id: crypto.randomUUID() }); return; }
      state.armed = true; feedback("已允许操控，请确认当前画面目标。"); }
    catch (error) { if (unknown(error)) markPending(id); localDisarm(); feedback(message(error), true); }
    finally { state.busy = false; state.arming = false; updateControls(); }
  });
  canvas.addEventListener("pointerdown", (event) => {
    if (!canWrite() || ![0, 2].includes(event.button)) return;
    const point = pointAt(event); if (!point) return;
    event.preventDefault(); canvas.focus(); state.pointer = point; paintPointer();
    state.gesture = { point, frame: state.frame.frame_id, button: event.button, pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointerup", (event) => {
    const gesture = state.gesture; state.gesture = null;
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const point = pointAt(event); if (!point || !canWrite()) return;
    state.pointer = point; paintPointer();
    const button = gesture.button === 2 ? "right" : "left";
    if (Math.hypot(event.clientX - gesture.clientX, event.clientY - gesture.clientY) > 5) { clearTimeout(clickTimer); clickPoint = null; void action("drag", { ...gesture.point, to_x: point.x, to_y: point.y, button }, gesture.frame); }
    else if (button === "right") void action("click", { ...point, button, clicks: 1 }, gesture.frame);
    else if (clickPoint && Math.hypot(point.x - clickPoint.x, point.y - clickPoint.y) < 5) { clearTimeout(clickTimer); clickPoint = null; void action("click", { ...point, button, clicks: 2 }, gesture.frame); }
    else { clearTimeout(clickTimer); clickPoint = point; clickTimer = setTimeout(() => { clickPoint = null; void action("click", { ...point, button, clicks: 1 }, gesture.frame); }, 280); }
  });
  canvas.addEventListener("pointercancel", () => { state.gesture = null; });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  canvas.addEventListener("wheel", (event) => {
    if (!canWrite()) return; const point = pointAt(event); if (!point) return; event.preventDefault();
    const unit = event.deltaMode === 1 ? 1 / 3 : event.deltaMode === 2 ? 3 : 1 / 100;
    wheel ||= { ...point, dx: 0, dy: 0, frame: state.frame.frame_id };
    wheel.dx = Math.max(-10, Math.min(10, wheel.dx + event.deltaX * unit)); wheel.dy = Math.max(-10, Math.min(10, wheel.dy + event.deltaY * unit));
    clearTimeout(wheelTimer); wheelTimer = setTimeout(() => { const item = wheel; wheel = null; const steps = (value) => value === 0 ? 0 : Math.max(-10, Math.min(10, Math.sign(value) * Math.max(1, Math.round(Math.abs(value))))); if (item) void action("scroll", { x: item.x, y: item.y, dx: steps(item.dx), dy: steps(item.dy) }, item.frame); }, 120);
  }, { passive: false });
  canvas.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Escape") { event.preventDefault(); void disarm(); return; }
    if (!state.pointer || !state.frame) return;
    const step = event.shiftKey ? 20 : 2;
    const deltas = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (deltas[event.key]) { event.preventDefault(); const [dx, dy] = deltas[event.key]; state.pointer = { x: Math.max(0, Math.min(state.frame.width - 1, state.pointer.x + dx)), y: Math.max(0, Math.min(state.frame.height - 1, state.pointer.y + dy)) }; paintPointer(); }
    else if (event.key === "Enter" && !event.repeat) { event.preventDefault(); void action("click", { ...state.pointer, button: "left", clicks: 1 }); }
  });
  $("target-kind").addEventListener("change", () => { chooseOptions(); void changeTarget(); });
  $("target-id").addEventListener("change", () => void changeTarget());
  $("refresh-targets").addEventListener("click", inventory);
  $("refresh-frame").addEventListener("click", refresh);
  $("pause-refresh").addEventListener("click", () => { state.paused = !state.paused; clearTimeout(state.timer); if (!state.paused) void refresh(); updateControls(); });
  $("check-action").addEventListener("click", queryPending);
  $("acknowledge-action").addEventListener("click", () => {
    if (!inspectedFrameAvailable()) return;
    state.pending = null; localDisarm();
    feedback("已保持停止。旧操作结果仍未确认，未重新发送输入。需要继续时请重新开启操控。");
  });
  $("reconnect").addEventListener("click", async () => { localDisarm(); await inventory(); if (state.connected) { feedback("连接已恢复，当前仅查看。"); await refresh(); } });
  document.querySelectorAll("[data-panel]").forEach((button) => button.addEventListener("click", () => {
    const opening = button.getAttribute("aria-expanded") !== "true";
    document.querySelectorAll("[data-panel]").forEach((tab) => { const selected = opening && tab === button; tab.setAttribute("aria-expanded", String(selected)); $(tab.dataset.panel).hidden = !selected; });
  }));
  $("literal-text").addEventListener("compositionstart", () => { state.composing = true; });
  $("literal-text").addEventListener("compositionend", () => { state.composing = false; });
  $("literal-form").addEventListener("submit", async (event) => { event.preventDefault(); if (state.composing) return; const text = $("literal-text").value; if (!text || text.length > 8192 || text.includes("\0")) { feedback("请填写 1–8192 个字符，不能包含空字符。", true); $("literal-text").focus(); return; } if (await action("type", { text })) $("literal-text").value = ""; });
  document.querySelectorAll("[data-keys]").forEach((button) => button.addEventListener("click", () => void action("shortcut", { keys: button.dataset.keys.split(",") })));
  $("pointer-click").addEventListener("click", () => state.pointer && void action("click", { ...state.pointer, button: "left", clicks: 1 }));
  $("pointer-right").addEventListener("click", () => state.pointer && void action("click", { ...state.pointer, button: "right", clicks: 1 }));
  $("scroll-up").addEventListener("click", () => state.pointer && void action("scroll", { ...state.pointer, dx: 0, dy: -3 }));
  $("scroll-down").addEventListener("click", () => state.pointer && void action("scroll", { ...state.pointer, dx: 0, dy: 3 }));
  $("drag-form").addEventListener("submit", (event) => { event.preventDefault(); const ids = ["drag-x", "drag-y", "drag-to-x", "drag-to-y"], values = ids.map((id) => $(id).value.trim() === "" ? NaN : Number($(id).value)); if (!values.every(Number.isInteger) || !state.frame || values.some((value, i) => value < 0 || value >= (i % 2 ? state.frame.height : state.frame.width))) { feedback("请填写画面范围内的完整坐标。", true); $(ids[0]).focus(); return; } void action("drag", { x: values[0], y: values[1], to_x: values[2], to_y: values[3], button: "left" }); });
  $("read-clipboard").addEventListener("click", async () => { try { const result = await call("DesktopView", { action: "clipboard" }); $("clipboard-text").value = result._meta?.clipboard_text ?? ""; feedback("已读取远端剪贴板。"); } catch (error) { feedback(message(error), true); } });
  $("write-clipboard").addEventListener("click", () => void action("clipboard_write", { text: $("clipboard-text").value }));
  document.addEventListener("visibilitychange", () => { if (document.hidden) { clearTimeout(state.timer); void disarm(); } else schedule(); });
  window.addEventListener("blur", () => void disarm());
  new ResizeObserver(paint).observe(canvas);
  setInterval(() => {
    const age = frameAge(); $("frame-age").textContent = Number.isFinite(age) ? `画面 ${(age / 1000).toFixed(1)} 秒前${fresh() ? "" : " · 已过期"}` : "画面 —";
    $("latency").textContent = state.latency == null ? "往返 —" : `往返 ${Math.round(state.latency)} ms`;
    $("refresh-state").textContent = !state.connected ? "未连接" : document.hidden ? "已暂停" : state.refreshing ? "读取中" : state.paused ? "已暂停" : "自动刷新";
    if (state.expires && Date.now() >= state.expires) { state.session = null; disconnected(); feedback("操控会话已到期，请重新打开操控台。", true); }
    if (state.armed && state.controlExpires && Date.now() >= state.controlExpires) { localDisarm(); feedback("30 秒未操作，已停止操控。需要操作时请重新开启。"); }
    updateControls();
  }, 250);

  // 文件收发为显式的有限流程；写请求出现未知结果时停止，不重新派发。
  const MAX_FILE = 128 * 1024 * 1024, CHUNK = 192 * 1024;
  function transferResult(result) { return { ...(result.structuredContent || {}), ...(result._meta?.transfer || {}) }; }
  function progress(done, total, label) { $("transfer-progress").value = total ? Math.round(done / total * 100) : 0; $("transfer-label").textContent = `${label} · ${(done / 1024 / 1024).toFixed(1)} / ${(total / 1024 / 1024).toFixed(1)} MB`; }
  function base64(bytes) { let text = ""; for (let i = 0; i < bytes.length; i += 8192) text += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(text); }
  function unbase64(text) { const binary = atob(text); return Uint8Array.from(binary, (char) => char.charCodeAt(0)); }
  async function digest(bytes) { return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (byte) => byte.toString(16).padStart(2, "0")).join(""); }
  async function fileCall(args, write = false) {
    const requestId = write ? crypto.randomUUID() : null;
    try { return transferResult(await call("DesktopFiles", { ...args, ...(write ? { request_id: requestId } : {}) }, 30000)); }
    catch (error) { if (write && unknown(error)) { markPending(requestId, "file"); localDisarm(); feedback("文件操作结果待确认，请查询原操作。", true); } throw error; }
  }
  async function releaseTransfer() {
    if (!state.transfer || state.pending) return;
    const id = state.transfer; state.transfer = null;
    try { await fileCall({ action: "release", transfer_id: id }, true); } catch { /* 失联时由服务器的有限过期策略清理。 */ }
  }
  async function runTransfer(work) {
    if (!sessionAlive() || state.transferring || state.pending) return;
    state.transferring = true; state.stopped = false; $("stop-transfer").hidden = false; updateControls();
    try { await work(); } catch (error) { feedback(state.stopped ? "文件收发已停止。" : state.pending ? "文件操作结果待确认，请查询原操作。" : message(error), true); }
    finally { await releaseTransfer(); state.transferring = false; $("stop-transfer").hidden = true; updateControls(); }
  }
  function ensureTransfer() { if (state.stopped || !sessionAlive()) throw { code: "UI_SESSION_EXPIRED" }; }
  $("stop-transfer").addEventListener("click", () => { state.stopped = true; feedback("正在停止文件收发，已发出的请求仍需确认。"); });
  $("upload-form").addEventListener("submit", (event) => {
    event.preventDefault(); const file = $("upload-file").files[0], path = $("upload-path").value.trim();
    if (!file || !path || file.size > MAX_FILE) { feedback("请选择不超过 128 MB 的文件，并填写远端保存路径。", true); return; }
    void runTransfer(async () => {
      progress(0, file.size, "校验文件"); const hash = await digest(await file.arrayBuffer()); ensureTransfer();
      const begin = await fileCall({ action: "upload_begin", path, size: file.size, sha256: hash }, true); state.transfer = begin.transfer_id;
      if (!state.transfer) throw { code: "REQUEST_REFUSED", definitive: true };
      const chunkSize = Math.min(CHUNK, begin.chunk_bytes || CHUNK); let offset = begin.next_offset || 0;
      while (offset < file.size) { ensureTransfer(); const bytes = new Uint8Array(await file.slice(offset, offset + chunkSize).arrayBuffer()); const sent = await fileCall({ action: "upload_chunk", transfer_id: state.transfer, offset, data_base64: base64(bytes) }, true); if (sent.next_offset !== offset + bytes.length) throw { code: "REQUEST_REFUSED", definitive: true }; offset = sent.next_offset; progress(offset, file.size, "发送中"); }
      ensureTransfer(); const committed = await fileCall({ action: "upload_commit", transfer_id: state.transfer }, true);
      if (committed.state !== "committed" || committed.sha256 !== hash || committed.size !== file.size) throw { code: "REQUEST_REFUSED", definitive: true };
      progress(file.size, file.size, "已发送"); feedback("文件已发送并校验完成。"); $("upload-file").value = "";
    });
  });
  $("download-form").addEventListener("submit", (event) => {
    event.preventDefault(); const path = $("download-path").value.trim(); if (!path) { feedback("请填写远端文件路径。", true); $("download-path").focus(); return; }
    void runTransfer(async () => {
      const begin = await fileCall({ action: "download_begin", path }, true); state.transfer = begin.transfer_id;
      if (!state.transfer || !Number.isSafeInteger(begin.size) || begin.size < 0 || begin.size > MAX_FILE) throw { code: "FILE_TOO_LARGE", definitive: true };
      let offset = 0; const chunks = [], chunkSize = Math.min(CHUNK, begin.chunk_bytes || CHUNK); progress(0, begin.size, "接收中");
      while (offset < begin.size) { ensureTransfer(); const chunk = await fileCall({ action: "download_chunk", transfer_id: state.transfer, offset, limit: chunkSize }); if (typeof chunk.data_base64 !== "string" || chunk.data_base64.length > CHUNK * 4 / 3 + 8) throw { code: "REQUEST_REFUSED", definitive: true }; const bytes = unbase64(chunk.data_base64); if (!bytes.length || chunk.offset !== offset || chunk.next_offset !== offset + bytes.length || chunk.next_offset > begin.size) throw { code: "REQUEST_REFUSED", definitive: true }; chunks.push(bytes); offset = chunk.next_offset; progress(offset, begin.size, "接收中"); }
      ensureTransfer(); const blob = new Blob(chunks, { type: "application/octet-stream" }); if (await digest(await blob.arrayBuffer()) !== begin.sha256) throw { code: "REQUEST_REFUSED", definitive: true };
      const name = (begin.name || "接收文件").replace(/[\\/]/g, "_");
      const saved = await app.downloadFile({ contents: [{ type: "resource", resource: { uri: `file:///${encodeURIComponent(name)}`, mimeType: "application/octet-stream", blob: base64(new Uint8Array(await blob.arrayBuffer())) } }] }, { timeout: 60000 });
      if (saved.isError) { progress(begin.size, begin.size, "保存已取消"); feedback("已接收并校验，保存请求未完成。", true); return; }
      progress(begin.size, begin.size, "校验完成"); feedback("文件已接收并校验，已请求保存到本机。");
    });
  });
  async function receiveConsole(result) {
    const desktop = result._meta?.desktop;
    if (!desktop?.ui_session) return;
    await disarm(); state.session = desktop.ui_session; state.expires = timeValue(desktop.expires_at); state.connected = true;
    if (Number.isFinite(desktop.max_frame_age_ms)) state.maxFrameAge = Math.max(1000, Math.min(30000, desktop.max_frame_age_ms));
    if (Number.isFinite(desktop.refresh_interval_ms)) state.refreshInterval = Math.max(1000, Math.min(10000, desktop.refresh_interval_ms));
    $("device-label").textContent = desktop.device_label || result.structuredContent?.device_label || "桌面操控台";
    if (state.ready) { await inventory(); feedback("当前仅查看。选择画面后可开启操控。"); }
  }
  app.ontoolresult = (result) => { void receiveConsole(result); };
  app.onteardown = async () => { state.stopped = true; clearTimeout(state.timer); await disarm(); await releaseTransfer(); state.session = null; return {}; };
  app.onerror = () => { disconnected(); };
  app.onclose = () => { disconnected(); };
  app.connect(new PostMessageTransport(window.parent, window.parent)).then(async () => { state.ready = true;
    if (!app.getHostCapabilities()?.serverTools) { state.ready = false; showEmpty("当前页面不支持操控台", "请在支持设备工具的会话中重新打开。"); return; }
    if (!app.getHostCapabilities()?.downloadFile) $("download-button").title = "当前页面不支持保存文件";
    if (state.session) await inventory(); else { showEmpty("等待设备画面", "请从当前工作流打开桌面操控台。"); feedback("等待操控会话。"); } }).catch(() => { showEmpty("当前页面无法连接", "请在支持操控台的会话中重新打开。"); feedback("无法连接操控台，请重新打开。", true); });
})();
