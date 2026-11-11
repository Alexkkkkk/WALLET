"""Russian desktop interface using Python's bundled Tk. No network access."""
import multiprocessing as mp
import os
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from .cli import save_password
from .errors import WalletError
from .progress import Progress
from .plan import wallet_plan
from .personal_wordlist import candidates as personal_candidates, write_unique
from .runner import RecoveryEngine, check_wordlist, inspect_wallet


class Application:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.engine = None
        self.password = None
        self.auto_save_path = None
        self.busy = False
        self.closing = False
        self.cancel_event = threading.Event()
        self.started_at = None
        self.last_progress_log = 0.0
        self.total = None
        self.operation = ""
        from . import __version__
        root.title("WALLET — локальное восстановление • v" + __version__)
        width = min(960, max(640, root.winfo_screenwidth() - 50))
        height = min(720, max(480, root.winfo_screenheight() - 80))
        root.geometry("%dx%d" % (width, height))
        root.minsize(640, 540)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Hint.TLabel", foreground="#526174")
        self.wallet = tk.StringVar()
        self.words = tk.StringVar()
        self.jobs = tk.StringVar(value=str(min(8, os.cpu_count() or 1)))
        self.authorized = tk.BooleanVar()
        self.checkpoint = tk.StringVar()
        self.resume_cpu = tk.BooleanVar()
        self.status = tk.StringVar(value="Готово. Выберите копию wallet.dat и словарь возможных паролей.")
        self.elapsed_text = tk.StringVar(value="Время: 0s • активных задач: 0")
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text="WALLET", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Копия кошелька • пароль не выводится в журнал • офлайн по умолчанию",
                  style="Hint.TLabel").grid(row=0, column=1, columnspan=2, sticky="w", padx=12)
        self.inputs = []
        self._file_row(frame, 1, "Копия wallet.dat", self.wallet)
        self._file_row(frame, 2, "Словарь паролей", self.words)
        consent = ttk.Checkbutton(frame, variable=self.authorized,
            text="Кошелёк мой / есть разрешение владельца. Bitcoin Core закрыт, использую копию.")
        consent.grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
        self.inputs.append(consent)
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=4)
        for col in range(4):
            actions.columnconfigure(col, weight=1)
        self.quick_btn = ttk.Button(actions, text="Быстрый запуск…", command=self.quick_start)
        self.inspect_btn = ttk.Button(actions, text="Проверить кошелёк", command=self.inspect)
        self.words_btn = ttk.Button(actions, text="Проверить словарь", command=self.check_words)
        self.plan_btn = ttk.Button(actions, text="AI-план восстановления", command=self.make_plan)
        self.start_btn = ttk.Button(actions, text="Начать проверку паролей", command=self.start)
        self.stop_btn = ttk.Button(actions, text="Остановить", command=self.stop, state="disabled")
        self.save_btn = ttk.Button(actions, text="Сохранить пароль…", command=self.save, state="disabled")
        for col, button in enumerate((self.quick_btn, self.inspect_btn, self.words_btn, self.plan_btn)):
            button.grid(row=0, column=col, sticky="ew", padx=2, pady=2)
        self.start_btn.grid(row=1, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        self.stop_btn.grid(row=1, column=2, sticky="ew", padx=2, pady=2)
        self.save_btn.grid(row=1, column=3, sticky="ew", padx=2, pady=2)
        self.inputs.extend([self.quick_btn, self.words_btn, self.plan_btn])
        self.notebook = ttk.Notebook(frame, height=140)
        self.notebook.grid(row=6, column=0, columnspan=3, sticky="ew", pady=4)
        def scroll_tab(title):
            outer = ttk.Frame(self.notebook)
            canvas = tk.Canvas(outer, height=140, highlightthickness=0)
            scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            inner = ttk.Frame(canvas, padding=8)
            window = canvas.create_window((0, 0), window=inner, anchor="nw")
            inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
            self.notebook.add(outer, text=title)
            return inner
        cpu = scroll_tab("CPU / продолжение")
        ai = scroll_tab("AI / словари")
        hc = scroll_tab("Hashcat / GPU")
        settings = scroll_tab("Настройки / диагностика")
        for tab in (cpu, ai, hc, settings):
            tab.columnconfigure(1, weight=1)
        ttk.Label(cpu, text="Процессов CPU (1–64)").grid(row=0, column=0, sticky="w")
        spin = ttk.Spinbox(cpu, from_=1, to=64, width=6, textvariable=self.jobs)
        spin.grid(row=0, column=1, sticky="w")
        checkpoint_entry = ttk.Entry(cpu, textvariable=self.checkpoint)
        ttk.Label(cpu, text="Чекпоинт CPU (JSON)").grid(row=1, column=0, sticky="w", pady=6)
        checkpoint_entry.grid(row=1, column=1, sticky="ew", padx=8)
        checkpoint_btn = ttk.Button(cpu, text="Путь…", command=self.choose_checkpoint)
        checkpoint_btn.grid(row=1, column=2)
        resume_cpu = ttk.Checkbutton(cpu, text="Продолжить CPU из существующего чекпоинта", variable=self.resume_cpu)
        resume_cpu.grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Label(cpu, text="Новый запуск: новый JSON-путь или пустое поле. Продолжение: прежние кошелёк и словарь.",
                  style="Hint.TLabel", wraplength=700).grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
        self.inputs.extend([spin, checkpoint_entry, checkpoint_btn, resume_cpu])
        self.memory = tk.StringVar()
        self.seeds = tk.StringVar()
        self.dates = tk.StringVar()
        for row, label, var in ((0, "Воспоминания о пароле", self.memory),
                                (1, "Ключевые слова (через запятую)", self.seeds),
                                (2, "Даты (через запятую)", self.dates)):
            ttk.Label(ai, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(ai, textvariable=var)
            entry.grid(row=row, column=1, columnspan=3, sticky="ew", padx=8, pady=2)
            self.inputs.append(entry)
        self.provider = tk.StringVar(value="offline")
        ttk.Label(ai, text="Провайдер").grid(row=3, column=0, sticky="w")
        self.provider_box = ttk.Combobox(ai, textvariable=self.provider, state="readonly", width=10,
                                        values=("offline", "ollama", "openai"))
        self.provider_box.grid(row=3, column=1, sticky="w", padx=8)
        self.inputs.append(self.provider_box)
        ttk.Label(ai, text="Лимит кандидатов").grid(row=3, column=2, sticky="e")
        self.limit = tk.StringVar(value="5000")
        limit_spin = ttk.Spinbox(ai, from_=1, to=1000000, width=8, textvariable=self.limit)
        limit_spin.grid(row=3, column=3, sticky="w", padx=8)
        self.inputs.append(limit_spin)
        self.ai_btn = ttk.Button(ai, text="Создать AI-словарь…", command=self.generate_ai)
        self.ai_btn.grid(row=4, column=0, sticky="w", pady=4)
        self.personal_btn = ttk.Button(ai, text="Создать персональный словарь", command=self.generate_personal)
        self.personal_btn.grid(row=4, column=1, columnspan=3, sticky="w", pady=4)
        self.inputs.extend([self.ai_btn, self.personal_btn])
        self.hash = tk.StringVar()
        self._file_row(hc, 0, "Файл хеша (mode 11300)", self.hash)
        self.state_dir = tk.StringVar(value=".wallet-recover-hashcat")
        ttk.Label(hc, text="Рабочий каталог").grid(row=1, column=0, sticky="w")
        state_entry = ttk.Entry(hc, textvariable=self.state_dir)
        state_entry.grid(row=1, column=1, sticky="ew", padx=8)
        self.hashcat_resume = tk.BooleanVar()
        resume_check = ttk.Checkbutton(hc, variable=self.hashcat_resume, text="Продолжить сохранённую сессию")
        resume_check.grid(row=2, column=0, columnspan=3, sticky="w")
        self.hashcat_consent = tk.BooleanVar()
        consent_check = ttk.Checkbutton(hc, variable=self.hashcat_consent,
            text="Хеш извлечён из моего кошелька или с разрешения владельца")
        consent_check.grid(row=3, column=0, columnspan=3, sticky="w")
        self.hashcat_btn = ttk.Button(hc, text="Запустить Hashcat", command=self.run_hashcat_action)
        self.hashcat_btn.grid(row=4, column=0, sticky="w", pady=4)
        self.inputs.extend([state_entry, resume_check, consent_check, self.hashcat_btn])
        ttk.Label(hc, text="Hashcat устанавливается отдельно. Potfile содержит пароль: не публикуйте его.",
                  style="Hint.TLabel").grid(row=4, column=1, columnspan=2, sticky="w", padx=8)
        self.ollama_model = tk.StringVar(value="mistral")
        self.ollama_url = tk.StringVar(value="http://127.0.0.1:11434")
        self.openai_model = tk.StringVar(value="gpt-5-mini")
        self.hashcat_executable = tk.StringVar(value="hashcat")
        for row, label, var in ((0, "Модель Ollama", self.ollama_model),
                                (1, "Локальный адрес Ollama", self.ollama_url),
                                (2, "Модель OpenAI", self.openai_model),
                                (3, "Hashcat.exe / команда", self.hashcat_executable)):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(settings, textvariable=var)
            entry.grid(row=row, column=1, sticky="ew", padx=8, pady=2)
            self.inputs.append(entry)
        hc_browse = ttk.Button(settings, text="Обзор…", command=lambda: self.browse(self.hashcat_executable))
        hc_browse.grid(row=3, column=2)
        self.inputs.append(hc_browse)
        self.diagnostics_btn = ttk.Button(settings, text="Проверить компоненты", command=self.diagnostics)
        self.diagnostics_btn.grid(row=4, column=0, sticky="w", pady=4)
        self.ollama_btn = ttk.Button(settings, text="Проверить Ollama", command=self.check_ollama)
        self.ollama_btn.grid(row=4, column=1, sticky="w", pady=4)
        self.inputs.extend([self.diagnostics_btn, self.ollama_btn])
        ttk.Label(settings, text="OpenAI: ключ из OPENAI_API_KEY; запросы только после согласия. Ничего не скачивается автоматически.",
                  style="Hint.TLabel", wraplength=700).grid(row=5, column=0, columnspan=3, sticky="w")
        monitor = ttk.LabelFrame(frame, text="Что программа делает сейчас", padding=8)
        monitor.grid(row=5, column=0, columnspan=3, sticky="ew", pady=4)
        monitor.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(monitor, textvariable=self.status, wraplength=850)
        self.status_label.grid(row=0, column=0, columnspan=3, sticky="ew")
        root.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(400, root.winfo_width()-60)) if e.widget is root else None)
        self.bar = ttk.Progressbar(monitor, mode="indeterminate")
        self.bar.grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(monitor, textvariable=self.elapsed_text, style="Hint.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Button(monitor, text="Сохранить журнал…", command=self.export_log).grid(row=2, column=1, padx=4)
        ttk.Button(monitor, text="Очистить журнал", command=self.clear_log).grid(row=2, column=2)
        logframe = ttk.Frame(frame)
        logframe.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self.log = tk.Text(logframe, wrap="word", height=6, state="disabled",
                           font=("Consolas", 10), background="#101c2c", foreground="#dce7f5")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(logframe, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)
        self.append("Готово. Одна строка словаря = один возможный пароль. BUILD-DEPENDENCIES.txt — не словарь.")
        root.bind("<Configure>", self.resize_layout, add="+")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def resize_layout(self, event):
        if event.widget is self.root:
            height = max(65, min(120, self.root.winfo_height() - 530))
            self.notebook.configure(height=height)

    def _file_row(self, frame, row, label, variable):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=8)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=8)
        button = ttk.Button(frame, text="Обзор…", command=lambda: self.browse(variable))
        button.grid(row=row, column=2, padx=(8, 0))
        self.inputs.extend([entry, button])

    def choose_checkpoint(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="Чекпоинт CPU (JSON)",
                                          defaultextension=".json", confirmoverwrite=False)
        if path:
            self.checkpoint.set(path)

    def browse(self, variable):
        path = filedialog.askopenfilename(parent=self.root, title="Выберите локальный файл")
        if path:
            variable.set(path)

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("[%H:%M:%S] ") + str(text) + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 1500:
            self.log.delete("1.0", "%d.0" % (lines - 1500))
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def export_log(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="Новый файл журнала (без паролей)",
                                           initialfile="wallet-log.txt", defaultextension=".txt", confirmoverwrite=False)
        if not path:
            return
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(self.log.get("1.0", "end-1c") + "\n")
            self.append("Журнал сохранён. Перед передачей проверьте личные пути к файлам.")
        except OSError as exc:
            messagebox.showerror("Журнал", str(exc), parent=self.root)

    def begin(self, message, recovery=False):
        self.cancel_event = threading.Event()
        self.started_at = time.monotonic()
        self.last_progress_log = 0.0
        self.total = None
        self.operation = message
        self.bar.stop()
        self.bar.configure(value=0)
        self.set_busy(True, recovery=recovery)
        self.status.set(message)
        self.append(message)

    def valid_wordlist(self, path, wallet=None):
        name = Path(path).name.lower()
        service_files = {"build-dependencies.txt", "requirements.txt", "readme.md", "readme-windows.txt", "license"}
        reason = None
        if name in service_files:
            reason = "Выбран служебный файл приложения, а не словарь паролей."
        elif Path(path).suffix.lower() in (".exe", ".dll", ".zip", ".dat", ".json", ".whl"):
            reason = "Этот тип файла не подходит для текстового словаря паролей."
        elif wallet and os.path.isfile(path) and os.path.isfile(wallet) and os.path.samefile(path, wallet):
            reason = "Кошелёк нельзя использовать как словарь."
        if reason:
            self.append(reason)
            self.status.set("Нужен словарь: одна строка = один возможный пароль.")
            messagebox.showerror("Неверный словарь", reason + "\nСоздайте TXT: одна строка = один возможный пароль, "
                                 "или используйте вкладку «AI / словари».", parent=self.root)
            return False
        return True

    def count_words(self, path):
        from .wordlist import iter_lines
        total = empty = 0
        for line in iter_lines(path):
            if self.cancel_event.is_set():
                raise InterruptedError("Подсчёт словаря остановлен")
            total += 1
            empty += line == b""
            if total % 10000 == 0:
                self.sink("stage", "Подсчёт словаря: прочитано %d строк…" % total)
        if not total:
            raise ValueError("Словарь пуст")
        return total, (["Пустых кандидатов: %d" % empty] if empty else [])

    def diagnostics(self):
        if self.busy:
            return
        binary = self.hashcat_executable.get().strip() or "hashcat"
        self.begin("Проверка установленных компонентов…")
        def task():
            import importlib
            import shutil
            import sys
            self.sink("log", "Python: " + sys.version.split()[0] + "; CPU: %d" % (os.cpu_count() or 1))
            for module in ("tkinter", "berkeleydb.db", "Crypto.Cipher.AES", "cryptography", "numpy", "openai"):
                try:
                    loaded = importlib.import_module(module)
                    detail = " BDB " + ".".join(map(str, loaded.version())) if module == "berkeleydb.db" else ""
                    self.sink("log", module + ": доступен" + detail)
                except Exception:
                    self.sink("log", module + ": недоступен")
            self.sink("log", "Hashcat: " + ("найден" if shutil.which(binary) else "не найден; задайте путь к hashcat.exe"))
            self.sink("log", "OpenAI: " + ("ключ задан в окружении (значение скрыто)" if os.environ.get("OPENAI_API_KEY") else "нет OPENAI_API_KEY; offline работает без ключа"))
            self.sink("task_done", "Диагностика завершена. Подробности в журнале.")
        threading.Thread(target=task, daemon=True).start()

    def local_ollama_url(self):
        from urllib.parse import urlparse
        url = self.ollama_url.get().strip().rstrip("/")
        parsed = urlparse(url)
        if (parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError("Ollama: допустим только локальный http(s) адрес localhost/127.0.0.1/::1 с портом.")
        return url

    def check_ollama(self):
        if self.busy:
            return
        try:
            url = self.local_ollama_url()
        except ValueError as exc:
            messagebox.showerror("Ollama", str(exc), parent=self.root)
            return
        model = self.ollama_model.get().strip()
        self.begin("Проверка локального Ollama (таймаут 5 секунд)…")
        def task():
            import json
            import urllib.request
            try:
                with urllib.request.urlopen(url + "/api/tags", timeout=5) as response:
                    names = [str(item.get("name", "")) for item in json.loads(response.read(1024*1024)).get("models", [])]
                available = model in names or model + ":latest" in names
                self.sink("log", "Ollama доступен; установлено моделей: %d; выбранная модель: %s" %
                          (len(names), "доступна" if available else "не найдена — установите её самостоятельно"))
                self.sink("task_done", "Ollama проверен: " + ("модель доступна" if available else "нужна установленная модель"))
            except Exception:
                self.sink("failed", "Ollama недоступен. Запустите локальный Ollama и проверьте адрес/порт; автоматической установки нет.")
        threading.Thread(target=task, daemon=True).start()

    def quick_start(self):
        if self.busy:
            return
        """Run the safe one-window flow with an explicit candidate list."""
        wallet = filedialog.askopenfilename(
            parent=self.root, title="Выберите закрытую копию wallet.dat",
            filetypes=(("Wallet backup", "*.dat"), ("All files", "*.*")))
        if not wallet:
            return
        words = filedialog.askopenfilename(
            parent=self.root, title="Выберите словарь возможных паролей",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")))
        if not words:
            messagebox.showinfo(
                "Нужен словарь",
                "Одного wallet.dat недостаточно для восстановления неизвестного пароля. "
                "Подготовьте список возможных паролей или создайте его из своих воспоминаний.",
                parent=self.root)
            return
        self.wallet.set(wallet)
        self.words.set(words)
        if not self.authorized.get() and not messagebox.askokcancel(
                "Разрешение владельца", "Кошелёк ваш или есть разрешение владельца? Bitcoin Core закрыт и выбрана копия?", parent=self.root):
            return
        self.authorized.set(True)
        self.resume_cpu.set(False)
        self.checkpoint.set(wallet + ".recovery.json")
        self.auto_save_path = wallet + ".recovered-password.bin"
        self.append("Быстрый запуск: исходник не изменяется; результат будет создан отдельно:")
        self.append(self.auto_save_path)
        self.start()

    def sink(self, event, *args):
        self.events.put((event, args))

    def set_busy(self, value, recovery=False):
        self.busy = value
        for control in self.inputs + [self.start_btn, self.inspect_btn]:
            control.configure(state="disabled" if value else "normal")
        self.stop_btn.configure(state="normal" if value and recovery else "disabled")
        self.save_btn.configure(state="normal" if not value and self.password is not None else "disabled")
        if not value:
            self.provider_box.configure(state="readonly")
            self.engine = None
            self.auto_save_path = None
        if value:
            self.bar.configure(mode="indeterminate")
            self.bar.start(12)
        else:
            self.bar.stop()

    def inspect(self):
        if self.busy:
            return
        path = self.wallet.get().strip()
        if not path:
            messagebox.showerror("Нет файла", "Выберите резервную копию wallet.dat.", parent=self.root)
            return
        self.begin("Чтение изолированной копии кошелька…")
        def task():
            try:
                self.sink("inspection", inspect_wallet(path))
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def make_plan(self):
        if self.busy:
            return
        """Copilot: диагностика + бенчмарк + фазы атаки из общего модуля plan."""
        path = self.wallet.get().strip()
        if not path:
            messagebox.showerror("Нет файла", "Выберите резервную копию wallet.dat.", parent=self.root)
            return
        try:
            jobs = int(self.jobs.get())
            if not 1 <= jobs <= 64:
                raise ValueError
        except ValueError:
            messagebox.showerror("Процессы", "Число процессов — целое число.", parent=self.root)
            return
        self.begin("Локальная диагностика и измерение скорости CPU…")
        def task():
            try:
                plan = wallet_plan(path, jobs=jobs)
                self.sink("plan_done", plan.summary(), plan.report, plan.speed_ps)
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def start(self):
        if self.busy:
            return
        if not self.authorized.get():
            messagebox.showwarning("Подтверждение", "Подтвердите право доступа и использование закрытой копии.", parent=self.root)
            return
        wallet, words = self.wallet.get().strip(), self.words.get().strip()
        if not os.path.isfile(wallet) or not os.path.isfile(words):
            messagebox.showerror("Файлы", "Выберите существующие файлы кошелька и словаря.", parent=self.root)
            return
        if not self.valid_wordlist(words, wallet):
            return
        try:
            engine = RecoveryEngine(wallet, words, int(self.jobs.get()), self.sink,
                                    checkpoint_path=self.checkpoint.get().strip() or None,
                                    resume=self.resume_cpu.get())
        except (WalletError, ValueError) as exc:
            messagebox.showerror("Параметры", str(exc), parent=self.root)
            return
        self.password = None
        self.total = None
        self.engine = engine
        self.begin("Подготовка CPU: подсчёт словаря и проверка входных файлов…", recovery=True)
        self.append("CPU-процессов: %d; режим: %s" % (engine.jobs, "продолжение" if engine.resume else "новый запуск"))
        self.append("Чекпоинт: " + ("включён" if engine.checkpoint_path else "не задан; продолжение после закрытия недоступно"))
        def prepare():
            try:
                total, warnings = self.count_words(words)
            except InterruptedError:
                self.sink("cancelled")
                return
            except Exception as exc:
                self.sink("failed", str(exc))
                return
            if engine.stopped():
                self.sink("finished", 0, 0.0, True)
                return
            self.sink("total", total)
            for warning in warnings:
                self.sink("log", warning)
            engine.start()
        threading.Thread(target=prepare, daemon=True).start()

    def stop(self):
        if not self.busy:
            return
        self.cancel_event.set()
        if self.engine:
            self.engine.stop()
        self.status.set("Остановка запрошена. Ожидаю безопасного завершения текущего этапа…")
        self.append("Остановка запрошена; незавершённая работа не считается выполненной.")
        self.stop_btn.configure(state="disabled")

    def poll(self):
        try:
            while True:
                event, args = self.events.get_nowait()
                if event == "stage":
                    self.status.set(args[0])
                    self.append(args[0])
                elif event == "hashcat_status":
                    status = args[0]
                    message = status.get("message")
                    if message:
                        self.status.set(message)
                        if status.get("event") in ("stage", "heartbeat", "finished"):
                            self.append(message)
                    if status.get("event") == "status":
                        done, total = status.get("done"), status.get("total")
                        if isinstance(done, (int, float)) and isinstance(total, (int, float)) and total > 0:
                            self.bar.stop()
                            self.bar.configure(mode="determinate", maximum=total, value=min(done, total))
                            self.status.set("Hashcat: выполнено %.0f из %.0f" % (done, total))
                        if status.get("rate"):
                            self.status.set(self.status.get() + " • %.1f H/s" % status["rate"])
                    if status.get("event") == "finished":
                        self.append(status.get("message", "Hashcat завершён"))
                        self.status.set(status.get("message", "Hashcat завершён"))
                        self.set_busy(False)
                elif event == "task_done":
                    self.status.set(args[0])
                    self.append(args[0])
                    self.set_busy(False)
                elif event == "cancelled":
                    self.status.set("Остановлено. Незавершённый словарь не сохраняется.")
                    self.append(self.status.get())
                    self.set_busy(False)
                elif event == "log":
                    self.append(args[0])
                elif event == "progress":
                    count, rate = args[:2]
                    if self.total:
                        self.bar.configure(value=count)
                    eta = args[2] if len(args) > 2 else None
                    if self.total:
                        self.status.set("Проверено: %d из %d (%.1f%%) • %.1f кандидатов/с" %
                                        (count, self.total, 100.0 * count / self.total, rate))
                    else:
                        self.status.set("Проверено: %d • Скорость: %.1f кандидатов/с" % (count, rate))
                    if eta is not None:
                        self.status.set(self.status.get() + " • ETA " + Progress._fmt(eta))
                    if time.monotonic() - self.last_progress_log >= 5:
                        self.append(self.status.get())
                        self.last_progress_log = time.monotonic()
                elif event == "inspection":
                    info = args[0]
                    self.append("Записей mkey: %d; ckey: %d" % (len(info["masters"]), info["keys"]))
                    for m in info["masters"]:
                        self.append("mkey %d: method=%d, итераций=%d" % (m["id"], m["method"], m["iterations"]))
                    for issue in info["problems"]:
                        self.append(issue)
                    self.status.set("Кошелёк пригоден для проверки" if info["usable"] else "Кошелёк не поддерживается или неполный")
                    self.set_busy(False)
                elif event == "plan_done":
                    summary, report, speed = args
                    for line in (summary.splitlines() + ["Измеренная скорость: %.1f паролей/с (1 ядро)" % speed,
                                                          "Полный план сохранён в журнале ниже."]):
                        self.append(line)
                    self.append("")
                    for line in report.splitlines():
                        self.append(line)
                    self.status.set("AI-план готов: %.1f паролей/с на ядро" % speed)
                    self.set_busy(False)
                elif event == "found":
                    self.password, count, elapsed = args
                    self.status.set("Пароль подтверждён по публичному ключу. Проверено: %d; %.1f с." % (count, elapsed))
                    if self.auto_save_path:
                        try:
                            save_password(self.auto_save_path, self.password)
                        except OSError as exc:
                            self.append("Пароль найден, но автосохранение не удалось: " + str(exc))
                            self.append("Используйте кнопку «Сохранить пароль…».")
                        else:
                            self.append("Пароль найден и сохранён в новый файл: " + self.auto_save_path)
                            self.password = None
                            self.auto_save_path = None
                    else:
                        self.append("Пароль найден. Сохраните результат в защищённое локальное хранилище.")
                    self.set_busy(False)
                elif event == "finished":
                    count, elapsed, stopped = args
                    self.status.set(("Остановлено" if stopped else "В словаре пароль не найден") +
                                    ". Проверено: %d; %.1f с." % (count, elapsed))
                    if self.total:
                        self.bar.configure(value=min(count, self.total))
                    self.append(self.status.get())
                    self.set_busy(False)
                elif event == "failed":
                    self.append("Ошибка: " + args[0])
                    self.status.set("Ошибка. Подробности в журнале.")
                    self.set_busy(False)
                elif event == "total":
                    self.total = args[0]
                    self.bar.stop()
                    self.bar.configure(mode="determinate", maximum=max(1, self.total), value=0)
                elif event == "wordlist_checked":
                    total, warnings = args
                    for warning in warnings:
                        self.append(warning)
                    self.append("Кандидатов в словаре: %d" % total)
                    self.status.set("Словарь проверен: %d кандидатов" % total)
                    self.set_busy(False)
                elif event == "ai_done":
                    path, count, provider = args
                    self.words.set(path)
                    self.append("Создан словарь: %s (%d уникальных кандидатов)" % (path, count))
                    if provider == "openai":
                        self.append("Текст воспоминаний был отправлен внешнему провайдеру OpenAI.")
                    elif provider == "ollama":
                        self.append("Использован локальный Ollama; при недоступности возможен офлайн fallback (этапы в журнале).")
                    else:
                        self.append("Офлайн-генерация: сетевые запросы не выполнялись.")
                    self.status.set("AI-словарь готов: %d кандидатов" % count)
                    self.set_busy(False)
                elif event == "personal_done":
                    path, count = args
                    self.words.set(path)
                    self.append("Персональный словарь создан: %s (%d кандидатов)" % (path, count))
                    self.status.set("Персональный словарь готов и выбран для запуска")
                    self.set_busy(False)
                elif event == "hashcat_done":
                    code, state_dir = args
                    self.append("Hashcat завершил работу, код выхода: %d. Potfile: %s/wallet.potfile" % (code, state_dir))
                    self.status.set("Hashcat завершён (код %d)" % code)
                    self.set_busy(False)
        except queue.Empty:
            pass
        if self.started_at is not None:
            elapsed = max(0, time.monotonic() - self.started_at)
            if self.busy:
                self.elapsed_text.set("Время: %s • задача активна • интерфейс отвечает" % Progress._fmt(elapsed))
            else:
                if self.elapsed_text.get().find("задача активна") != -1:
                    self.elapsed_text.set("Время: %s • активных задач: 0" % Progress._fmt(elapsed))
                self.started_at = None
        if self.closing and not self.busy:
            self.password = None
            self.root.destroy()
            return
        self.root.after(100, self.poll)

    def save(self):
        if self.password is None:
            return
        if not messagebox.askokcancel("Незашифрованный файл",
                "Пароль будет сохранён без шифрования. На Windows права 0600 не заменяют NTFS ACL. "
                "Выберите защищённую личную папку.", parent=self.root):
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Новый файл для точных байтов пароля",
                                          initialfile="found_password.bin", defaultextension=".bin")
        if path:
            try:
                save_password(path, self.password)
                self.append("Пароль сохранён без перевода строки. Существующие файлы не перезаписываются.")
            except OSError as exc:
                messagebox.showerror("Не удалось сохранить", str(exc), parent=self.root)

    def check_words(self):
        if self.busy:
            return
        path = self.words.get().strip()
        if not path:
            messagebox.showerror("Нет файла", "Выберите файл словаря.", parent=self.root)
            return
        if not self.valid_wordlist(path, self.wallet.get().strip()):
            return
        self.begin("Подсчёт кандидатов в словаре…", recovery=True)
        def task():
            try:
                total, warnings = self.count_words(path)
                self.sink("wordlist_checked", total, warnings)
            except InterruptedError:
                self.sink("cancelled")
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def generate_ai(self):
        if self.busy:
            return
        memory = self.memory.get().strip()
        seeds = [item.strip() for item in self.seeds.get().split(",") if item.strip()]
        wordlist = self.words.get().strip() or None
        provider = self.provider.get()
        if not memory and not seeds and not wordlist:
            messagebox.showerror("AI-словарь",
                "Укажите воспоминания, ключевые слова или словарь-основу.", parent=self.root)
            return
        if provider == "openai" and not messagebox.askokcancel("Внешний провайдер",
                "Текст воспоминаний будет отправлен на серверы OpenAI. Продолжить?", parent=self.root):
            return
        try:
            limit = int(self.limit.get())
            if not 1 <= limit <= 1000000:
                raise ValueError
        except ValueError:
            messagebox.showerror("Лимит", "Лимит кандидатов — целое число от 1 до 1000000.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Новый файл AI-словаря",
                                            initialfile="ai_words.txt", defaultextension=".txt",
                                            confirmoverwrite=False)
        if not path:
            return
        if os.path.lexists(path):
            messagebox.showerror("Файл существует",
                "Файл уже существует; выберите новое имя — перезапись запрещена.", parent=self.root)
            return
        if wordlist and not self.valid_wordlist(wordlist):
            return
        if provider not in ("offline", "ollama", "openai"):
            messagebox.showerror("Провайдер", "Выберите offline, ollama или openai.", parent=self.root)
            return
        try:
            ollama_url = self.local_ollama_url() if provider == "ollama" else "http://127.0.0.1:11434"
        except ValueError as exc:
            messagebox.showerror("Ollama", str(exc), parent=self.root)
            return
        model = self.openai_model.get().strip() if provider == "openai" else self.ollama_model.get().strip()
        if provider != "offline" and not model:
            messagebox.showerror("Модель", "Укажите модель во вкладке настроек.", parent=self.root)
            return
        self.begin("Генерация AI-словаря: провайдер " + provider + "…", recovery=True)
        def task():
            try:
                from .ai.pipeline import export_candidates
                count = export_candidates(path, prompt=memory, seed_words=seeds, wordlist=wordlist,
                                          max_candidates=limit, provider=provider,
                                          model=model, ollama_url=ollama_url,
                                          include_neural_lm=True, rank=True,
                                          progress_callback=lambda phase, done: self.sink(
                                              "stage", "AI %s: обработано %d" % (phase, done)),
                                          stage_callback=lambda message: self.sink("stage", message),
                                          stop_event=self.cancel_event)
                self.sink("ai_done", path, count, provider)
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def generate_personal(self):
        if self.busy:
            return
        seeds = [item.strip() for item in self.seeds.get().split(",") if item.strip()]
        dates = [item.strip() for item in self.dates.get().split(",") if item.strip()]
        if not seeds:
            messagebox.showerror("Персональный словарь", "Укажите хотя бы одно собственное ключевое слово.", parent=self.root)
            return
        try:
            limit = int(self.limit.get())
            if not 1 <= limit <= 1000000:
                raise ValueError
        except ValueError:
            messagebox.showerror("Лимит", "Лимит кандидатов — целое число от 1 до 1000000.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Новый персональный словарь", initialfile="personal_words.txt",
            defaultextension=".txt", confirmoverwrite=False)
        if not path:
            return
        if os.path.lexists(path):
            messagebox.showerror("Файл существует", "Перезапись запрещена; выберите новое имя.", parent=self.root)
            return
        self.begin("Создание персонального словаря офлайн…", recovery=True)
        def task():
            try:
                count = write_unique(
                    os.path.abspath(path),
                    personal_candidates(seeds, dates, range(2009, 2016)),
                    limit,
                )
                self.sink("personal_done", path, count)
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def run_hashcat_action(self):
        if self.busy:
            return
        hash_path, words = self.hash.get().strip(), self.words.get().strip()
        if not os.path.isfile(hash_path) or not os.path.isfile(words):
            messagebox.showerror("Файлы", "Выберите существующие файлы хеша и словаря.", parent=self.root)
            return
        if not self.hashcat_consent.get():
            messagebox.showwarning("Подтверждение",
                "Подтвердите, что хеш извлечён из вашего кошелька или с разрешения владельца.", parent=self.root)
            return
        state_dir = self.state_dir.get().strip() or ".wallet-recover-hashcat"
        resume = self.hashcat_resume.get()
        if not self.valid_wordlist(words):
            return
        executable = self.hashcat_executable.get().strip() or "hashcat"
        self.begin("Запуск локального Hashcat…", recovery=True)
        def task():
            try:
                from .ai.hashcat import run_hashcat
                code = run_hashcat(hash_path, words, state_dir, executable=executable,
                                   resume=resume, stop_event=self.cancel_event,
                                   progress_callback=lambda status: self.sink("hashcat_status", status))
                self.sink("hashcat_done", code, state_dir)
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def close(self):
        if self.busy:
            if not messagebox.askokcancel("Завершение", "Остановить работу и закрыть окно", parent=self.root):
                return
            self.closing = True
            self.stop()
        else:
            self.password = None
            self.root.destroy()


def main():
    mp.freeze_support()
    root = tk.Tk()
    Application(root)
    root.mainloop()


if __name__ == "__main__":
    main()
