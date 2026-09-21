"""A small Windows setup window; credentials are references, never displayed."""

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

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
}


class SetupWindow:
    def __init__(self, root, path, assets_root=None):
        self.root, self.path, self.assets = root, Path(path).resolve(), assets_root
        self.service, self.busy, self.closing = None, False, False
        self.events = queue.Queue()
        self.values = {name: tk.StringVar() for name in
            ("device_label", "workspace_root", "data_root", "tunnel_id", "key_source", "key", "port", "permission_mode")}
        root.title("个人开发机连接 · Unified Personal MCP")
        root.geometry("850x830")
        root.minsize(800, 750)
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
                options = ("密钥文件", "环境变量") if name == "key_source" else ("允许项目内执行", "限制执行")
                entry = ttk.Combobox(page, textvariable=self.values[name], values=options, state="readonly")
            else:
                entry = ttk.Entry(page, textvariable=self.values[name])
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            self.fields.append((entry, "readonly" if name in {"key_source", "permission_mode"} else "normal"))
            if name in {"workspace_root", "data_root", "key"}:
                button = ttk.Button(page, text="选择", command=lambda n=name: self.choose(n))
                button.grid(row=row, column=2, padx=(8, 0), pady=6)
                self.fields.append((button, "normal"))
        ttk.Label(page, text="每个任务选择项目父目录下的具体项目。私有数据目录请放在项目父目录之外。",
            wraplength=710, foreground="#596579").grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 12))
        actions = ttk.Frame(page)
        actions.grid(row=12, column=0, columnspan=3, sticky="ew")
        for name, title, callback in (("save", "保存配置", self.save), ("doctor", "检查配置", self.check),
                                     ("start", "启动本地服务", self.start), ("connect", "连接 OpenAI", self.connect),
                                     ("stop", "停止服务", self.stop)):
            button = ttk.Button(actions, text=title, command=callback)
            button.pack(side="left", padx=(0, 5))
            self.buttons[name] = button
        feedback_frame = ttk.Frame(page)
        feedback_frame.grid(row=13, column=0, columnspan=3, sticky="nsew", pady=(18, 10))
        feedback_frame.rowconfigure(0, weight=1)
        feedback_frame.columnconfigure(0, weight=1)
        self.feedback = tk.Text(feedback_frame, height=9, wrap="word", font=("Microsoft YaHei UI", 10),
            relief="solid", borderwidth=1, padx=12, pady=10, bg="white", fg="#263448", state="disabled")
        self.feedback.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(feedback_frame, orient="vertical", command=self.feedback.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.feedback.configure(yscrollcommand=scrollbar.set)
        page.rowconfigure(13, weight=1)
        ttk.Label(page, text="配置文件：" + str(self.path), wraplength=730,
                  foreground="#596579").grid(row=14, column=0, columnspan=3, sticky="w")
        self.load()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Control-s>", lambda event: self.save() if not self.busy else None)
        root.after(100, self.poll)

    def load(self):
        private = self.path.parent / "data"
        defaults = {"device_label": "我的开发机", "workspace_root": "", "data_root": str(private),
            "tunnel_id": "", "key_source": "密钥文件", "key": str(private / "tunnel.key"),
            "port": "28776", "permission_mode": "允许项目内执行"}
        if self.path.is_file():
            try:
                cfg = load_config(self.path)
                defaults.update(device_label=cfg.device_label, workspace_root=str(cfg.workspace_root),
                    data_root=str(cfg.data_root), tunnel_id=cfg.tunnel_id, port=str(cfg.port),
                    key_source="环境变量" if cfg.tunnel_key_env else "密钥文件",
                    key=cfg.tunnel_key_env or str(cfg.tunnel_key_file),
                    permission_mode="允许项目内执行" if cfg.permission_mode == "trusted" else "限制执行")
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
                "permission_mode": "trusted" if values["permission_mode"] == "允许项目内执行" else "safe"}, key_import

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
        if self.service is not None:
            self.show("请先停止服务，再修改配置。")
            return
        try:
            raw, key_import = self.raw()
        except (ValueError, OSError):
            self.show("请检查目录和端口，端口应为 1 至 65535 的整数。")
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
            return "本地服务已启动，49 个工具可用。点击“连接 OpenAI”后可从客户端使用。"
        self.submit(start, "正在启动本地服务…")

    def connect(self):
        def connect():
            self.service.connect_tunnel()
            return "OpenAI 隧道已就绪。请在客户端验证连接和工具目录。"
        self.submit(connect, "正在连接 OpenAI…")

    def stop(self):
        def stop():
            if self.service is not None:
                self.service.stop()
                self.service = None
            return "服务已停止，已释放本程序的任务资源。"
        self.submit(stop, "正在停止服务并清理任务资源…")

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
            if ok and self.closing:
                self.root.destroy()
                return
            self.closing = False
        except queue.Empty:
            pass
        active = self.service is not None
        for entry, normal in self.fields:
            entry.configure(state="disabled" if self.busy or active else normal)
        enabled = {"save": not active, "doctor": True, "start": not active,
                   "connect": active and self.service.tunnel is None, "stop": active}
        for name, button in self.buttons.items():
            button.configure(state="normal" if not self.busy and enabled[name] else "disabled")
        status = "操作进行中…" if self.busy else "尚未启动"
        if active and not self.busy:
            status = "本地服务运行中 · 49 个工具"
            try:
                saved = json.loads((self.service.config.data_root / "service-status.json").read_text())
                if saved.get("tunnel_connected"):
                    status = "OpenAI 已连接 · 49 个工具"
                if saved.get("error") or self.service.failure:
                    status = "需要处理 · 请检查私有数据目录中的运行状态"
            except (ValueError, OSError):
                status = "正在更新运行状态…"
        self.state_label.configure(text=status)
        self.root.after(150, self.poll)


def run(path, assets_root=None):
    root = tk.Tk()
    SetupWindow(root, path, assets_root)
    root.mainloop()
