"""Движок подбора, независимый от интерфейса (CLI и GUI используют его).

Все процессы создаются через контекст ``spawn``: это единственный способ,
который одинаково работает на Windows 7/10 и на Linux/macOS. Поэтому модуль
не содержит побочных эффектов при импорте.
"""
import multiprocessing as mp
import os
import time
from pathlib import Path

from .bdb import load_wallet
from .cli import candidates, save_password
from .cli import init_worker, worker

__all__ = [
    "inspect_wallet", "counting_lines", "RecoveryEngine",
    "candidates", "save_password", "check_wordlist",
]


def inspect_wallet(wallet_path):
    """Читает параметры кошелька без подбора пароля."""
    masters, keys = load_wallet(wallet_path)
    info = {
        "masters": [
            {"id": m.id, "method": m.method, "iterations": m.iterations,
             "salt": m.salt.hex(), "has_extra": bool(m.other)}
            for m in masters
        ],
        "keys": len(keys),
        "compressed_keys": sum(1 for k in keys if len(k.pubkey) == 33),
        "usable": bool(masters) and bool(keys),
    }
    problems = []
    if not masters:
        problems.append("нет записей mkey — кошелёк не зашифрован или файл неполный")
    if not keys:
        problems.append("нет записей ckey — подтвердить пароль невозможно")
    if any(m.method != 0 or m.other for m in masters):
        problems.append("неподдерживаемый метод деривации (нужен method 0)")
    info["problems"] = problems
    return info


def check_wordlist(path):
    """Проверяет словарь и возвращает (число строк, предупреждения)."""
    p = Path(path)
    if not p.is_file():
        raise ValueError("Файл словаря не найден")
    total, empty, warnings = 0, 0, []
    with p.open("rb") as f:
        for line in f:
            total += 1
            if line.rstrip(b"\r\n") == b"":
                empty += 1
    if total == 0:
        raise ValueError("Словарь пуст")
    if empty:
        warnings.append("пустых строк: %d (каждая проверяется как пустой пароль)" % empty)
    if total > 5_000_000:
        warnings.append("очень большой словарь: %d строк" % total)
    return total, warnings


def counting_lines(path):
    return check_wordlist(path)[0]


class RecoveryEngine:
    """Запускает подбор в фоновом потоке и отдаёт события через callbacks.

    events: log(str), progress(completed, rate), found(bytes),
            finished(completed, elapsed, stopped), failed(str)
    """

    def __init__(self, wallet, wordlist, jobs=None, sink=None):
        self.wallet = wallet
        self.wordlist = wordlist
        self.jobs = jobs or max(1, os.cpu_count() or 1)
        self.sink = sink
        self.completed = 0
        self.started_at = None
        self._pool = None
        self._stop = False

    # -- lifecycle -----------------------------------------------------
    def start(self):
        import threading
        self._thread = threading.Thread(target=self._run, name="recover", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self):
        self._stop = True
        pool = self._pool
        if pool is not None:
            try:
                pool.terminate()
            except Exception:
                pass

    def is_running(self):
        return getattr(self, "_thread", None) is not None and self._thread.is_alive()

    # -- worker --------------------------------------------------------
    def _emit(self, kind, *args):
        sink = self.sink
        if sink is not None:
            sink(kind, *args)

    def _run(self):
        self.started_at = time.monotonic()
        try:
            masters, keys = load_wallet(self.wallet)
            if not masters:
                raise ValueError("Нет mkey: кошелёк не зашифрован или файл неполный")
            if not keys:
                raise ValueError("Нет ckey: подтвердить пароль невозможно")
            if any(m.method != 0 or m.other for m in masters):
                raise ValueError("Неподдерживаемые параметры KDF (нужен method 0)")
            self._emit("log", "Запуск: %d процессов, %d записей ckey" % (self.jobs, len(keys)))
            context = mp.get_context("spawn")
            pool = context.Pool(self.jobs, init_worker, (masters, keys))
            self._pool = pool
            found = None
            last_report = 0.0
            try:
                for result in pool.imap_unordered(worker, candidates(self.wordlist),
                                                  chunksize=16):
                    self.completed += 1
                    if result is not None:
                        found = result
                        break
                    now = time.monotonic()
                    if now - last_report >= 0.2:
                        last_report = now
                        elapsed = now - self.started_at
                        self._emit("progress", self.completed,
                                   self.completed / max(elapsed, 0.001))
                pool.close()
                pool.join()
            except (KeyboardInterrupt, Exception):
                try:
                    pool.terminate()
                    pool.join()
                except Exception:
                    pass
                raise
            finally:
                self._pool = None
            elapsed = time.monotonic() - self.started_at
            if found is None:
                self._emit("finished", self.completed, elapsed, self._stop, None)
                return
            if self.sink_file:
                try:
                    save_password(self.sink_file, found)
                    self._emit("log", "Пароль сохранён в файл (0600, без перевода строки)")
                except OSError as exc:
                    self._emit("log", "Не удалось сохранить файл: %s" % exc)
            self._emit("found", found, self.completed, elapsed)
        except Exception as exc:
            self._emit("failed", str(exc))

    sink_file = None


def _attach_sink_file(engine, path):
    engine.sink_file = path
    return engine
