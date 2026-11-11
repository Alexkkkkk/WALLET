"""Live backend contracts: synthetic input only, never invoke a real Hashcat."""
import io
import json
import subprocess
import threading

import pytest

from wallet_recover.ai import hashcat
from wallet_recover.checkpoint import Checkpoint
from wallet_recover import runner


@pytest.fixture
def hashcat_inputs(tmp_path):
    hashes = tmp_path / "synthetic.hash"
    words = tmp_path / "synthetic.words"
    hashes.write_text("$bitcoin$SYNTHETIC-HASH-SECRET\n", encoding="utf-8")
    words.write_text("SYNTHETIC-CANDIDATE-SECRET\n", encoding="utf-8")
    return hashes, words, tmp_path / "private-state"


class FakeProcess:
    def __init__(self, output="", returncode=1, running=False, ignore_terminate=False):
        self.stdout = io.StringIO(output)
        self.returncode = None if running else returncode
        self.terminated = False
        self.killed = False
        self.ignore_terminate = ignore_terminate
        self.wait_calls = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("synthetic-local-binary", timeout)
        return self.returncode


def install_process(monkeypatch, process):
    calls = []
    monkeypatch.setattr(hashcat.shutil, "which", lambda executable: "/local/hashcat-custom")

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(hashcat.subprocess, "Popen", popen)
    return calls


def test_hashcat_safe_machine_status_and_compatible_exit(hashcat_inputs, monkeypatch, capsys):
    output = "\n".join([
        "SYNTHETIC-CANDIDATE-SECRET:$bitcoin$SYNTHETIC-HASH-SECRET",
        "{not-json}",
        json.dumps({"target": "$bitcoin$SYNTHETIC-HASH-SECRET",
                    "candidates": ["SYNTHETIC-CANDIDATE-SECRET"]}),
        json.dumps({"status": 3, "progress": [4, 20], "recovered_hashes": [0, 1],
                    "rejected": 2, "target": "$bitcoin$SYNTHETIC-HASH-SECRET",
                    "session": "SYNTHETIC-CANDIDATE-SECRET",
                    "devices": [{"speed": 8.0, "device_name": "SECRET-DEVICE",
                                 "candidates": ["SYNTHETIC-CANDIDATE-SECRET"]}]}),
    ]) + "\n"
    process = FakeProcess(output)
    calls = install_process(monkeypatch, process)
    seen = []
    caller_thread = threading.get_ident()
    callback_threads = []

    def callback(status):
        seen.append(status)
        callback_threads.append(threading.get_ident())

    code = hashcat.run_hashcat(*hashcat_inputs, executable="/chosen/hashcat",
                              progress_callback=callback)
    assert code == 1
    assert set(callback_threads) == {caller_thread}
    statuses = [event for event in seen if event["event"] == "status"]
    assert len(statuses) == 1
    status = statuses[0]
    assert status == {"event": "status", "status": 3, "done": 4, "total": 20,
                      "recovered": 0, "hashes_total": 1, "rejected": 2,
                      "rate": 8.0, "eta": 2.0, "elapsed": status["elapsed"]}
    assert seen[-1]["event"] == "finished" and not seen[-1]["stopped"]
    safe_output = json.dumps(seen)
    assert "SECRET" not in safe_output and "$bitcoin$" not in safe_output
    assert capsys.readouterr() == ("", "")
    command, options = calls[0]
    assert command[0] == "/local/hashcat-custom"
    assert command[-4:] == ["--status", "--status-json", "--status-timer", "1"]
    assert options["shell"] is False
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stdout"] == subprocess.PIPE and options["stderr"] == subprocess.STDOUT
    assert options["cwd"] == str(hashcat_inputs[2].resolve())
    assert process.stdout.closed and process.wait_calls


@pytest.mark.parametrize("payload", [
    "not-json", "[]", "null", '"plaintext"',
    '{"target":"$bitcoin$secret","candidates":["secret"]}',
    '{"status":"secret","progress":["secret",20],"rejected":true}',
    '{"devices":[{"speed":"secret"},{"speed":NaN},{"speed":Infinity}]}',
    '{"progress":[true,10],"recovered_hashes":[10,2]}',
    '{"status":99,"progress":[-1,2],"rejected":-1}',
])
def test_hashcat_untrusted_status_is_discarded(payload):
    assert hashcat._safe_status(payload) is None


def test_hashcat_numeric_strings_are_not_coerced():
    assert hashcat._safe_status('{"progress":["123456",20],"status":3}') == {
        "event": "status", "status": 3,
    }


