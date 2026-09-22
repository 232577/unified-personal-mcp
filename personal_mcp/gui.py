"""A small Windows setup window; credentials are references, never displayed."""

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from . import __version__
from .autostart import AutostartManager, bundle_directory
from .config import load_config
from .service import LocalService
from .settings import doctor, save_settings

ERRORS = {
    "TUNNEL_ALREADY_IN_USE": "该隧道正在被另一个服务使用。请使用独立隧道，或在维护时停止旧连接后再连接。",
    "TUNNEL_OWNERSHIP_UNCERTAIN": "无法确认现有隧道的归属，请检查当前正在运行的隧道程序。",
    "INSTANCE_ALREADY_RUNNING": "这份配置已经在运行，请使用原来的程序窗口。",
    "TUNNEL_KEY_UNAVAILABLE": "未找到运行密钥，请选择密钥文件或填写准确的环境变量名。",
    "TUNNEL_KEY_INVALID": "运行密钥格式不正确，请检查所选文件或环境变量。",
    "TUNNEL_CLIENT_HASH_MISMATCH": "隧道程序与固定版本不一致，请重新安装完整程序包。",
    "TUNNEL_READY_TIMEOUT": "隧道尚未就绪，请检查网络和隧道配置后重试。",
    "TUNNEL_START_FAILED": "隧道启动失败，请检查私有数据目录中的隧道日志。",
    "SERVICE_CLEANUP_INCOMPLETE": "部分资源尚未关闭，项目仍保持锁定。请再次点击停止服务。",
    "BACKEND_KEY_INVALID": "本地连接密钥损坏，请检查私有数据目录。",
    "PORTABLE_PACKAGE_REQUIRED": "请使用完整便携包中的程序来设置登录自启动。",
    "PACKAGE_HASH_MISMATCH": "程序包校验失败，已保留原来的自启动设置。请重新解压完整程序包。",
    "PACKAGE_INVALID": "程序包不完整，已保留原来的自启动设置。",
    "PACKAGE_VERSION_MISMATCH": "程序包版本不一致，已保留原来的自启动设置。",
    "ISOLATED_START_FAILED": "新程序的隔离启动检查未通过，已保留原来的自启动设置。",
    "AUTOSTART_TASK_NOT_OWNED": "同名启动任务不属于当前用户的本程序，未修改它。",
    "AUTOSTART_CONFIGURATION_CONFLICT": "已有启动任务使用另一份配置，未覆盖它。请使用原配置或为独立安装指定另一任务名。",
    "AUTOSTART_UPDATE_FAILED": "更新自启动失败，已恢复原来的设置。",
    "AUTOSTART_ROLLBACK_FAILED": "更新自启动及恢复旧设置均未完成，请检查私有数据目录中的自启动备份。",
    "AUTOSTART_UPDATE_CONFLICT": "启动任务已被外部修改或无法确认更新结果，未覆盖当前设置。旧设置备份保存在私有数据目录中。",
    "LOCAL_SERVICE_NOT_RUNNING": "未找到使用当前配置的运行服务，请刷新状态或启动本地服务。",
    "LOCAL_RETRY_UNAVAILABLE": "当前后台版本尚不支持本机重试入口，请在新版启用后重试。当前服务保持运行。",
    "LOCAL_CONTROL_KEY_INVALID": "本机控制凭证不可用，请检查私有数据目录。",
    "LOCAL_RETRY_FAILED": "未能确认重试结果，请稍后刷新状态。当前服务保持运行。",
}


