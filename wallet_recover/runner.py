"""Bounded adaptive batches; resume commits only a contiguous completed prefix.

Stop terminates uncommitted work. A crash may repeat work since the last atomic
checkpoint, but never skips unfinished candidates. Events run on the engine thread.
"""
import multiprocessing as mp
import os
from pathlib import Path
import threading
import time

from .bdb import load_wallet
from .checkpoint import Checkpoint
from .crypto import Decryptor
from .errors import CheckpointError
from .wordlist import count, fingerprint, iter_lines, iter_offsets

_DECRYPTOR = None
_BATCH_MIN, _BATCH_MAX = 4, 64
_CHECKPOINT_SECONDS = 2.0


def _init_worker(masters, keys):
    global _DECRYPTOR
    _DECRYPTOR = Decryptor(masters, keys)


def _run_batch(passwords):
    for index, password in enumerate(passwords):
        if _DECRYPTOR.try_password(password):
            return index + 1, password
    return len(passwords), None


class _CommitFrontier:
    """Out-of-order acknowledgements cannot advance beyond a missing batch."""
    def __init__(self, consumed=0, offset=0):
        self.consumed, self.offset = consumed, offset
        self.sequence = 0
        self.pending = {}

    def acknowledge(self, sequence, size, offset):
        self.pending[sequence] = (size, offset)
        while self.sequence in self.pending:
            size, self.offset = self.pending.pop(self.sequence)
            self.consumed += size
            self.sequence += 1


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
    for line in iter_lines(path):
        total += 1
        empty += line == b""
    if not total:
        raise ValueError("Словарь пуст")
    return total, (["Пустых кандидатов: %d" % empty] if empty else [])


def counting_lines(path):
    return check_wordlist(path)[0]


def _stamp(path):
    s = Path(path).stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


