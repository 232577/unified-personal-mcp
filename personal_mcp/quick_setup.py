"""First-device form; advanced configuration keeps its existing owner."""

import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import __version__
from .autostart import AutostartManager, bundle_directory
from .gui import ERRORS
from .onboarding import configure_new_device, launch_background_service


class QuickSetupWindow:
    def __init__(self, root, path, assets_root=None, *, on_complete):
        self.root, self.path = root, Path(path).resolve()
        self.assets = Path(assets_root) if assets_root else Path(__file__).resolve().parents[1] / 'resources'
        self.on_complete = on_complete
        self.busy, self.disposed = False, False
        self.events = queue.Queue()
        self.tunnel, self.key, self.feedback = tk.StringVar(root), tk.StringVar(root), tk.StringVar(root)
        self.show_key = tk.BooleanVar(root, False)
        self.full_control = tk.BooleanVar(root, True)
        self.auto_login = tk.BooleanVar(root, True)
        root.title('连接这台开发机 · Unified Personal MCP')
        root.geometry('700x620')
        root.minsize(620, 560)
        style = ttk.Style(root)
        style.theme_use('vista')
        style.configure('TLabel', font=('Microsoft YaHei UI', 10))
        style.configure('TButton', font=('Microsoft YaHei UI', 10), padding=(12, 7))
        self.page = ttk.Frame(root, padding=28)
        self.page.pack(fill='both', expand=True)
        self.page.columnconfigure(0, weight=1)
        ttk.Label(self.page, text='连接这台开发机', font=('Microsoft YaHei UI', 20, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(self.page, text='填写这台设备的 OpenAI 隧道与运行 key，其余配置由程序准备。',
                  wraplength=600, foreground='#596579').grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(8, 22))
        ttk.Label(self.page, text='OpenAI 隧道 ID').grid(row=2, column=0, columnspan=2, sticky='w')
        self.tunnel_entry = ttk.Entry(self.page, textvariable=self.tunnel, font=('Segoe UI', 11))
        self.tunnel_entry.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(6, 6))
        ttk.Label(self.page, text='以 tunnel_ 开头的完整 ID', foreground='#596579').grid(
            row=4, column=0, columnspan=2, sticky='w', pady=(0, 16))
        ttk.Label(self.page, text='隧道运行 key').grid(row=5, column=0, columnspan=2, sticky='w')
        self.key_entry = ttk.Entry(self.page, textvariable=self.key, show='●', font=('Segoe UI', 11))
        self.key_entry.grid(row=6, column=0, sticky='ew', pady=(6, 6))
        show = ttk.Checkbutton(self.page, text='显示', variable=self.show_key,
                               command=lambda: self.key_entry.configure(show='' if self.show_key.get() else '●'))
        show.grid(row=6, column=1, padx=(10, 0))
        ttk.Label(self.page, text='直接粘贴运行 key；保存后仅存放在这台电脑的私有目录。',
                  wraplength=600, foreground='#596579').grid(
            row=7, column=0, columnspan=2, sticky='w', pady=(0, 20))
        control = ttk.Checkbutton(self.page, text='允许完全控制这台开发机', variable=self.full_control)
        control.grid(row=8, column=0, columnspan=2, sticky='w')
        ttk.Label(self.page, text='权限等同当前 Windows 用户；取消后仅允许在默认项目目录中执行。',
                  wraplength=600, foreground='#596579').grid(
            row=9, column=0, columnspan=2, sticky='w', pady=(4, 12))
        login = ttk.Checkbutton(self.page, text='登录 Windows 后自动连接', variable=self.auto_login)
        login.grid(row=10, column=0, columnspan=2, sticky='w')
        actions = ttk.Frame(self.page)
        actions.grid(row=11, column=0, columnspan=2, sticky='ew', pady=(22, 10))
        self.save_button = ttk.Button(actions, text='保存并连接', command=self.submit)
        self.save_button.pack(side='left')
        advanced = ttk.Button(actions, text='高级配置', command=self.advanced)
        advanced.pack(side='left', padx=(10, 0))
        ttk.Label(self.page, textvariable=self.feedback, wraplength=600, anchor='nw',
                  foreground='#263448').grid(row=12, column=0, columnspan=2, sticky='nsew')
        self.page.rowconfigure(12, weight=1)
        self.controls = [self.tunnel_entry, self.key_entry, show, control, login, self.save_button, advanced]
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Return>', lambda event: self.submit())
        self.tunnel_entry.focus_set()
        self._poll_id = root.after(100, self.poll)

    def submit(self):
        if self.busy or self.disposed:
            return
        tunnel, key = self.tunnel.get().strip(), self.key.get().strip()
        if not re.fullmatch(r'tunnel_[0-9a-f]{32}', tunnel):
            self.feedback.set('请填写完整的隧道 ID，例如 tunnel_ 后接 32 位标识。')
            self.tunnel_entry.focus_set()
            return
        if not key:
            self.feedback.set('请粘贴此隧道的运行 key。')
            self.key_entry.focus_set()
            return
        full_control, auto_login = self.full_control.get(), self.auto_login.get()
        self.key.set('')
        self.busy = True
        for control in self.controls:
            control.state(['disabled'])
        self.feedback.set('正在保存配置并准备后台连接，请稍候…')

        def work():
            try:
                config = configure_new_device(self.path, tunnel, key, full_control=full_control)
            except Exception as exc:
                messages = {'CONFIGURATION_ALREADY_EXISTS': '这台设备已有配置，请进入高级配置查看。',
                            'TUNNEL_KEY_INVALID': '运行 key 格式不正确，请重新粘贴完整密钥。'}
                self.events.put((False, messages.get(str(exc), '配置未保存，请检查连接信息和目录写入权限后重试。')))
                return
            notes = ['配置已保存。']
            if auto_login:
                try:
                    AutostartManager(self.path).enable(bundle_directory(self.assets), expected_version=__version__)
                    notes.append('已启用登录自动连接。')
                except Exception as exc:
                    notes.append('登录自动连接未启用：' + ERRORS.get(str(exc), '请在控制台重试启用。'))
            try:
                launch_background_service(config, self.assets)
                notes.append('已请求后台连接，请以上方实际运行状态为准。关闭此窗口后后台服务继续运行。')
            except Exception:
                notes.append('后台启动未完成，可在控制台检查配置后启动；当前连接状态尚未确认。')
            self.events.put((True, '\n'.join(notes)))

        threading.Thread(target=work, daemon=True, name='new-device-setup').start()

    def poll(self):
        if self.disposed:
            return
        try:
            success, message = self.events.get_nowait()
            self.busy = False
            if success:
                self.dispose()
                self.on_complete(message)
                return
            self.feedback.set(message)
            for control in self.controls:
                control.state(['!disabled'])
        except queue.Empty:
            pass
        self._poll_id = self.root.after(100, self.poll)

    def dispose(self):
        self.disposed = True
        self.key.set('')
        self.root.after_cancel(self._poll_id)
        self.root.unbind('<Return>')

    def advanced(self):
        if not self.busy:
            self.dispose()
            self.on_complete(None)

    def close(self):
        if self.busy:
            self.feedback.set('当前操作仍在进行，请等待完成后关闭。')
            return
        self.dispose()
        self.root.destroy()