def service_presentation(observed, *, owned):
    active = bool(observed.get("running"))
    uncertain = bool(observed.get("running_unknown"))
    tunnel = observed.get('health', {}).get('tunnel', {})
    tunnel_state = tunnel.get('status')
    version = observed.get("running_version") or "版本待确认"
    text = "尚未启动"
    if uncertain:
        text = "暂时无法确认后台服务状态"
    elif active:
        connection = "OpenAI 已连接" if observed.get("tunnel_connected") else "本地服务运行中"
        count = observed.get("tools")
        text = f"{connection} · 当前运行 {version}" + (f" · {count} 个工具" if count else "")
        if observed.get("service_error"):
            text += " · 需要检查运行状态"
        if tunnel.get('error_code'):
            text += ' · ' + ERRORS.get(tunnel['error_code'], '连接暂不可用，可重新尝试连接')
        if not owned and observed.get('retry_error'):
            text += ' · 当前版本的重试入口不可用，需启用新版'
        if not owned:
            text += "（后台服务）"
    pending = observed.get("pending_version")
    startup = "登录自启动：" + ("已启用" if observed.get("enabled") else "未启用")
    if observed.get('error') in ERRORS:
        startup += ' · ' + ERRORS[observed['error']]
    if pending:
        startup += f" · 已配置版本 {pending}"
        if observed.get("enabled") and pending != observed.get("running_version"):
            startup += "（下次登录启用）"
    return {"text": text, "startup_text": startup, "start": not active and not uncertain and not owned,
            "connect": active and not uncertain and (owned or bool(observed.get('retry_available')))
                and not observed.get('tunnel_connected') and tunnel_state not in {'healthy', 'recovering'},
            "stop": owned, "editable": not active and not uncertain and not owned}


