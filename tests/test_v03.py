"""v0.3 contracts: synthetic fixtures only, no real wallets or secrets."""
from dataclasses import replace
import hashlib
import io
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import time

import pytest

from wallet_recover.checkpoint import Checkpoint
from wallet_recover.crypto import Decryptor, public_key
from wallet_recover.errors import BDBError, CheckpointError, KDFError, VerifyError, WalletError
from wallet_recover.plan import estimate_entropy_bits, time_to_crack, wallet_plan
from wallet_recover.progress import Progress
from wallet_recover.runner import RecoveryEngine, _CommitFrontier, _run_batch, _init_worker
from wallet_recover.selftest import fixture_records, PASSWORD
from wallet_recover.serialize import parse_records
from wallet_recover.wordlist import fingerprint, iter_lines, iter_unique, export_unique
from test_runner import make_wallet

ROOT = Path(__file__).resolve().parents[1]


def inputs(tmp_path, data=b"wrong\nno\n"):
    wallet, words, cp = tmp_path / "wallet.dat", tmp_path / "words", tmp_path / "state.cp"
    make_wallet(wallet)
    words.write_bytes(data)
    return wallet, words, cp


def run_engine(wallet, words, cp, resume=False, sink=None):
    events = []
    def callback(event, *args):
        events.append((event, args))
        if sink:
            sink(event, *args)
    engine = RecoveryEngine(wallet, words, 2, callback, checkpoint_path=cp, resume=resume)
    thread = engine.start()
    thread.join(15)
    if thread.is_alive():
        engine.stop()
        thread.join(5)
        pytest.fail("Engine stalled")
    return engine, events


def test_frontier_never_skips_unfinished_batch():
    f = _CommitFrontier(10, 50)
    f.acknowledge(2, 3, 110)
    f.acknowledge(1, 4, 90)
    assert (f.consumed, f.offset, f.sequence) == (10, 50, 0)
    f.acknowledge(0, 5, 75)
    assert (f.consumed, f.offset, f.sequence) == (22, 110, 3)
    assert not f.pending


def test_worker_counts_only_attempted_candidates():
    masters, keys = parse_records(fixture_records())
    _init_worker(masters, keys)
    assert _run_batch([b"wrong", PASSWORD, b"not-tested"]) == (2, PASSWORD)


def test_decryptor_backends_agree(monkeypatch):
    import wallet_recover.crypto as crypto
    for compressed in (True, False):
        secret = (1).to_bytes(32, "big")
        fast = public_key(secret, compressed)
        backend = crypto._FastPublicKey
        monkeypatch.setattr(crypto, "_FastPublicKey", None)
        assert public_key(secret, compressed) == fast
        masters, keys = parse_records(fixture_records(compressed=compressed))
        decryptor = Decryptor(masters, keys)
        assert decryptor.try_password(PASSWORD)
        assert not decryptor.try_password(b"wrong")
        monkeypatch.setattr(crypto, "_FastPublicKey", backend)


def test_errors_and_validation():
    masters, keys = parse_records(fixture_records())
    assert issubclass(KDFError, WalletError) and issubclass(WalletError, ValueError)
    with pytest.raises(KDFError):
        Decryptor([], keys)
    with pytest.raises(VerifyError):
        Decryptor(masters, [])
    with pytest.raises(KDFError):
        Decryptor([replace(masters[0], method=1)], keys)
    with pytest.raises(BDBError):
        from wallet_recover.bdb import load_wallet
        load_wallet("/missing/synthetic/wallet.dat")


def test_wordlist_exact_bytes_dedup_and_cr(tmp_path):
    words = tmp_path / "words"
    words.write_bytes(b"a\na\n\r\n \r\n\xff\nlast\r")
    assert list(iter_lines(words)) == [b"a", b"a", b"", b" ", b"\xff", b"last\r"]
    assert list(iter_unique(words)) == [b"a", b"", b" ", b"\xff", b"last\r"]
    out = tmp_path / "unique"
    assert export_unique(words, out) == 5
    assert list(iter_lines(out)) == list(iter_unique(words))
    with pytest.raises(FileExistsError):
        export_unique(words, out)
    if os.name == "posix":
        assert out.stat().st_mode & 0o777 == 0o600


