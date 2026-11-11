"""Russian desktop interface using Python's bundled Tk. No network access."""
import multiprocessing as mp
import os
import queue
import threading
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
        self.root.title("WALLET — локальное восстановление")
        self.root.geometry("860x640")
        self.root.minsize(700, 580)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"))
        style.configure("Hint.TLabel", foreground="#526174")
        self.wallet = tk.StringVar()
        self.words = tk.StringVar()
        self.jobs = tk.StringVar(value=str(min(8, os.cpu_count() or 1)))
        self.authorized = tk.BooleanVar()
        self.checkpoint = tk.StringVar()
        self.resume_cpu = tk.BooleanVar()
        self.status = tk.StringVar(value="Готово к работе. Выберите резервную копию кошелька.")
        self.total = None
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(12, weight=1)
        ttk.Label(frame, text="WALLET", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="Локально • Без отправки файлов • Только ваш кошелёк", style="Hint.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 18))
        self.inputs = []
        self._file_row(frame, 2, "Копия wallet.dat", self.wallet)
        self._file_row(frame, 3, "Словарь паролей", self.words)
        ttk.Label(frame, text="Процессов CPU").grid(row=4, column=0, sticky="w", pady=8)
        spin = ttk.Spinbox(frame, from_=1, to=64, width=6, textvariable=self.jobs)
        spin.grid(row=4, column=1, sticky="w")
        self.inputs.append(spin)
        options = ttk.Frame(frame)
        options.grid(row=5, column=0, columnspan=3, sticky="ew", pady=8)
        options.columnconfigure(1, weight=1)
        ttk.Label(options, text="Чекпоинт CPU (необязательно)").grid(row=0, column=0, sticky="w")
        checkpoint_entry = ttk.Entry(options, textvariable=self.checkpoint)
        checkpoint_entry.grid(row=0, column=1, sticky="ew", padx=8)
        checkpoint_btn = ttk.Button(options, text="Путь…", command=self.choose_checkpoint)
        checkpoint_btn.grid(row=0, column=2)
        resume_cpu = ttk.Checkbutton(options, text="Продолжить CPU", variable=self.resume_cpu)
        resume_cpu.grid(row=1, column=0, columnspan=2, sticky="w")
        self.inputs.extend([checkpoint_entry, checkpoint_btn, resume_cpu])
        consent = ttk.Checkbutton(frame, variable=self.authorized,
            text="Кошелёк мой / есть разрешение владельца. Bitcoin Core закрыт, использую копию.")
        consent.grid(row=6, column=0, columnspan=3, sticky="w", pady=8)
        self.inputs.append(consent)
        actions = ttk.Frame(frame)
        actions.grid(row=7, column=0, columnspan=3, sticky="ew", pady=12)
        self.quick_btn = ttk.Button(actions, text="Быстрый запуск…", command=self.quick_start)
        self.quick_btn.pack(side="left")
        self.inputs.append(self.quick_btn)
        self.inspect_btn = ttk.Button(actions, text="Проверить кошелёк", command=self.inspect)
        self.inspect_btn.pack(side="left")
        self.plan_btn = ttk.Button(actions, text="AI-план восстановления", command=self.make_plan)
        self.plan_btn.pack(side="left", padx=8)
        self.inputs.append(self.plan_btn)
        self.words_btn = ttk.Button(actions, text="Проверить словарь", command=self.check_words)
        self.words_btn.pack(side="left", padx=8)
        self.inputs.append(self.words_btn)
        self.start_btn = ttk.Button(actions, text="Начать проверку паролей", command=self.start)
        self.start_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(actions, text="Остановить", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        self.save_btn = ttk.Button(actions, text="Сохранить пароль…", command=self.save, state="disabled")
        self.save_btn.pack(side="right")
        ai = ttk.LabelFrame(frame, text="AI-генерация словаря (офлайн по умолчанию)", padding=12)
        ai.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ai.columnconfigure(1, weight=1)
        ttk.Label(ai, text="Воспоминания о пароле").grid(row=0, column=0, sticky="w", pady=4)
        self.memory = tk.StringVar()
        memory_entry = ttk.Entry(ai, textvariable=self.memory)
        memory_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self.inputs.append(memory_entry)
        ttk.Label(ai, text="Ключевые слова (через запятую)").grid(row=1, column=0, sticky="w", pady=4)
        self.seeds = tk.StringVar()
        seeds_entry = ttk.Entry(ai, textvariable=self.seeds)
        seeds_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.inputs.append(seeds_entry)
        ttk.Label(ai, text="Даты (через запятую)").grid(row=2, column=0, sticky="w", pady=4)
        self.dates = tk.StringVar()
        dates_entry = ttk.Entry(ai, textvariable=self.dates)
        dates_entry.grid(row=2, column=1, sticky="ew", pady=4)
        self.inputs.append(dates_entry)
        ttk.Label(ai, text="Провайдер").grid(row=3, column=0, sticky="w", pady=4)
        self.provider = tk.StringVar(value="offline")
        provider_box = ttk.Combobox(ai, textvariable=self.provider, state="readonly", width=10,
                                    values=("offline", "ollama", "openai"))
        provider_box.grid(row=3, column=1, sticky="w", pady=4)
        self.inputs.append(provider_box)
        ttk.Label(ai, text="Лимит кандидатов").grid(row=3, column=2, sticky="e", padx=(16, 8))
        self.limit = tk.StringVar(value="5000")
        limit_spin = ttk.Spinbox(ai, from_=1, to=1000000, width=8, textvariable=self.limit)
        limit_spin.grid(row=3, column=3, sticky="w", pady=4)
        self.inputs.append(limit_spin)
        self.ai_btn = ttk.Button(ai, text="Создать AI-словарь…", command=self.generate_ai)
        self.ai_btn.grid(row=4, column=0, sticky="w", pady=4)
        self.inputs.append(self.ai_btn)
        self.personal_btn = ttk.Button(ai, text="Создать персональный словарь", command=self.generate_personal)
        self.personal_btn.grid(row=4, column=1, columnspan=2, sticky="w", pady=4)
        self.inputs.append(self.personal_btn)
        hc = ttk.LabelFrame(frame, text="Локальный Hashcat (mode 11300, извлечённый вами хеш)", padding=12)
        hc.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        hc.columnconfigure(1, weight=1)
        ttk.Label(hc, text="Файл хеша").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        self.hash = tk.StringVar()
        hash_entry = ttk.Entry(hc, textvariable=self.hash)
        hash_entry.grid(row=0, column=1, sticky="ew", pady=4)
        hash_btn = ttk.Button(hc, text="Обзор…", command=lambda: self.browse(self.hash))
        hash_btn.grid(row=0, column=2, padx=(8, 0))
        self.inputs.extend([hash_entry, hash_btn])
        ttk.Label(hc, text="Рабочий каталог").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        self.state_dir = tk.StringVar(value=".wallet-recover-hashcat")
        state_entry = ttk.Entry(hc, textvariable=self.state_dir)
        state_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.inputs.append(state_entry)
        self.hashcat_resume = tk.BooleanVar()
        resume_check = ttk.Checkbutton(hc, variable=self.hashcat_resume, text="Продолжить сохранённую сессию")
        resume_check.grid(row=1, column=2, sticky="w", padx=(8, 0))
        self.inputs.append(resume_check)
        self.hashcat_consent = tk.BooleanVar()
        consent_check = ttk.Checkbutton(hc, variable=self.hashcat_consent,
            text="Хеш извлечён из моего кошелька или с разрешения владельца")
        consent_check.grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        self.inputs.append(consent_check)
        self.hashcat_btn = ttk.Button(hc, text="Запустить Hashcat", command=self.run_hashcat_action)
        self.hashcat_btn.grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        self.inputs.append(self.hashcat_btn)
        ttk.Label(frame, textvariable=self.status, wraplength=740).grid(
                row=10, column=0, columnspan=3, sticky="w", pady=6)
        self.bar = ttk.Progressbar(frame, mode="indeterminate")
        self.bar.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        logframe = ttk.Frame(frame)
        logframe.grid(row=12, column=0, columnspan=3, sticky="nsew")
        self.log = tk.Text(logframe, wrap="word", height=10, state="disabled",
                           font=("Consolas", 10), background="#101c2c", foreground="#dce7f5")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(logframe, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)
        ttk.Label(frame, text="Пароль не показывается в журнале. Сохранение — только по вашей команде.",
                  style="Hint.TLabel").grid(row=13, column=0, columnspan=3, sticky="w", pady=(10, 0))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

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
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def quick_start(self):
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
        if value:
            self.bar.configure(mode="indeterminate")
            self.bar.start(12)
        else:
            self.bar.stop()

    def inspect(self):
        path = self.wallet.get().strip()
        if not path:
            messagebox.showerror("Нет файла", "Выберите резервную копию wallet.dat.", parent=self.root)
            return
        self.set_busy(True)
        self.status.set("Чтение изолированной копии кошелька…")
        def task():
            try:
                self.sink("inspection", inspect_wallet(path))
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def make_plan(self):
        """Copilot: диагностика + бенчмарк + фазы атаки из общего модуля plan."""
        path = self.wallet.get().strip()
        if not path:
            messagebox.showerror("Нет файла", "Выберите резервную копию wallet.dat.", parent=self.root)
            return
        try:
            jobs = int(self.jobs.get())
        except ValueError:
            messagebox.showerror("Процессы", "Число процессов — целое число.", parent=self.root)
            return
        self.set_busy(True)
        self.status.set("AI-диагностика и измерение скорости…")
        def task():
            try:
                plan = wallet_plan(path, jobs=jobs)
                self.sink("plan_done", plan.summary(), plan.report, plan.speed_ps)
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def start(self):
        if not self.authorized.get():
            messagebox.showwarning("Подтверждение", "Подтвердите право доступа и использование закрытой копии.", parent=self.root)
            return
        wallet, words = self.wallet.get().strip(), self.words.get().strip()
        if not os.path.isfile(wallet) or not os.path.isfile(words):
            messagebox.showerror("Файлы", "Выберите существующие файлы кошелька и словаря.", parent=self.root)
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
        self.set_busy(True, recovery=True)
        self.status.set("Подготовка к проверке…")
        def prepare():
            try:
                total, warnings = check_wordlist(words)
            except ValueError as exc:
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
        if self.engine:
            self.engine.stop()
            self.status.set("Остановка…")
            self.stop_btn.configure(state="disabled")

    def poll(self):
        try:
            while True:
                event, args = self.events.get_nowait()
                if event == "log":
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
                    self.append("Создан словарь: %s (%d уникальных кандидатов)" % (path, count))
                    if provider == "openai":
                        self.append("Текст воспоминаний был отправлен внешнему провайдеру OpenAI.")
                    else:
                        self.append("Сетевые запросы не выполнялись; файл создан с правами 0600.")
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
        path = self.words.get().strip()
        if not path:
            messagebox.showerror("Нет файла", "Выберите файл словаря.", parent=self.root)
            return
        self.set_busy(True)
        self.status.set("Подсчёт кандидатов в словаре…")
        def task():
            try:
                total, warnings = check_wordlist(path)
                self.sink("wordlist_checked", total, warnings)
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def generate_ai(self):
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
            if limit < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Лимит", "Лимит кандидатов — целое число не меньше 1.", parent=self.root)
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
        self.set_busy(True)
        self.status.set("Генерация AI-словаря…")
        def task():
            try:
                from .ai.pipeline import export_candidates
                count = export_candidates(path, prompt=memory, seed_words=seeds, wordlist=wordlist,
                                          max_candidates=limit, provider=provider,
                                          model="gpt-5-mini" if provider == "openai" else "mistral",
                                          ollama_url="http://127.0.0.1:11434",
                                          include_neural_lm=True, rank=True)
                self.sink("ai_done", path, count, provider)
            except Exception as exc:
                self.sink("failed", str(exc))
        threading.Thread(target=task, daemon=True).start()

    def generate_personal(self):
        seeds = [item.strip() for item in self.seeds.get().split(",") if item.strip()]
        dates = [item.strip() for item in self.dates.get().split(",") if item.strip()]
        if not seeds:
            messagebox.showerror("Персональный словарь", "Укажите хотя бы одно собственное ключевое слово.", parent=self.root)
            return
        try:
            limit = int(self.limit.get())
            if limit < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Лимит", "Лимит кандидатов — целое число не меньше 1.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Новый персональный словарь", initialfile="personal_words.txt",
            defaultextension=".txt", confirmoverwrite=False)
        if not path:
            return
        if os.path.lexists(path):
            messagebox.showerror("Файл существует", "Перезапись запрещена; выберите новое имя.", parent=self.root)
            return
        self.set_busy(True)
        self.status.set("Создание персонального словаря офлайн…")
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
        self.set_busy(True)
        self.status.set("Запуск локального Hashcat…")
        def task():
            try:
                from .ai.hashcat import run_hashcat
                code = run_hashcat(hash_path, words, state_dir, resume=resume)
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