def test_hashcat_stop_terminates_and_reaps(hashcat_inputs, monkeypatch):
    process = FakeProcess(running=True)
    install_process(monkeypatch, process)
    stop_event = threading.Event()
    seen = []

    def callback(event):
        seen.append(event)
        if event.get("message", "").startswith("Hashcat запущен"):
            stop_event.set()

    assert hashcat.run_hashcat(*hashcat_inputs, progress_callback=callback,
                              stop_event=stop_event) == -15
    assert process.terminated and not process.killed
    assert process.wait_calls and process.stdout.closed
    assert seen[-1]["stopped"]
    assert any(event.get("message") == "Остановка Hashcat" for event in seen)


def test_hashcat_stop_escalates_to_kill(hashcat_inputs, monkeypatch):
    process = FakeProcess(running=True, ignore_terminate=True)
    install_process(monkeypatch, process)
    stop_event = threading.Event()

    def callback(event):
        if event.get("message", "").startswith("Hashcat запущен"):
            stop_event.set()

    assert hashcat.run_hashcat(*hashcat_inputs, progress_callback=callback,
                              stop_event=stop_event) == -9
    assert process.terminated and process.killed
    assert hashcat._STOP_TIMEOUT_SECONDS in process.wait_calls


def test_hashcat_pre_stopped_does_not_launch_or_create_state(hashcat_inputs, monkeypatch):
    stop_event = threading.Event()
    stop_event.set()
    seen = []
    monkeypatch.setattr(hashcat.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not launch"))
    assert hashcat.run_hashcat(*hashcat_inputs, stop_event=stop_event,
                              progress_callback=seen.append) == 130
    assert not hashcat_inputs[2].exists()
    assert seen == [{"event": "finished", "returncode": 130, "stopped": True,
                     "elapsed": 0.0,
                     "message": "Hashcat остановлен по запросу пользователя"}]


def test_hashcat_heartbeat_means_process_alive_not_fake_progress(hashcat_inputs, monkeypatch):
    process = FakeProcess("raw secret output\n", running=True)
    install_process(monkeypatch, process)
    monkeypatch.setattr(hashcat, "_HEARTBEAT_SECONDS", 0.0)
    stop_event = threading.Event()
    seen = []

    def callback(event):
        seen.append(event)
        if event["event"] == "heartbeat":
            stop_event.set()

    hashcat.run_hashcat(*hashcat_inputs, stop_event=stop_event, progress_callback=callback)
    heartbeats = [event for event in seen if event["event"] == "heartbeat"]
    assert heartbeats
    assert all("done" not in event and "total" not in event for event in heartbeats)
    assert "secret" not in json.dumps(seen)


def test_hashcat_callback_exception_cleans_up_child(hashcat_inputs, monkeypatch):
    process = FakeProcess(running=True)
    install_process(monkeypatch, process)

    def callback(event):
        if event.get("message", "").startswith("Hashcat запущен"):
            raise RuntimeError("synthetic callback failure")

    with pytest.raises(RuntimeError, match="synthetic callback failure"):
        hashcat.run_hashcat(*hashcat_inputs, progress_callback=callback)
    assert process.terminated and process.stdout.closed


def test_hashcat_no_callback_preserves_old_positional_api(hashcat_inputs, monkeypatch):
    process = FakeProcess("$bitcoin$secret:plaintext\n", returncode=0)
    calls = install_process(monkeypatch, process)
    assert hashcat.run_hashcat(*hashcat_inputs, "hashcat", True) == 0
    assert "--restore" in calls[0][0]
    assert process.stdout.closed


def test_hashcat_executable_not_found_is_clear(hashcat_inputs, monkeypatch):
    monkeypatch.setattr(hashcat.shutil, "which", lambda executable: None)
    with pytest.raises(FileNotFoundError, match="Hashcat не найден"):
        hashcat.run_hashcat(*hashcat_inputs, executable="missing-local-hashcat")


def test_hashcat_oversized_output_is_discarded(hashcat_inputs, monkeypatch):
    long_line = "private" * 20000 + '{"progress":[1,2]}\n'
    process = FakeProcess(long_line + '{"status":3}\n')
    install_process(monkeypatch, process)
    seen = []
    hashcat.run_hashcat(*hashcat_inputs, progress_callback=seen.append)
    assert [event["status"] for event in seen if event["event"] == "status"] == [3]
    assert not any("done" in event for event in seen)


class ReadyBatch:
    def __init__(self, size):
        self.size = size

    def ready(self):
        return True

    def get(self):
        return self.size, None


class SyntheticPool:
    def __init__(self):
        self.candidates = []
        self.terminated = False
        self.joined = False

    def apply_async(self, function, args):
        self.candidates.extend(args[0])
        return ReadyBatch(len(args[0]))

    def terminate(self):
        self.terminated = True

    def join(self):
        self.joined = True


@pytest.fixture
def synthetic_engine(tmp_path, monkeypatch):
    wallet, words, cp = tmp_path / "wallet", tmp_path / "words", tmp_path / "state.cp"
    wallet.write_bytes(b"SYNTHETIC-WALLET-DATA")
    words.write_bytes(b"a\r\n\xff\n\nlast\r")
    events = []
    pool = SyntheticPool()
    monkeypatch.setattr(runner, "load_wallet", lambda path: ([], []))
    monkeypatch.setattr(runner, "Decryptor", lambda masters, keys: None)
    context = type("Context", (), {"Pool": lambda self, *args: pool})()
    monkeypatch.setattr(runner.mp, "get_context", lambda method: context)
    return wallet, words, cp, events, pool


def test_runner_stages_precede_expensive_work(synthetic_engine, monkeypatch):
    wallet, words, cp, events, pool = synthetic_engine
    original_fingerprint = runner.fingerprint
    fingerprints = []

    def fingerprint(path):
        assert events and events[-1][0] == "stage" and "отпечатка" in events[-1][1][0]
        fingerprints.append(path)
        return original_fingerprint(path)

    monkeypatch.setattr(runner, "fingerprint", fingerprint)
    engine = runner.RecoveryEngine(wallet, words, jobs=1,
                                  sink=lambda kind, *args: events.append((kind, args)),
                                  checkpoint_path=cp)
    engine._run()
    assert fingerprints == [wallet, words]
    assert not any(kind == "failed" for kind, args in events)
    stages = [args[0] for kind, args in events if kind == "stage"]
    for fragment in ("кошелька", "словаря", "Подсчёт", "контрольной точки",
                     "Berkeley DB", "шифрования", "Сохранение", "Запуск", "Проверка кандидатов"):
        assert any(fragment in stage for stage in stages)
    assert events[-1][0] == "finished" and events[-1][1][0] == 4
    saved = Checkpoint.load(cp)
    assert saved.finished and saved.consumed == 4
    assert saved.bytes_offset == words.stat().st_size
    assert pool.candidates == [b"a", b"\xff", b"", b"last\r"]
    assert pool.terminated and pool.joined
    logs = [args[0] for kind, args in events if kind == "log"]
    assert any("Контрольная точка сохранена" in log and "байт" in log for log in logs)
    assert not any("SYNTHETIC" in message for message in stages + logs)


def test_runner_resume_stages_and_exact_offset(synthetic_engine):
    wallet, words, cp, events, pool = synthetic_engine
    saved = Checkpoint(wordlist=str(words.resolve()), wordlist_size=words.stat().st_size,
                       wallet_fingerprint=runner.fingerprint(wallet), consumed=2,
                       bytes_offset=5, elapsed_s=7.0, rate_ps=0.0, total=4,
                       wordlist_fingerprint=runner.fingerprint(words))
    saved.save(cp)
    engine = runner.RecoveryEngine(wallet, words, jobs=1,
                                  sink=lambda kind, *args: events.append((kind, args)),
                                  checkpoint_path=cp, resume=True)
    engine._run()
    assert not any(kind == "failed" for kind, args in events)
    stages = [args[0] for kind, args in events if kind == "stage"]
    assert "Загрузка и проверка контрольной точки" in stages
    assert "Проверка точного смещения словаря для продолжения" in stages
    assert any(kind == "stage" and "кандидатов" in args[0] for kind, args in events)
    assert pool.candidates == [b"", b"last\r"]
    assert Checkpoint.load(cp).bytes_offset == words.stat().st_size


def test_runner_stop_after_preparation_does_not_start_workers(synthetic_engine):
    wallet, words, cp, events, pool = synthetic_engine
    engine = None

    def sink(kind, *args):
        events.append((kind, args))
        if kind == "stage" and args[0] == "Чтение кошелька Berkeley DB (BDB)":
            engine.stop()

    engine = runner.RecoveryEngine(wallet, words, jobs=1, sink=sink, checkpoint_path=cp)
    engine._run()
    assert events[-1][0] == "finished" and events[-1][1][2]
    assert pool.candidates == [] and not pool.terminated
    saved = Checkpoint.load(cp)
    assert saved.consumed == saved.bytes_offset == 0 and not saved.finished
    assert not any(kind == "failed" for kind, args in events)