def test_fingerprint_covers_middle(tmp_path):
    p = tmp_path / "large"
    p.write_bytes(b"a" * 20000)
    original = fingerprint(p)
    with p.open("r+b") as f:
        f.seek(10000)
        f.write(b"b")
    assert fingerprint(p) != original
    assert fingerprint(p) == hashlib.sha256(p.read_bytes()).hexdigest()


def test_checkpoint_atomic_permissions_and_failed_replace(tmp_path, monkeypatch):
    cp = Checkpoint("words", 10, "0" * 64, 0, 0, 1, 2,
                    wordlist_fingerprint="1" * 64, total=3)
    path = tmp_path / "checkpoint"
    cp.save(path)
    assert Checkpoint.load(path) == cp
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    original = path.read_bytes()
    def fail(*args):
        raise OSError("synthetic replace error")
    monkeypatch.setattr("wallet_recover.checkpoint.os.replace", fail)
    with pytest.raises(OSError):
        replace(cp, elapsed_s=3).save(path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("field,value", [("consumed", -1), ("consumed", True),
    ("bytes_offset", 11), ("elapsed_s", float("nan")), ("version", 9),
    ("wallet_fingerprint", "short"), ("finished", "yes")])
def test_invalid_checkpoint_rejected(tmp_path, field, value):
    cp = Checkpoint("words", 10, "0" * 64, 0, 0, 1, 2,
                    wordlist_fingerprint="1" * 64, total=3)
    with pytest.raises(CheckpointError):
        replace(cp, **{field: value}).save(tmp_path / "cp")


def test_exhaustion_checkpoint_and_resume_no_work(tmp_path):
    wallet, words, cp = inputs(tmp_path, b"wrong\n\nlast\r")
    engine, events = run_engine(wallet, words, cp)
    assert not [e for e in events if e[0] == "failed"]
    saved = Checkpoint.load(cp)
    assert saved.finished and saved.consumed == 3
    assert saved.bytes_offset == words.stat().st_size
    _, resumed = run_engine(wallet, words, cp, True)
    assert any(e == "finished" and a[0] == 3 for e, a in resumed)
    assert not any(e == "failed" for e, a in resumed)
    assert b"wrong" not in cp.read_bytes()
    assert b"ciphertext" not in cp.read_bytes()


def test_stop_resume_recovers_password_without_skipping(tmp_path):
    wallet, words, cp = inputs(tmp_path, b"wrong\n" * 10000 + PASSWORD + b"\n")
    engine = None
    def stop(event, *args):
        if event == "progress":
            engine.stop()
    events = []
    engine = RecoveryEngine(wallet, words, 2,
        lambda event, *args: (events.append((event, args)), stop(event, *args)),
        checkpoint_path=cp)
    engine.start().join(15)
    assert not engine.is_running()
    saved = Checkpoint.load(cp)
    assert 0 < saved.consumed < 10001 and not saved.finished
    assert saved.bytes_offset == saved.consumed * len(b"wrong\n")
    _, resumed = run_engine(wallet, words, cp, True)
    assert any(e == "found" and a[0] == PASSWORD for e, a in resumed)
    assert not any(e == "failed" for e, a in resumed)
    assert PASSWORD not in cp.read_bytes()


def test_found_checkpoint_does_not_lose_password(tmp_path):
    wallet, words, cp = inputs(tmp_path, b"wrong\n" + PASSWORD + b"\n")
    _, events = run_engine(wallet, words, cp)
    assert any(e == "found" for e, a in events)
    saved = Checkpoint.load(cp)
    assert not saved.finished and saved.consumed == 0
    _, events = run_engine(wallet, words, cp, True)
    assert any(e == "found" and a[0] == PASSWORD for e, a in events)


@pytest.mark.parametrize("which", ["wallet", "words", "offset", "corrupt", "line_count"])
def test_resume_refuses_changed_or_corrupt_inputs(tmp_path, which):
    wallet, words, cp = inputs(tmp_path, b"wrong\n" * 3000)
    run_engine(wallet, words, cp)
    original_cp = cp.read_bytes()
    if which == "wallet":
        wallet.write_bytes(wallet.read_bytes() + b"x")
    elif which == "words":
        # Same-size mutation, deliberately away from first/last samples.
        with words.open("r+b") as f:
            f.seek(9000)
            f.write(b"X")
    elif which == "corrupt":
        cp.write_text("{not json")
    else:
        data = json.loads(cp.read_text())
        if which == "offset":
            data["bytes_offset"] -= 1
        else:
            data["consumed"] = data["total"] + 1
        cp.write_text(json.dumps(data))
    before = cp.read_bytes()
    _, events = run_engine(wallet, words, cp, True)
    assert any(e == "failed" and "CheckpointError" in a[0] for e, a in events)
    assert cp.read_bytes() == before


def test_checkpoint_protects_inputs_and_existing_files(tmp_path):
    wallet, words, cp = inputs(tmp_path)
    with pytest.raises(CheckpointError):
        RecoveryEngine(wallet, words, checkpoint_path=wallet)
    if os.name == "posix":
        alias = tmp_path / "alias"
        os.link(words, alias)
        with pytest.raises(CheckpointError):
            RecoveryEngine(wallet, words, checkpoint_path=alias)
    cp.write_text("must not overwrite")
    _, events = run_engine(wallet, words, cp)
    assert any(e == "failed" for e, a in events)
    assert cp.read_text() == "must not overwrite"


def test_progress_redirect_and_clamp():
    stream = io.StringIO()
    p = Progress(10, stream=stream)
    p.update(5, rate=2.5, eta=2)
    p.update(12, rate=2.5, eta=0)
    p.close()
    text = stream.getvalue()
    assert "ETA" in text and "p/s" in text and "10/10" in text
    assert "\r" not in text
    assert p._fmt(float("inf")) == "unknown"
    assert p._fmt(3661) == "1h01m"
    Progress(0, stream=io.StringIO()).close()


def test_entropy_models_and_existing_plan_contract(tmp_path):
    assert estimate_entropy_bits(10, 6) == pytest.approx(math.log2(1000000))
    assert time_to_crack(10, 2) == 256
    assert math.isinf(time_to_crack(1000000, 1))
    assert math.isinf(time_to_crack(1, 0))
    for args in [(0, 5), (26, -1), (26, 1.5)]:
        with pytest.raises(ValueError):
            estimate_entropy_bits(*args)
    wallet, _, _ = inputs(tmp_path)
    plan = wallet_plan(wallet, jobs=2, speed_ps=2350)
    assert plan.speed_ps == 2350 and plan.summary()
    assert "12 букв/цифр" in plan.report
    assert "2.18e+10 лет" in plan.report
    assert "11300" in plan.report


def test_cli_dedup_and_resume_flag_validation(tmp_path):
    _, words, _ = inputs(tmp_path, b"a\na\n")
    out = tmp_path / "unique"
    r = subprocess.run([sys.executable, "-m", "wallet_recover", "--wordlist", str(words),
                        "--deduplicate", str(out)], cwd=ROOT, capture_output=True, timeout=10)
    assert r.returncode == 0 and out.read_bytes() == b"a\n"
    r = subprocess.run([sys.executable, "-m", "wallet_recover", "--resume"],
                       cwd=ROOT, capture_output=True, timeout=10)
    assert r.returncode == 2 and b"--checkpoint" in r.stderr


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGINT subprocess test")
def test_cli_sigint_checkpoint_and_successful_resume(tmp_path):
    wallet, words, cp = inputs(tmp_path, b"wrong\n" * 6000 + PASSWORD + b"\n")
    output = tmp_path / "result.bin"
    command = [sys.executable, "-m", "wallet_recover", "--wallet", str(wallet),
               "--wordlist", str(words), "--jobs", "2", "--checkpoint", str(cp),
               "--save", str(output)]
    p = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not cp.exists():
            time.sleep(0.01)
        assert cp.exists()
        p.send_signal(signal.SIGINT)
        stdout, stderr = p.communicate(timeout=15)
        assert p.returncode == 130, (stdout, stderr)
        cpdata = Checkpoint.load(cp)
        assert not cpdata.finished
        resumed = subprocess.run(command + ["--resume"], cwd=ROOT,
                                 capture_output=True, timeout=30)
        assert resumed.returncode == 0, (resumed.stdout, resumed.stderr)
        assert output.read_bytes() == PASSWORD
        assert PASSWORD not in resumed.stdout
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
