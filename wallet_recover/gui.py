"""Russian desktop interface using Python's bundled Tk. No network access."""
import multiprocessing as mp
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from .cli import save_password
from .runner import RecoveryEngine, inspect_wallet


class Application:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.engine = None
        self.password = None
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
        self.status = tk.StringVar(value="Готово к работе. Выберите резервную копию кошелька.")
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(10, weight=1)
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
        ttk.Label(frame, text="Legacy BDB / method 0. SQLite-кошельки не поддерживаются.",
                  style="Hint.TLabel").grid(row=5, column=0, columnspan=3, sticky="w", pady=8)
        consent = ttk.Checkbutton(frame, variable=self.authorized,
            text="Кошелёк мой / есть разрешение владельца. Bitcoin Core закрыт, использую копию.")
        consent.grid(row=6, column=0, columnspan=3, sticky="w", pady=8)
        self.inputs.append(consent)
        actions = ttk.Frame(frame)
        actions.grid(row=7, column=0, columnspan=3, sticky="ew", pady=12)
        self.inspect_btn = ttk.Button(actions, text="Проверить кошелёк", command=self.inspect)
        self.inspect_btn.pack(side="left")
        self.start_btn = ttk.Button(actions, text="Начать проверку паролей", command=self.start)
        self.start_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(actions, text="Остановить", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        self.save_btn = ttk.Button(actions, text="Сохранить пароль…", command=self.save, state="disabled")
        self.save_btn.pack(side="right")
        ttk.Label(frame, textvariable=self.status, wraplength=740).grid(
            row=8, column=0, columnspan=3, sticky="w", pady=6)
        self.bar = ttk.Progressbar(frame, mode="indeterminate")
        self.bar.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        logframe = ttk.Frame(frame)
        logframe.grid(row=10, column=0, columnspan=3, sticky="nsew")
        self.log = tk.Text(logframe, wrap="word", height=10, state="disabled",
                           font=("Consolas", 10), background="#101c2c", foreground="#dce7f5")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(logframe, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)
        ttk.Label(frame, text="Пароль не показывается в журнале. Сохранение — только по вашей команде.",
                  style="Hint.TLabel").grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 0))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def _file_row(self, frame, row, label, variable):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=8)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=8)
        button = ttk.Button(frame, text="Обзор…", command=lambda: self.browse(variable))
        button.grid(row=row, column=2, padx=(8, 0))
        self.inputs.extend([entry, button])

    def browse(self, variable):
        path = filedialog.askopenfilename(parent=self.root, title="Выберите локальный файл")
        if path:
            variable.set(path)

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def sink(self, event, *args):
        self.events.put((event, args))

    def set_busy(self, value, recovery=False):
        self.busy = value
        for control in self.inputs + [self.start_btn, self.inspect_btn]:
            control.configure(state="disabled" if value else "normal")
        self.stop_btn.configure(state="normal" if value and recovery else "disabled")
        self.save_btn.configure(state="normal" if not value and self.password is not None else "disabled")
        if value:
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

    def start(self):
        if not self.authorized.get():
            messagebox.showwarning("Подтверждение", "Подтвердите право доступа и использование закрытой копии.", parent=self.root)
            return
        wallet, words = self.wallet.get().strip(), self.words.get().strip()
        if not os.path.isfile(wallet) or not os.path.isfile(words):
            messagebox.showerror("Файлы", "Выберите существующие файлы кошелька и словаря.", parent=self.root)
            return
        try:
            engine = RecoveryEngine(wallet, words, int(self.jobs.get()), self.sink)
        except ValueError as exc:
            messagebox.showerror("Параметры", str(exc), parent=self.root)
            return
        self.password = None
        self.engine = engine
        self.set_busy(True, recovery=True)
        self.status.set("Подготовка к проверке…")
        engine.start()

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
                    self.status.set("Проверено: %d • Скорость: %.1f кандидатов/с" % args)
                elif event == "inspection":
                    info = args[0]
                    self.append("Записей mkey: %d; ckey: %d" % (len(info["masters"]), info["keys"]))
                    for m in info["masters"]:
                        self.append("mkey %d: method=%d, итераций=%d" % (m["id"], m["method"], m["iterations"]))
                    for issue in info["problems"]:
                        self.append(issue)
                    self.status.set("Кошелёк пригоден для проверки" if info["usable"] else "Кошелёк не поддерживается или неполный")
                    self.set_busy(False)
                elif event == "found":
                    self.password, count, elapsed = args
                    self.status.set("Пароль подтверждён по публичному ключу. Проверено: %d; %.1f с." % (count, elapsed))
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