class RecoveryEngine:
    """One-shot engine with opt-in checkpoints, no implicit output files.

    Events: stage(message), total(n), log(message), progress(done, rate, eta, elapsed),
    found(password, done, elapsed), finished(done, elapsed, stopped), failed(message).
    Preparation progress has rate=0 and eta=inf: no candidates were checked yet.
    Stages and checkpoint logs contain no candidate bytes or fingerprints.
    completed counts actual checks plus the previously committed prefix.
    Checkpoint consumed is conservative and may be smaller than completed.
    """
    def __init__(self, wallet, wordlist, jobs=None, sink=None,
                 checkpoint_path=None, resume=False):
        self.wallet, self.wordlist = wallet, wordlist
        self.jobs = max(1, min(64, os.cpu_count() or 1)) if jobs is None else int(jobs)
        if not 1 <= self.jobs <= 64:
            raise ValueError("Число процессов должно быть от 1 до 64")
        self.sink = sink
        self.checkpoint_path = Path(checkpoint_path).expanduser() if checkpoint_path else None
        self.resume = resume
        if resume and not self.checkpoint_path:
            raise CheckpointError("Resume requires a checkpoint path")
        if self.checkpoint_path:
            for source in (wallet, wordlist):
                if self.checkpoint_path.resolve() == Path(source).expanduser().resolve():
                    raise CheckpointError("Checkpoint must not overwrite an input file")
                if self.checkpoint_path.exists() and Path(source).exists():
                    if os.path.samefile(self.checkpoint_path, source):
                        raise CheckpointError("Checkpoint aliases an input file")
        self.completed = 0
        self.elapsed = 0.0
        self.rate = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._frontier = _CommitFrontier()
        self._previous_elapsed = 0.0

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Для нового запуска создайте новый движок")
        self._thread = threading.Thread(target=self._run, name="recover", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self):
        self._stop.set()

    def stopped(self):
        return self._stop.is_set()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def _emit(self, event, *args):
        if self.sink:
            self.sink(event, *args)

    def _prepare(self):
        self._input_stamps = (_stamp(self.wallet), _stamp(self.wordlist))
        self._emit("stage", "Вычисление полного отпечатка кошелька")
        self._wallet_hash = fingerprint(self.wallet)
        self._emit("stage", "Вычисление полного отпечатка словаря")
        self._wordlist_hash = fingerprint(self.wordlist)
        self._emit("stage", "Подсчёт строк словаря")
        self._total = count(self.wordlist)
        self._check_inputs()
        if self.resume:
            self._emit("stage", "Загрузка и проверка контрольной точки")
            cp = Checkpoint.load(self.checkpoint_path)
            if (cp.wordlist != str(Path(self.wordlist).resolve())
                    or cp.wallet_fingerprint != self._wallet_hash
                    or cp.wordlist_fingerprint != self._wordlist_hash
                    or cp.wordlist_size != Path(self.wordlist).stat().st_size
                    or cp.total != self._total):
                raise CheckpointError("Wallet or wordlist differs from the checkpoint")
            # Validate line and byte counters together, not just an arbitrary seek.
            self._emit("stage", "Проверка точного смещения словаря для продолжения")
            offset = 0
            with open(self.wordlist, "rb") as f:
                for _ in range(cp.consumed):
                    if not f.readline():
                        raise CheckpointError("Checkpoint line count exceeds wordlist")
                offset = f.tell()
            if offset != cp.bytes_offset:
                raise CheckpointError("Checkpoint byte offset does not match its line count")
            self._frontier = _CommitFrontier(cp.consumed, cp.bytes_offset)
            self.completed = cp.consumed
            self._previous_elapsed = cp.elapsed_s
            self._emit("log", "Продолжение с подтверждённой строки %d" % cp.consumed)
        elif self.checkpoint_path and os.path.lexists(self.checkpoint_path):
            raise CheckpointError("Checkpoint exists; use resume or choose a NEW path")
        elif self.checkpoint_path:
            self._emit("stage", "Подготовка новой контрольной точки")
        self._emit("total", self._total)

    def _check_inputs(self):
        if self._input_stamps != (_stamp(self.wallet), _stamp(self.wordlist)):
            raise CheckpointError("Input changed during recovery; checkpoint not advanced")

    def _save_checkpoint(self, finished=False):
        if not self.checkpoint_path:
            return
        self._emit("stage", "Сохранение подтверждённого прогресса в контрольную точку")
        self._check_inputs()
        active = max(time.monotonic() - self._started, 0.001)
        Checkpoint(
            wordlist=str(Path(self.wordlist).resolve()),
            wordlist_size=Path(self.wordlist).stat().st_size,
            wallet_fingerprint=self._wallet_hash,
            wordlist_fingerprint=self._wordlist_hash,
            consumed=self._frontier.consumed, bytes_offset=self._frontier.offset,
            elapsed_s=self._previous_elapsed + active,
            rate_ps=(self.completed - self._baseline) / active,
            finished=finished, total=self._total,
        ).save(self.checkpoint_path)
        self._emit("log", "Контрольная точка сохранена: строк %d, смещение %d байт" %
                   (self._frontier.consumed, self._frontier.offset))

    def _run(self):
        pool = stream = None
        self._started = time.monotonic()
        try:
            if self.stopped():
                self._emit("finished", 0, 0.0, True)
                return
            self._prepare()
            self._emit("stage", "Чтение кошелька Berkeley DB (BDB)")
            masters, keys = load_wallet(self.wallet)
            self._emit("stage", "Проверка параметров шифрования кошелька")
            Decryptor(masters, keys)  # validate in parent: no respawning failing initializers
            self._check_inputs()
            self._baseline = self.completed
            self._started = time.monotonic()
            self._save_checkpoint(finished=self._frontier.consumed == self._total)
            if self._frontier.consumed == self._total:
                self._emit("finished", self.completed, self._previous_elapsed, False)
                return
            if self.stopped():
                self.elapsed = self._previous_elapsed + time.monotonic() - self._started
                self._emit("finished", self.completed, self.elapsed, True)
                return
            self._emit("stage", "Запуск рабочих процессов: %d" % self.jobs)
            pool = mp.get_context("spawn").Pool(self.jobs, _init_worker, (masters, keys))
            self._emit("log", "Запущено процессов: %d" % self.jobs)
            self._emit("stage", "Проверка кандидатов")
            stream = iter_offsets(self.wordlist, self._frontier.offset)
            pending = {}
            sequence, batch_size = 0, _BATCH_MIN
            exhausted = False
            last_emit = last_save = time.monotonic()
            while not self.stopped():
                # Also bound acknowledged-but-not-committed batches behind slow work.
                while (not exhausted and not self.stopped()
                       and sequence - self._frontier.sequence < 2 * self.jobs):
                    batch, end = [], self._frontier.offset
                    for _ in range(batch_size):
                        item = next(stream, None)
                        if item is None:
                            exhausted = True
                            break
                        password, end = item
                        batch.append(password)
                    if batch:
                        pending[sequence] = (pool.apply_async(_run_batch, (batch,)), len(batch), end)
                        sequence += 1
                ready = [seq for seq, (result, _, _) in pending.items() if result.ready()]
                for seq in ready:
                    result, size, end = pending.pop(seq)
                    checked, found = result.get()  # worker exceptions reach failed event
                    self.completed += checked
                    if found is not None:
                        # Do NOT commit this batch: a found password is not stored in cp.
                        # A resumed session rechecks it rather than losing the result.
                        self._check_inputs()
                        self._save_checkpoint()
                        self.rate = (self.completed - self._baseline) / max(time.monotonic() - self._started, 0.001)
                        self.elapsed = self._previous_elapsed + time.monotonic() - self._started
                        pool.terminate()
                        pool.join()
                        pool = None
                        self._emit("found", found, self.completed, self.elapsed)
                        return
                    self._frontier.acknowledge(seq, size, end)
                now = time.monotonic()
                active = max(now - self._started, 0.001)
                rate = self.rate = (self.completed - self._baseline) / active
                if now - last_emit >= 0.25 or (exhausted and not pending):
                    self._emit("progress", self.completed, rate,
                               max(self._total - self.completed, 0) / rate if rate else float("inf"),
                               self._previous_elapsed + active)
                    last_emit = now
                    # Aim for about 0.1 seconds of work per batch, bounded to 4..64.
                    batch_size = max(_BATCH_MIN, min(_BATCH_MAX, int(rate / self.jobs * 0.1)))
                if now - last_save >= _CHECKPOINT_SECONDS:
                    self._save_checkpoint()
                    if self.checkpoint_path:
                        self._emit("stage", "Проверка кандидатов")
                    last_save = now
                if exhausted and not pending:
                    break
                self._stop.wait(0.01)
            pool.terminate()
            pool.join()
            pool = None
            self._check_inputs()
            finished = self._frontier.consumed == self._total
            self._save_checkpoint(finished=finished)
            self.elapsed = self._previous_elapsed + time.monotonic() - self._started
            self._emit("finished", self.completed, self.elapsed, self.stopped())
        except Exception as exc:
            self._emit("failed", "%s: %s" % (type(exc).__name__, exc))
        finally:
            if stream is not None:
                stream.close()
            if pool is not None:
                pool.terminate()
                pool.join()
