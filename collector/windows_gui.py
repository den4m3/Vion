"""Windows control panel for Vion Collector. Existing config/data remain in place."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

TITLE = 'Vion Collector'


class Tray:
    """Native Windows notification icon; callbacks are delivered to Tk via a queue."""
    def __init__(self, events):
        self.events, self.hwnd, self.thread = events, None, None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name='VionTray')
        self.thread.start()

    def run(self):
        user, shell, kernel = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        class WndClass(ctypes.Structure):
            _fields_ = [('style', W.UINT), ('proc', callback_type), ('cls', ctypes.c_int), ('wnd', ctypes.c_int),
                ('instance', W.HINSTANCE), ('icon', W.HICON), ('cursor', W.HANDLE), ('brush', W.HBRUSH),
                ('menu', W.LPCWSTR), ('name', W.LPCWSTR)]
        class Notify(ctypes.Structure):
            _fields_ = [('cbSize', W.DWORD), ('hWnd', W.HWND), ('uID', W.UINT), ('uFlags', W.UINT),
                ('uCallbackMessage', W.UINT), ('hIcon', W.HICON), ('szTip', W.WCHAR * 128),
                ('dwState', W.DWORD), ('dwStateMask', W.DWORD), ('szInfo', W.WCHAR * 256),
                ('uTimeout', W.UINT), ('szInfoTitle', W.WCHAR * 64), ('dwInfoFlags', W.DWORD),
                ('guidItem', ctypes.c_byte * 16), ('hBalloonIcon', W.HICON)]
        user.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        user.DefWindowProcW.restype = LRESULT
        user.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.HANDLE, W.HINSTANCE, W.LPVOID]
        user.CreateWindowExW.restype = W.HWND
        user.LoadIconW.argtypes = [W.HINSTANCE, W.LPCWSTR]
        user.LoadIconW.restype = W.HICON
        user.CreatePopupMenu.restype = W.HMENU
        user.AppendMenuW.argtypes = [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR]
        user.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.LPCRECT]
        user.DestroyMenu.argtypes = [W.HMENU]
        user.SetForegroundWindow.argtypes = [W.HWND]
        user.DestroyWindow.argtypes = [W.HWND]
        kernel.GetModuleHandleW.restype = W.HMODULE
        shell.Shell_NotifyIconW.argtypes = [W.DWORD, ctypes.POINTER(Notify)]
        self.notice = None
        taskbar_created = user.RegisterWindowMessageW('TaskbarCreated')
        def callback(hwnd, msg, wparam, lparam):
            if msg == taskbar_created and self.notice is not None:
                shell.Shell_NotifyIconW(0, ctypes.byref(self.notice))
                return 0
            if msg == 0x8001:
                if lparam == 0x203:
                    self.events.put(('show', None))
                elif lparam == 0x205:
                    menu = user.CreatePopupMenu()
                    for number, label in ((1, 'Открыть Vion Collector'), (2, 'Запустить'), (3, 'Остановить'), (4, 'Выход')):
                        user.AppendMenuW(menu, 0, number, label)
                    point = W.POINT()
                    user.GetCursorPos(ctypes.byref(point))
                    user.SetForegroundWindow(hwnd)
                    selected = user.TrackPopupMenu(menu, 0x100 | 0x80, point.x, point.y, 0, hwnd, None)
                    user.DestroyMenu(menu)
                    if selected:
                        self.events.put(({1: 'show', 2: 'start', 3: 'stop', 4: 'exit'}[selected], None))
                return 0
            if msg == 0x10:
                user.DestroyWindow(hwnd)
                return 0
            if msg == 2:
                if self.notice is not None:
                    shell.Shell_NotifyIconW(2, ctypes.byref(self.notice))
                user.PostQuitMessage(0)
                return 0
            return user.DefWindowProcW(hwnd, msg, wparam, lparam)
        self.callback = callback_type(callback)
        name = f'VionCollectorTray-{os.getpid()}'
        cls = WndClass()
        cls.proc, cls.instance, cls.name = self.callback, kernel.GetModuleHandleW(None), name
        user.RegisterClassW(ctypes.byref(cls))
        self.hwnd = user.CreateWindowExW(0, name, TITLE + ' tray', 0, 0, 0, 0, 0, None, None, cls.instance, None)
        if not self.hwnd:
            self.events.put(('tray_error', None))
            return
        notice = Notify()
        notice.cbSize, notice.hWnd, notice.uID = ctypes.sizeof(Notify), self.hwnd, 1
        notice.uFlags, notice.uCallbackMessage = 1 | 2 | 4, 0x8001
        notice.hIcon = user.LoadIconW(None, ctypes.cast(ctypes.c_void_p(32512), W.LPCWSTR))
        notice.szTip = TITLE + ' · by tomeeli'
        self.notice = notice
        if not shell.Shell_NotifyIconW(0, ctypes.byref(notice)):
            self.events.put(('tray_error', None))
        msg = W.MSG()
        while user.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user.TranslateMessage(ctypes.byref(msg))
            user.DispatchMessageW(ctypes.byref(msg))
        user.UnregisterClassW(name, cls.instance)
        self.hwnd = None

    def close(self):
        if self.hwnd:
            ctypes.windll.user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
            ctypes.windll.user32.PostMessageW(self.hwnd, 0x10, 0, 0)
        if self.thread:
            self.thread.join(timeout=2)


class Panel:
    def __init__(self, root, home, auto_start=True):
        self.root, self.home = root, Path(home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.config_path = self.home / 'config.json'
        self.events = queue.Queue(maxsize=1000)
        self.process, self.stopping, self.discovery_busy = None, False, False
        self.config, self.secrets, self.devices = {}, {}, {}
        self.key_labels = {}
        self.tray_ok = True
        self.config_valid = True
        root.title(TITLE)
        root.geometry('920x670')
        root.minsize(800, 560)
        root.protocol('WM_DELETE_WINDOW', self.hide)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TLabel', font=('Segoe UI', 10))
        style.configure('TButton', font=('Segoe UI', 10), padding=6)
        header = ttk.Frame(root, padding=(18, 14))
        header.pack(fill='x')
        ttk.Label(header, text='VION  /  Collector', font=('Segoe UI Semibold', 20)).pack(side='left')
        ttk.Label(header, text='by tomeeli').pack(side='right')
        controls = ttk.Frame(root, padding=(18, 0, 18, 12))
        controls.pack(fill='x')
        self.state = tk.StringVar(value='Остановлен')
        ttk.Label(controls, textvariable=self.state).pack(side='left', fill='x', expand=True)
        for label, command in (('Запустить', self.start), ('Остановить', self.stop), ('В трей', self.hide), ('Выход', self.exit)):
            ttk.Button(controls, text=label, command=command).pack(side='left', padx=4)
        tabs = ttk.Notebook(root)
        tabs.pack(fill='both', expand=True, padx=18, pady=(0, 12))
        quick, settings, devices, logs = [ttk.Frame(tabs, padding=12) for _ in range(4)]
        for frame, label in ((quick, 'Быстрый запуск'), (settings, 'Дополнительные настройки'), (devices, 'Устройства Vion'), (logs, 'Журнал')):
            tabs.add(frame, text=label)
        ttk.Label(quick, text='Первый запуск — три шага', font=('Segoe UI Semibold', 17)).pack(anchor='w', pady=12)
        ttk.Label(quick, text='1. Нажмите «Настроить в браузере»: сборщик запустится, ключ управления скопируется.\n\n2. Вставьте ключ в открывшемся окне, выберите источник и ключи обложек.\n   Нажмите «Сохранить и обновить».\n\n3. В Vion найдите внешний сборщик в локальной сети и вставьте ключ подключения.\n   Он показан в браузере отдельно от ключа управления.', wraplength=720).pack(anchor='w', pady=14)
        ttk.Button(quick, text='Настроить в браузере', command=self.open_setup).pack(anchor='w', pady=12)
        ttk.Label(quick, text='Закрытие окна оставляет сборщик в трее. Для полной остановки нажмите «Выход».\nПри обновлении сохраняйте папку data и файлы настроек.', wraplength=720).pack(anchor='w', pady=12)
        self.host = tk.StringVar(value='0.0.0.0')
        self.port = tk.StringVar(value='8790')
        self.source = tk.StringVar(value='rutor')
        self.interval = tk.StringVar(value='120')
        self.poster_provider = tk.StringVar(value='auto')
        def row(label, var, values=None):
            index = settings.grid_size()[1]
            ttk.Label(settings, text=label).grid(row=index, column=0, sticky='w', pady=4)
            field = ttk.Combobox(settings, textvariable=var, values=values, state='readonly') if values else ttk.Entry(settings, textvariable=var)
            field.grid(row=index, column=1, sticky='ew', padx=(16, 0), pady=4)
            return index
        row('Адрес прослушивания', self.host, ['0.0.0.0', '127.0.0.1'])
        row('Порт', self.port)
        row('Источник релизов', self.source, ['rutor', 'rutracker', 'kinozal', 'nnm-club', 'selezen'])
        row('Обновление, минут', self.interval)
        row('Источник обложек', self.poster_provider, ['auto', 'tmdb', 'poiskkino', 'off'])
        self.jackett_url = tk.StringVar()
        row('Адрес Jackett', self.jackett_url)
        for key, label in (('jackett_key', 'Ключ Jackett'), ('tmdb_key', 'Ключ TMDB'), ('poiskkino_key', 'Ключ ПоискКино')):
            var = tk.StringVar()
            index = row(label, var)
            settings.grid_slaves(row=index, column=1)[0].configure(show='•')
            self.secrets[key] = var
            self.key_labels[key] = ttk.Label(settings, text='Не задан')
            self.key_labels[key].grid(row=index, column=2, padx=(8, 0))
            ttk.Button(settings, text='Удалить', command=lambda k=key: self.clear_secret(k)).grid(row=index, column=3, padx=(8, 0))
        settings.columnconfigure(1, weight=1)
        actions = ttk.Frame(settings)
        actions.grid(row=settings.grid_size()[1], column=0, columnspan=4, sticky='w', pady=12)
        ttk.Button(actions, text='Сохранить настройки', command=self.save).pack(side='left', padx=(0, 8))
        ttk.Button(actions, text='Скопировать ключ подключения', command=self.copy_key).pack(side='left')
        ttk.Label(settings, text='Пустые поля ключей сохраняют прежнее значение. Удаление — только отдельной кнопкой.').grid(row=settings.grid_size()[1], column=0, columnspan=4, sticky='w')
        ttk.Label(settings, text='0.0.0.0 — доступ из домашней сети; 127.0.0.1 — только с этого компьютера.').grid(row=settings.grid_size()[1], column=0, columnspan=4, sticky='w', pady=5)
        self.find_status = tk.StringVar(value='Поиск выполняется в локальной сети, без передачи ключей.')
        ttk.Button(devices, text='Найти Vion', command=self.find).pack(anchor='w')
        ttk.Label(devices, textvariable=self.find_status).pack(anchor='w', pady=8)
        self.device_list = ttk.Treeview(devices, columns=('kind', 'address'), show='tree headings', height=12)
        self.device_list.heading('#0', text='Устройство')
        self.device_list.heading('kind', text='Служба')
        self.device_list.heading('address', text='Адрес')
        self.device_list.column('#0', width=220)
        self.device_list.column('kind', width=120)
        self.device_list.column('address', width=340)
        self.device_list.pack(fill='both', expand=True)
        ttk.Button(devices, text='Скопировать выбранный адрес', command=self.copy_device).pack(anchor='w', pady=(8, 0))
        self.logs = tk.Text(logs, wrap='word', state='disabled', font=('Consolas', 10))
        self.logs.pack(fill='both', expand=True)
        ttk.Label(root, text=str(self.home), foreground='#666666').pack(anchor='w', padx=18, pady=(0, 10))
        self.load()
        self.tray = Tray(self.events)
        self.tray.start()
        root.after(100, self.tick)
        if auto_start:
            root.after(300, self.start)

    def load(self):
        try:
            self.config = json.loads(self.config_path.read_text(encoding='utf-8-sig')) if self.config_path.exists() else {}
            self.source.set(self.config.get('source', 'rutor'))
            self.interval.set(str(self.config.get('interval_minutes', 120)))
            self.poster_provider.set(self.config.get('poster_provider', 'auto'))
            self.jackett_url.set(self.config.get('jackett_url', ''))
            ui = json.loads((self.home / 'windows-ui.json').read_text()) if (self.home / 'windows-ui.json').exists() else {}
            self.host.set(ui.get('host', '0.0.0.0'))
            self.port.set(str(ui.get('port', 8790)))
            self.key_status()
        except Exception:
            self.config_valid = False
            messagebox.showerror(TITLE, 'Не удалось прочитать настройки. Файлы не изменены. Проверьте config.json.', parent=self.root)

    def key_status(self):
        for key, label in self.key_labels.items():
            label.configure(text="Сохранён" if self.config.get(key) else "Не задан")

    def values(self):
        if not self.config_valid:
            raise ValueError("invalid existing config")
        # Browser setup can change the same file while this window is open.
        config = json.loads(self.config_path.read_text(encoding='utf-8-sig')) if self.config_path.exists() else dict(self.config)
        config.update(source=self.source.get(), interval_minutes=int(self.interval.get()), poster_provider=self.poster_provider.get(), jackett_url=self.jackett_url.get().strip())
        port = int(self.port.get())
        if config['source'] not in ('rutor', 'rutracker', 'kinozal', 'nnm-club', 'selezen') or not 15 <= config['interval_minutes'] <= 1440 or not 1024 <= port <= 65535 or self.host.get() not in ('0.0.0.0', '127.0.0.1'):
            raise ValueError()
        for key, var in self.secrets.items():
            if var.get().strip():
                config[key] = var.get().strip()
        return config, {'host': self.host.get(), 'port': port}

    def save(self, notice=True):
        try:
            from collector import atomic
            config, ui = self.values()
            atomic(self.config_path, config)
            atomic(self.home / 'windows-ui.json', ui)
            self.config = config
            self.key_status()
            for var in self.secrets.values():
                var.set('')
        except Exception:
            messagebox.showerror(TITLE, 'Проверьте порт (1024–65535), интервал (15–1440) и доступ к папке.', parent=self.root)
            return False
        if notice and self.running() and messagebox.askyesno(TITLE, 'Настройки сохранены. Перезапустить сборщик, чтобы применить их?', parent=self.root):
            self.stop(restart=True)
        elif notice:
            self.append('Настройки сохранены. Они применяются при следующем запуске.')
        return True

    def clear_secret(self, key):
        if messagebox.askyesno(TITLE, 'Удалить сохранённый ключ? Остальные настройки останутся.', parent=self.root):
            self.config.pop(key, None)
            self.secrets[key].set('')
            self.save()

    def running(self):
        return self.process is not None and self.process.poll() is None

    def open_setup(self):
        if not self.running():
            self.load()
            if not self.save(notice=False):
                return
        try:
            from collector import Collector
            from setup_web import Setup
            key = Setup(Collector(self.home / 'data', self.config_path)).key
            if not self.running():
                self.start()
            if not self.running():
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(key)
            address = 'http://127.0.0.1:' + self.port.get()
            self.append('Открытие мастера: ожидаем готовности сборщика. Ключ управления скопирован.')
            def ready():
                import time
                from urllib.request import urlopen
                for _ in range(30):
                    if not self.running():
                        break
                    try:
                        with urlopen(address + '/health', timeout=.5) as response:
                            if json.load(response).get('service') == 'vion-collector':
                                self.events.put(('setup_ready', address + '/setup'))
                                return
                    except Exception:
                        pass
                    time.sleep(.3)
                self.events.put(('setup_failed', None))
            threading.Thread(target=ready, daemon=True, name='VionSetupReady').start()
        except Exception:
            messagebox.showerror(TITLE, 'Не удалось открыть мастер. Проверьте запуск сборщика; откройте /setup по его адресу.', parent=self.root)

    def start(self):
        if self.running() or self.stopping or not self.save(notice=False):
            return
        if getattr(sys, 'frozen', False):
            executable = Path(sys.executable).with_name('VionCollector.exe')
            command = [str(executable)]
        else:
            command = [sys.executable, str(Path(__file__).with_name('collector.py'))]
        command += ['--control-stdin', '--config', str(self.config_path), '--data', str(self.home / 'data'), '--host', self.host.get(), '--port', self.port.get()]
        try:
            self.process = subprocess.Popen(command, cwd=self.home, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
            self.state.set('Работает · порт ' + self.port.get())
            threading.Thread(target=self.read_log, args=(self.process,), daemon=True).start()
        except Exception:
            self.state.set('Не запущен')
            messagebox.showerror(TITLE, 'Не удалось запустить сборщик. Проверьте наличие VionCollector.exe рядом с окном управления.', parent=self.root)

    def read_log(self, process):
        for line in process.stdout:
            try:
                self.events.put_nowait(('log', line.strip()[:2000]))
            except queue.Full:
                pass

    def stop(self, restart=False):
        if not self.running() or self.stopping:
            return
        self.stopping = True
        self.state.set('Остановка…')
        process = self.process
        def work():
            try:
                process.stdin.write("stop\n")
                process.stdin.flush()
            except Exception:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            self.events.put(('stopped', restart))
        threading.Thread(target=work, daemon=True).start()

    def find(self):
        if self.discovery_busy:
            return
        self.discovery_busy = True
        self.find_status.set('Поиск Vion в локальной сети…')
        def work():
            try:
                from discovery import discover
                self.events.put(('devices', discover()))
            except Exception:
                self.events.put(('find_error', None))
        threading.Thread(target=work, daemon=True).start()

    def copy_key(self):
        try:
            value = (self.home / 'data' / 'access-key.txt').read_text().strip()
            if not value:
                raise ValueError()
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.append('Ключ подключения скопирован. Не публикуйте его.')
        except Exception:
            messagebox.showinfo(TITLE, 'Сначала запустите сборщик: он создаст ключ подключения.', parent=self.root)

    def copy_device(self):
        selected = self.device_list.selection()
        if selected:
            value = self.device_list.item(selected[0], 'values')[1]
            self.root.clipboard_clear()
            self.root.clipboard_append(value)

    def append(self, text):
        for key in ('tmdb_key', 'poiskkino_key', 'jackett_key'):
            value = self.config.get(key, '')
            if value:
                text = text.replace(value, '***')
        self.logs.configure(state='normal')
        self.logs.insert('end', text + '\n')
        if int(self.logs.index('end-1c').split('.')[0]) > 500:
            self.logs.delete('1.0', '100.0')
        self.logs.see('end')
        self.logs.configure(state='disabled')

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def hide(self):
        if self.tray_ok:
            self.root.withdraw()
        else:
            self.root.iconify()

    def exit(self):
        if self.running() and not messagebox.askyesno(TITLE, 'Остановить сборщик и выйти? Для работы в фоне используйте «В трей».', parent=self.root):
            return
        if self.running():
            try:
                self.process.stdin.write("stop\n")
                self.process.stdin.flush()
            except Exception:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.tray.close()
        self.root.destroy()

    def tick(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'log': self.append(value)
            elif kind == 'setup_ready':
                import webbrowser
                webbrowser.open(value)
                messagebox.showinfo(TITLE, 'Ключ управления скопирован. Вставьте его в браузере.\nНе путайте его с ключом подключения Vion.', parent=self.root)
            elif kind == 'setup_failed':
                messagebox.showerror(TITLE, 'Сборщик не ответил. Проверьте журнал и свободен ли выбранный порт.', parent=self.root)
            elif kind == 'show': self.show()
            elif kind == 'start': self.start()
            elif kind == 'stop': self.stop()
            elif kind == 'exit': self.exit(); return
            elif kind == 'tray_error': self.tray_ok = False
            elif kind == 'stopped':
                self.stopping = False
                self.state.set('Остановлен')
                if value: self.start()
            elif kind == 'devices':
                self.discovery_busy = False
                self.device_list.delete(*self.device_list.get_children())
                labels = {'device': 'Vion', 'web': 'Веб-управление', 'sync': 'Главное'}
                for item in value:
                    self.device_list.insert('', 'end', text=item['name'], values=(labels.get(item['kind'], item['kind']), item.get('url', ', '.join(item['addresses']))))
                self.find_status.set('Найдено служб: ' + str(len(value)) if value else 'Не найдено. Откройте Vion в одной локальной сети и проверьте брандмауэр.')
            elif kind == 'find_error':
                self.discovery_busy = False
                self.find_status.set('Поиск недоступен. Проверьте mDNS UDP 5353 и локальную сеть.')
        if self.process is not None and not self.running() and not self.stopping:
            self.state.set('Остановлен · код ' + str(self.process.returncode))
        self.root.after(100, self.tick)


def main():
    if os.name != 'nt':
        raise SystemExit('This control panel is for Windows. Use collector.py on NAS/Linux.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent)
    parser.add_argument('--no-start', action='store_true')
    args = parser.parse_args()
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [W.LPVOID, W.BOOL, W.LPCWSTR]
    kernel.CreateMutexW.restype = W.HANDLE
    identity = hashlib.sha256(str(args.home.resolve()).lower().encode()).hexdigest()[:24]
    mutex = kernel.CreateMutexW(None, False, 'Local\\VionCollector-' + identity)
    if kernel.GetLastError() == 183:
        kernel.CloseHandle.argtypes = [W.HANDLE]
        kernel.CloseHandle(mutex)
        user = ctypes.windll.user32
        user.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
        user.FindWindowW.restype = W.HWND
        existing = user.FindWindowW(None, TITLE)
        if existing:
            user.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
            user.SetForegroundWindow.argtypes = [W.HWND]
            user.ShowWindow(existing, 9)
            user.SetForegroundWindow(existing)
        return 0
    root = tk.Tk()
    panel = Panel(root, args.home, auto_start=not args.no_start)
    try:
        root.mainloop()
    finally:
        panel.tray.close()
        if panel.running(): panel.process.terminate()
        kernel.CloseHandle.argtypes = [W.HANDLE]
        kernel.CloseHandle(mutex)
    return 0


if __name__ == '__main__':
    main()
