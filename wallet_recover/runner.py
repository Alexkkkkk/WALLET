"""Background recovery engine; callbacks are delivered on the worker thread."""
import multiprocessing as mp
import os
import threading
import time
from pathlib import Path
from .bdb import load_wallet
from .cli import candidates, init_worker, worker


def inspect_wallet(path):
    masters, keys = load_wallet(path)
    problems = []
    if not masters:
        problems.append("Нет mkey: кошелёк не зашифрован или файл неполный")
    if not keys:
        problems.append("Нет ckey: подтвердить пароль невозможно")
    if any(m.method != 0 or m.other for m in masters):
        problems.append("Поддерживается только метод деривации 0 без дополнительных параметров")
    return {"masters": [{"id": m.id, "method": m.method, "iterations": m.iterations}
                        for m in masters], "keys": len(keys),
            "usable": not problems, "problems": problems}


def check_wordlist(path):
    if not Path(path).is_file():
        raise ValueError("Файл словаря не найден")
    total = empty = 0
    for line in candidates(path):
        total += 1
        empty += (line == b"")
    if not total:
        raise ValueError("Словарь пуст")
    warnings = ["Пустых кандидатов: %d" % empty] if empty else []
    return total, warnings


def counting_lines(path):
    return check_wordlist(path)[0]


class RecoveryEngine:
    """One-shot engine. stop() is nonblocking; queues carry no secrets except found.

    Events: log(message), progress(count, rate), found(password, count, seconds),
    finished(count, seconds, stopped), failed(message). No output files are written.
    """
    def __init__(self, wallet, wordlist, jobs=None, sink=None):
        self.wallet, self.wordlist = wallet, wordlist
        self.jobs = max(1, min(64, os.cpu_count() or 1)) if jobs is None else int(jobs)
        if not 1 <= self.jobs <= 64:
            raise ValueError("Число процессов должно быть от 1 до 64")
        self.sink = sink
        self.completed = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Для нового запуска создайте новый движок")
        self._thread = threading.Thread(target=self._run, name="recover", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self):
        self._stop.set()

    def stopped(self):
        """Whether stop() has already been requested for this engine."""
        return self._stop.is_set()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def _emit(self, event, *args):
        if self.sink:
            self.sink(event, *args)

    def _run(self):
        start = time.monotonic()
        pool = None
        try:
            if not self._stop.is_set():
                masters, keys = load_wallet(self.wallet)
                if not masters or not keys:
                    raise ValueError("Нужны записи mkey и ckey для подтверждения пароля")
                if any(m.method != 0 or m.other for m in masters):
                    raise ValueError("Поддерживается только method 0 без дополнительных параметров")
                if not Path(self.wordlist).is_file():
                    raise ValueError("Файл словаря не найден")
                if not self._stop.is_set():
                    pool = mp.get_context("spawn").Pool(self.jobs, init_worker, (masters, keys))
                    # chunksize=1 permits timeout polling for a responsive Stop button.
                    results = pool.imap_unordered(worker, candidates(self.wordlist), chunksize=1)
                    self._emit("log", "Запущено процессов: %d" % self.jobs)
                    last = start
                    while not self._stop.is_set():
                        try:
                            result = results.next(timeout=0.1)
                        except mp.TimeoutError:
                            continue
                        except StopIteration:
                            break
                        self.completed += 1
                        elapsed = time.monotonic() - start
                        if result is not None:
                            pool.terminate()
                            pool.join()
                            pool = None
                            self._emit("found", result, self.completed, elapsed)
                            return
                        if time.monotonic() - last >= 0.2:
                            last = time.monotonic()
                            self._emit("progress", self.completed,
                                       self.completed / max(elapsed, 0.001))
            if pool is not None:
                pool.terminate()
                pool.join()
                pool = None
            self._emit("finished", self.completed, time.monotonic() - start,
                       self._stop.is_set())
        except Exception as exc:
            if pool is not None:
                pool.terminate()
                pool.join()
                pool = None
            self._emit("failed", str(exc))
        finally:
            if pool is not None:
                pool.terminate()
                pool.join()
