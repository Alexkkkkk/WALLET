import queue
import time
import pytest
from wallet_recover.bdb import bdb_module
from wallet_recover.runner import RecoveryEngine, check_wordlist, inspect_wallet
from wallet_recover.selftest import fixture_records, PASSWORD


def make_wallet(path):
    db = bdb_module()
    handle = db.DB()
    handle.open(str(path), "main", db.DB_BTREE, db.DB_CREATE)
    for key, value in fixture_records():
        handle.put(key, value)
    handle.close()


def collect(tmp_path, content, stop=False):
    wallet, words = tmp_path / "wallet.dat", tmp_path / "words.txt"
    make_wallet(wallet)
    before = wallet.read_bytes()
    words.write_bytes(content)
    events = queue.Queue()
    engine = RecoveryEngine(wallet, words, jobs=2,
                            sink=lambda kind, *args: events.put((kind, args)))
    if stop:
        engine.stop()
    thread = engine.start()
    thread.join(timeout=15)
    if thread.is_alive():
        engine.stop()
        thread.join(timeout=5)
        pytest.fail("Engine did not finish within 15 seconds")
    assert wallet.read_bytes() == before
    result = []
    while not events.empty():
        result.append(events.get_nowait())
    return result


def test_engine_found(tmp_path):
    events = collect(tmp_path, b"wrong\n" + PASSWORD + b"\n")
    found = [args for event, args in events if event == "found"]
    assert len(found) == 1 and found[0][0] == PASSWORD
    assert not any(event == "failed" for event, args in events)


def test_engine_exhausted(tmp_path):
    events = collect(tmp_path, b"wrong\nno\n")
    finished = [args for event, args in events if event == "finished"]
    assert len(finished) == 1
    assert finished[0][0] == 2 and finished[0][2] is False


def test_stop_before_start(tmp_path):
    events = collect(tmp_path, b"wrong\n" * 10000, stop=True)
    assert any(event == "finished" and args[2] for event, args in events)
    assert not any(event == "found" for event, args in events)


def test_stop_during_recovery(tmp_path):
    wallet, words = tmp_path / "wallet.dat", tmp_path / "words.txt"
    make_wallet(wallet)
    words.write_bytes(b"wrong\n" * 200000)
    events = queue.Queue()
    engine = RecoveryEngine(wallet, words, 2, lambda event, *args: events.put((event, args)))
    thread = engine.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        event, args = events.get(timeout=10)
        if event == "log":
            break
    engine.stop()
    thread.join(timeout=8)
    assert not thread.is_alive()
    rest = []
    while not events.empty():
        rest.append(events.get_nowait())
    assert any(event == "finished" and args[2] for event, args in rest)


def test_engine_error(tmp_path):
    events = queue.Queue()
    engine = RecoveryEngine(tmp_path / "missing", tmp_path / "missing2", 1,
                            lambda event, *args: events.put((event, args)))
    engine.start().join(timeout=5)
    assert not engine.is_running()
    assert events.get_nowait()[0] == "failed"


def test_inspect(tmp_path):
    wallet = tmp_path / "wallet.dat"
    make_wallet(wallet)
    info = inspect_wallet(wallet)
    assert info["usable"] and info["keys"] == 1
    assert info["masters"][0]["method"] == 0


def test_check_wordlist(tmp_path):
    words = tmp_path / "words"
    words.write_bytes(b"a\n\r\n \n")
    count, warnings = check_wordlist(words)
    assert count == 3 and warnings == ["Пустых кандидатов: 1"]
    words.write_bytes(b"")
    with pytest.raises(ValueError):
        check_wordlist(words)


@pytest.mark.parametrize("jobs", [0, -1, 65])
def test_invalid_jobs(jobs):
    with pytest.raises(ValueError):
        RecoveryEngine("wallet", "words", jobs)


def test_engine_stopped_flag():
    engine = RecoveryEngine("wallet", "words", 1)
    assert not engine.stopped()
    engine.stop()
    assert engine.stopped()