class SetupWindow:
    def __init__(self, root, path, assets_root=None):
        self.root, self.path, self.assets = root, Path(path).resolve(), assets_root
        self.service, self.busy, self.closing = None, False, False
        self.events = queue.Queue()
        self.status_events = queue.Queue()
        self.status_inflight, self.next_status_check = False, 0.0
        self.status_factory = AutostartManager
        self.observed = {"running_unknown": True}
        self.values = {name: tk.StringVar() for name in
            ("device_label", "workspace_root", "data_root", "tunnel_id", "key_source", "key", "port", "permission_mode",
             "workflow_idle_seconds", "browser_sessions", "webview2_instances", "search_sessions")}
        root.title("个人开发机连接 · Unified Personal MCP")
        root.geometry("920x980")
        root.minsize(880, 900)
        root.configure(bg="#f5f6f8")
        style = ttk.Style(root)
        style.theme_use("vista")
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))
        page = ttk.Frame(root, padding=28)
        page.pack(fill="both", expand=True)
        page.columnconfigure(1, weight=1)
        ttk.Label(page, text="个人开发机连接", font=("Microsoft YaHei UI", 21, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(page, text="在当前设备上统一使用代码、桌面和浏览器工具。").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 18))
        self.state_label = ttk.Label(page, text="尚未启动", font=("Microsoft YaHei UI", 11, "bold"))
        self.state_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 14))
        self.fields, self.buttons = [], {}
        rows = [("设备名称", "device_label"), ("项目父目录", "workspace_root"),
                ("私有数据目录", "data_root"), ("OpenAI 隧道 ID", "tunnel_id"),
                ("密钥来源", "key_source"), ("密钥文件或变量名", "key"),
                ("本地端口", "port"), ("代码操作权限", "permission_mode")]
        for row, (label, name) in enumerate(rows, start=3):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=6)
            if name in {"key_source", "permission_mode"}:
                options = (("密钥文件", "环境变量") if name == "key_source"
                           else ("允许项目内执行", "限制执行", "完全控制本机"))
                entry = ttk.Combobox(page, textvariable=self.values[name], values=options, state="readonly")
            else:
                entry = ttk.Entry(page, textvariable=self.values[name])
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            self.fields.append((entry, "readonly" if name in {"key_source", "permission_mode"} else "normal"))
            if name in {"workspace_root", "data_root", "key"}:
                button = ttk.Button(page, text="选择", command=lambda n=name: self.choose(n))
                button.grid(row=row, column=2, padx=(8, 0), pady=6)
                self.fields.append((button, "normal"))
        ttk.Label(page, text="任务分别管理进程和浏览器。完全控制模式允许跨目录读写及调用外部工具，权限等同当前 Windows 用户。",
            wraplength=710, foreground="#596579").grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 12))
        capacity = ttk.LabelFrame(page, text="并行与会话设置", padding=(12, 6))
        capacity.grid(row=12, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        capacity.columnconfigure(1, weight=1)
        capacity.columnconfigure(3, weight=1)
        for index, (title, name, minimum, maximum) in enumerate((
            ("浏览器会话", "browser_sessions", 1, 32),
            ("WebView2 实例", "webview2_instances", 1, 16),
            ("搜索会话", "search_sessions", 1, 64),
            ("空闲释放（秒）", "workflow_idle_seconds", 300, 86400),
        )):
            row, column = divmod(index, 2)
            ttk.Label(capacity, text=title).grid(row=row, column=column * 2, sticky="w", padx=(0, 10), pady=4)
            field = ttk.Spinbox(capacity, textvariable=self.values[name], from_=minimum, to=maximum, width=10)
            field.grid(row=row, column=column * 2 + 1, sticky="ew", padx=(0, 18 if column == 0 else 0), pady=4)
            self.fields.append((field, "normal"))
        actions = ttk.Frame(page)
        actions.grid(row=13, column=0, columnspan=3, sticky="ew")
        for name, title, callback in (("save", "保存配置", self.save), ("doctor", "检查配置", self.check),
                                     ("start", "启动本地服务", self.start), ("connect", "连接 OpenAI", self.connect),
                                     ("stop", "停止服务", self.stop)):
            button = ttk.Button(actions, text=title, command=callback)
            button.pack(side="left", padx=(0, 5))
            self.buttons[name] = button
        startup = ttk.Frame(page)
        startup.grid(row=14, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        for name, title, callback in (("enable_login", "启用登录自启动", self.enable_login),
                                     ("disable_login", "取消登录自启动", self.disable_login)):
            button = ttk.Button(startup, text=title, command=callback)
            button.pack(side="left", padx=(0, 5))
            self.buttons[name] = button
        self.startup_label = ttk.Label(page, text="正在读取登录自启动状态…", wraplength=810)
        self.startup_label.grid(row=15, column=0, columnspan=3, sticky="w", pady=(8, 0))
        feedback_frame = ttk.Frame(page)
        feedback_frame.grid(row=16, column=0, columnspan=3, sticky="nsew", pady=(12, 10))
        feedback_frame.rowconfigure(0, weight=1)
        feedback_frame.columnconfigure(0, weight=1)
        self.feedback = tk.Text(feedback_frame, height=5, wrap="word", font=("Microsoft YaHei UI", 10),
            relief="solid", borderwidth=1, padx=12, pady=10, bg="white", fg="#263448", state="disabled")
        self.feedback.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(feedback_frame, orient="vertical", command=self.feedback.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.feedback.configure(yscrollcommand=scrollbar.set)
        page.rowconfigure(16, weight=1)
        ttk.Label(page, text="配置文件：" + str(self.path), wraplength=730,
                  foreground="#596579").grid(row=17, column=0, columnspan=3, sticky="w")
        self.load()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Control-s>", lambda event: self.save() if not self.busy else None)
        root.after(100, self.poll)

    def load(self):
        private = self.path.parent / "data"
        defaults = {"device_label": "我的开发机", "workspace_root": "", "data_root": str(private),
            "tunnel_id": "", "key_source": "密钥文件", "key": str(private / "tunnel.key"),
            "port": "28776", "permission_mode": "允许项目内执行",
            "workflow_idle_seconds": "1800", "browser_sessions": "8",
            "webview2_instances": "4", "search_sessions": "16"}
        if self.path.is_file():
            try:
                cfg = load_config(self.path)
                defaults.update(device_label=cfg.device_label, workspace_root=str(cfg.workspace_root),
                    data_root=str(cfg.data_root), tunnel_id=cfg.tunnel_id, port=str(cfg.port),
                    key_source="环境变量" if cfg.tunnel_key_env else "密钥文件",
                    key=cfg.tunnel_key_env or str(cfg.tunnel_key_file),
                    workflow_idle_seconds=str(cfg.workflow_idle_seconds), browser_sessions=str(cfg.browser_sessions),
                    webview2_instances=str(cfg.webview2_instances), search_sessions=str(cfg.search_sessions),
                    permission_mode={"trusted": "允许项目内执行", "safe": "限制执行",
                                     "full_control": "完全控制本机"}[cfg.permission_mode])
            except Exception:
                self.show("已有配置未通过检查，请重新填写后保存。")
        for name, value in defaults.items():
            self.values[name].set(value)

    def choose(self, name):
        if name == "key":
            if self.values["key_source"].get() != "密钥文件":
                self.show("环境变量模式请直接填写变量名，不要填写密钥内容。")
                return
            value = filedialog.askopenfilename(parent=self.root, title="选择隧道运行密钥文件")
        else:
            value = filedialog.askdirectory(parent=self.root, title="选择目录")
        if value:
            self.values[name].set(value)

    def raw(self):
        values = {name: value.get().strip() for name, value in self.values.items()}
        tunnel = {"id": values["tunnel_id"]}
        key_import = None
        if values["key_source"] == "环境变量":
            tunnel["key_env"] = values["key"]
        else:
            target = Path(values["data_root"]) / "tunnel.key"
            source = Path(values["key"])
            tunnel["key_file"] = str(target)
            if source.is_file() and source.resolve() != target.resolve():
                key_import = source
        return {"schema_version": 1, "workspace_root": values["workspace_root"],
                "data_root": values["data_root"], "device_label": values["device_label"],
                "port": int(values["port"]), "host": "127.0.0.1", "tunnel": tunnel,
                "workflow_idle_seconds": int(values["workflow_idle_seconds"]),
                "browser_sessions": int(values["browser_sessions"]),
                "webview2_instances": int(values["webview2_instances"]),
                "search_sessions": int(values["search_sessions"]),
                "permission_mode": {"允许项目内执行": "trusted", "限制执行": "safe",
                                    "完全控制本机": "full_control"}[values["permission_mode"]]}, key_import

    def show(self, text):
        self.feedback.configure(state="normal")
        self.feedback.delete("1.0", "end")
        self.feedback.insert("1.0", text)
        self.feedback.configure(state="disabled")

    def submit(self, fn, label):
        if self.busy:
            return
        self.busy = True
        self.show(label)
        def work():
            try:
                self.events.put((True, fn()))
            except Exception as exc:
                text = ERRORS.get(str(exc), "操作未完成，请检查目录、端口和配置后重试。")
                self.events.put((False, text))
        threading.Thread(target=work, daemon=True, name="personal-setup").start()

    def save(self):
        if self.service is not None or self.observed.get("running") or self.observed.get("running_unknown"):
            self.show("配置正在被服务使用，暂不能覆盖。请在服务停止后修改。")
            return
        try:
            raw, key_import = self.raw()
        except (ValueError, OSError):
            self.show("请检查目录和数值配置。端口、并行数量和空闲释放时间都应填写整数。")
            return
        def save():
            save_settings(self.path, raw, key_import=key_import)
            return "配置已保存。运行密钥保存在本机私有目录或指定环境变量中。"
        self.submit(save, "正在保存配置…")

    def check(self):
        def check():
            result = doctor(self.path, assets_root=self.assets)
            return "\n".join(("通过  " if row["ok"] else "待处理  ") + row["name"] +
                ("" if row["ok"] else "：" + ERRORS.get(row["code"], "不可用")) for row in result["checks"])
        self.submit(check, "正在检查已保存的配置…")

    def start(self):
        def start():
            self.service = LocalService(load_config(self.path), assets_root=self.assets)
            try:
                self.service.start()
            except Exception:
                if self.service.guard is None:
                    self.service = None
                raise
            count = self.service.status().get("tools", 0)
            return f"本地服务已启动，{count} 个工具可用。点击“连接 OpenAI”后可从客户端使用。"
        self.submit(start, "正在启动本地服务…")

    def connect(self):
        def connect():
            if self.service is None:
                result = self.status_factory(self.path).retry_tunnel()
                tunnel = result['tunnel']
                self.status_events.put({**self.observed, 'health': {'tunnel': tunnel},
                                        'tunnel_connected': tunnel.get('status') == 'healthy'})
                return '连接重试已提交。请以上方实际连接状态为准。'
            self.service.connect_tunnel()
            if self.service.status().get("tunnel_connected"):
                return "OpenAI 隧道已就绪。请在客户端验证连接和工具目录。"
            return "已开始连接 OpenAI。请以上方实际连接状态为准；网络恢复后会自动尝试重新连接。"
        self.submit(connect, "正在连接 OpenAI…")

    def stop(self):
        def stop():
            if self.service is not None:
                self.service.stop()
                self.service = None
            return "服务已停止，已释放本程序的任务资源。"
        self.submit(stop, "正在停止服务并清理任务资源…")

    def enable_login(self):
        def enable():
            result = self.status_factory(self.path).enable(bundle_directory(self.assets), expected_version=__version__)
            return (f"已验证并设置版本 {result['pending_version']}。下次登录 Windows 后自动连接，"
                    "当前运行中的项目保持运行。")
        self.submit(enable, "正在校验完整程序包并进行隔离启动检查；现有服务继续运行…")

    def disable_login(self):
        def disable():
            self.status_factory(self.path).disable()
            return "已取消下次登录时自动连接。当前后台服务和项目继续运行。"
        self.submit(disable, "正在取消登录自启动…")

    def refresh_status(self):
        if self.status_inflight:
            return
        self.status_inflight = True
        def refresh():
            try:
                result = self.status_factory(self.path).status()
            except (ValueError, FileNotFoundError):
                result = {"running": False, "enabled": False, "configuration_required": True}
            except Exception:
                result = {"running_unknown": True, "enabled": None, "error": "STATUS_UNAVAILABLE"}
            service = getattr(self, "service", None)
            if service is not None:
                try:
                    local = service.status()
                    result.update(local, running_version=__version__, service_error=local.get("error"))
                except Exception:
                    result.update(running_unknown=True, error="STATUS_UNAVAILABLE")
            self.status_events.put(result)
        threading.Thread(target=refresh, daemon=True, name="personal-status").start()

    def close(self):
        if self.busy:
            self.show("当前操作仍在进行，请等待完成后关闭。")
            return
        self.closing = True
        self.stop()

    def poll(self):
        try:
            ok, message = self.events.get_nowait()
            self.busy = False
            self.show(message)
            self.next_status_check = 0
            if ok and self.closing:
                self.root.destroy()
                return
            self.closing = False
        except queue.Empty:
            pass
        try:
            self.observed = self.status_events.get_nowait()
            self.status_inflight = False
            self.next_status_check = time.monotonic() + 5
        except queue.Empty:
            pass
        if not self.status_inflight and time.monotonic() >= self.next_status_check:
            self.refresh_status()
        owned = self.service is not None
        observed = dict(self.observed)
        if owned:
            observed.update(running_version=__version__)
        view = service_presentation(observed, owned=owned)
        active = not view["editable"]
        for entry, normal in self.fields:
            entry.configure(state="disabled" if self.busy or active else normal)
        enabled = {"save": not active, "doctor": True, "start": view["start"],
                   "connect": view['connect'], "stop": owned,
                   "enable_login": self.path.is_file(), "disable_login": bool(observed.get("enabled"))}
        for name, button in self.buttons.items():
            button.configure(state="normal" if not self.busy and enabled[name] else "disabled")
        self.state_label.configure(text="操作进行中… · " + view["text"] if self.busy else view["text"])
        self.startup_label.configure(text=view["startup_text"])
        self.root.after(150, self.poll)


def run(path, assets_root=None):
    root = tk.Tk()
    SetupWindow(root, path, assets_root)
    root.mainloop()
