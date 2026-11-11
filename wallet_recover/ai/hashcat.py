"""Optional, strictly local Hashcat adapter for legacy Bitcoin wallet hashes.

This module never downloads a binary/model, makes a network request, or uploads any
wallet/hash/candidate. Hash extraction is intentionally a separate user-controlled
step using a trusted local bitcoin2john.py installation. Child output is private:
only allowlisted numeric JSON status fields can reach a progress callback.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Callable

HASHCAT_MODE_BITCOIN_WALLET = "11300"
_POLL_SECONDS = 0.1
_HEARTBEAT_SECONDS = 1.0
_STOP_TIMEOUT_SECONDS = 3.0
_MAX_STATUS_LINE = 65536


def build_hashcat_command(hash_file: str | Path, wordlist: str | Path,
                          state_dir: str | Path, executable: str = "hashcat",
                          resume: bool = False) -> list[str]:
    """Build a shell-free local Hashcat wordlist command (Bitcoin wallet mode 11300)."""
    h = Path(hash_file).expanduser().resolve()
    w = Path(wordlist).expanduser().resolve()
    state = Path(state_dir).expanduser().resolve()
    if not h.is_file() or h.stat().st_size == 0:
        raise ValueError("Файл хеша не найден или пуст")
    if not w.is_file() or w.stat().st_size == 0:
        raise ValueError("Файл словаря не найден или пуст")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        state.chmod(0o700)
    except OSError:
        pass
    potfile = state / "wallet.potfile"
    # Restrict the potfile because it stores recovered plaintext passwords.
    if not potfile.exists():
        fd = os.open(str(potfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    else:
        try:
            potfile.chmod(0o600)
        except OSError:
            pass
    if resume:
        return [executable, "--session", "wallet-recovery-local", "--restore",
                "--potfile-path", str(potfile)]
    return [executable, "-m", HASHCAT_MODE_BITCOIN_WALLET, "-a", "0",
            "--session", "wallet-recovery-local", "--potfile-path", str(potfile),
            str(h), str(w)]


def _number(value):
    """Never coerce strings (which may contain hashes or candidate plaintext)."""
    if type(value) not in (int, float):
        return False
    try:
        return value >= 0 and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _safe_status(line):
    """Decode Hashcat --status-json; discard target, candidates and all strings."""
    try:
        data = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    safe = {}
    status = data.get("status")
    if type(status) is int and 0 <= status <= 12:
        safe["status"] = status
    for source, names in (("progress", ("done", "total")),
                          ("recovered_hashes", ("recovered", "hashes_total"))):
        values = data.get(source)
        if (isinstance(values, list) and len(values) == 2
                and all(type(v) is int and _number(v) for v in values)
                and values[0] <= values[1]):
            safe.update(zip(names, values))
    rejected = data.get("rejected")
    if type(rejected) is int and _number(rejected):
        safe["rejected"] = rejected
    devices = data.get("devices")
    if isinstance(devices, list):
        speeds = [d["speed"] for d in devices
                  if isinstance(d, dict) and _number(d.get("speed"))]
        if speeds:
            rate = sum(speeds)
            if _number(rate):
                safe["rate"] = rate
    if not safe:
        return None
    if safe.get("rate", 0) > 0 and "total" in safe:
        try:
            eta = (safe["total"] - safe["done"]) / safe["rate"]
            if _number(eta):
                safe["eta"] = eta
        except OverflowError:
            pass
    return {"event": "status", **safe}


def _read_status(output, updates, finished):
    """Drain the pipe without blocking stop polling or retaining raw child output."""
    try:
        while True:
            line = output.readline(_MAX_STATUS_LINE)
            if not line:
                break
            # Discard an entire oversized line, not fragments that resemble JSON.
            if len(line) == _MAX_STATUS_LINE and not line.endswith("\n"):
                while line and not line.endswith("\n"):
                    line = output.readline(_MAX_STATUS_LINE)
                continue
            status = _safe_status(line)
            if status is not None:
                try:
                    updates.put_nowait(status)
                except queue.Full:
                    # Backpressure must never stall Hashcat; the next status is enough.
                    pass
    except (OSError, ValueError):
        pass
    finally:
        finished.set()


def _terminate(process):
    """Reap the direct child even if it ignores the initial termination request."""
    if process.poll() is None:
        process.terminate()
    try:
        return process.wait(timeout=_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()


def hashcat_exit_message(returncode: int, stopped: bool = False) -> str:
    """Translate only the exit code; never inspect the secret potfile or output."""
    if stopped:
        return "Hashcat остановлен по запросу пользователя"
    messages = {
        0: "Hashcat: пароль восстановлен; результат хранится в локальном potfile",
        1: "Hashcat: словарь исчерпан, пароль не найден",
        2: "Hashcat: выполнение прервано",
        3: "Hashcat: выполнение прервано с контрольной точкой",
        4: "Hashcat: достигнут предел времени выполнения",
        -1: "Hashcat: ошибка выполнения",
    }
    return messages.get(returncode, "Hashcat завершён с кодом %d" % returncode)


def run_hashcat(hash_file: str | Path, wordlist: str | Path,
                state_dir: str | Path, executable: str = "hashcat",
                resume: bool = False, *,
                progress_callback: Callable[[dict], None] | None = None,
                stop_event: threading.Event | None = None,
                progress: Callable[[str], None] | None = None) -> int:
    """Run local Hashcat; preserve the exit-code API and original positional args.

    ``progress(message)`` is an optional simple text callback for a GUI stage/log.
    ``progress_callback`` receives dictionaries on the calling thread. ``event`` is
    stage/status/heartbeat/finished. Stages have a fixed Russian ``message``;
    status can have numeric status, done, total, recovered, hashes_total, rejected,
    rate and eta. All events after launch have elapsed seconds. Finished also has
    returncode and stopped. No raw output, paths, hashes, candidates or device names
    are forwarded. Missing metrics mean unknown, NOT zero. Heartbeats indicate
    only a live process, not completed checks. Hashcat progress units are its own
    work units, not necessarily unique wordlist lines.

    Set ``stop_event`` to terminate and reap Hashcat (kill after a bounded grace
    period). A pre-set stop returns 130 without launching or writing state files;
    otherwise return the actual child exit code. Exceptions/KeyboardInterrupt in
    the caller also clean up the child. Raw stdout/stderr are always suppressed,
    including when called from the CLI without a callback.
    """
    def emit(event, **fields):
        if progress_callback is not None:
            progress_callback({"event": event, **fields})
        if progress is not None and "message" in fields:
            progress(fields["message"])

    if stop_event is not None and stop_event.is_set():
        emit("finished", returncode=130, stopped=True, elapsed=0.0,
             message=hashcat_exit_message(130, stopped=True))
        return 130
    emit("stage", message="Подготовка локального запуска Hashcat")
    binary = shutil.which(str(Path(executable).expanduser()))
    if binary is None:
        raise FileNotFoundError("Hashcat не найден; укажите установленный локальный исполняемый файл")
    state = Path(state_dir).expanduser().resolve()
    command = build_hashcat_command(hash_file, wordlist, state_dir, executable=binary,
                                    resume=resume)
    # Hashcat 6.x machine-readable status. Restore reads the original attack from
    # its session file; runtime reporting flags contain no attack/candidate data.
    command.extend(["--status", "--status-json", "--status-timer", "1"])
    previous_umask = os.umask(0o077)
    try:
        process = subprocess.Popen(command, cwd=str(state), shell=False,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", bufsize=1)
    finally:
        # Do not change the GUI process's global umask throughout a long attack.
        os.umask(previous_umask)
    started = time.monotonic()
    updates = queue.Queue(maxsize=64)
    reader_done = threading.Event()
    reader = threading.Thread(target=_read_status,
                              args=(process.stdout, updates, reader_done),
                              name="hashcat-status", daemon=True)
    stopped = False

    def drain():
        while True:
            try:
                status = updates.get_nowait()
            except queue.Empty:
                return
            if progress_callback is not None:
                progress_callback({**status, "elapsed": time.monotonic() - started})
            if progress is not None:
                message = "Hashcat: получен статус устройства"
                if "done" in status:
                    message += "; выполнено %d из %d единиц работы" % (status["done"], status["total"])
                if "rate" in status:
                    message += "; скорость %.1f H/s" % status["rate"]
                progress(message)

    try:
        reader.start()
        emit("stage", message="Hashcat запущен; ожидание статуса устройства", elapsed=0.0)
        last_heartbeat = started
        while True:
            drain()
            returncode = process.poll()
            if returncode is not None:
                break
            if stop_event is not None and stop_event.is_set():
                stopped = True
                emit("stage", message="Остановка Hashcat", elapsed=time.monotonic() - started)
                returncode = _terminate(process)
                break
            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                emit("heartbeat", message="Hashcat работает; ожидание обновления статуса",
                     elapsed=now - started)
                last_heartbeat = now
            if stop_event is None:
                time.sleep(_POLL_SECONDS)
            else:
                stop_event.wait(_POLL_SECONDS)
        process.wait()
        reader.join(timeout=1.0)
        drain()
        emit("finished", returncode=returncode, stopped=stopped,
             elapsed=time.monotonic() - started,
             message=hashcat_exit_message(returncode, stopped))
        return returncode
    finally:
        try:
            if process.poll() is None:
                _terminate(process)
        finally:
            if reader.is_alive():
                reader.join(timeout=1.0)
            process.stdout.close()
