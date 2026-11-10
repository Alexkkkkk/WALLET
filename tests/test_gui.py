"""Run with xvfb-run -a python -m pytest tests/test_gui.py on Linux."""
import os
import tkinter as tk
import pytest
from wallet_recover.gui import Application

pytestmark = pytest.mark.skipif(os.name != "nt" and not os.environ.get("DISPLAY"),
                                reason="A display or Xvfb is required")


@pytest.fixture
def gui():
    root = tk.Tk()
    app = Application(root)
    root.update()
    yield app
    try:
        root.destroy()
    except tk.TclError:
        pass


def test_found_password_not_in_log(gui):
    gui.set_busy(True, recovery=True)
    gui.sink("found", b"synthetic-only-secret", 3, 0.5)
    gui.poll()
    assert gui.password == b"synthetic-only-secret"
    assert "synthetic-only-secret" not in gui.log.get("1.0", "end")
    assert str(gui.save_btn["state"]) == "normal"
    assert not gui.busy


def test_inspection_and_failure(gui):
    gui.set_busy(True)
    gui.sink("inspection", {"masters": [{"id": 1, "method": 0, "iterations": 25}],
                            "keys": 1, "usable": True, "problems": []})
    gui.poll()
    assert not gui.busy
    assert "пригоден" in gui.status.get()
    gui.sink("failed", "synthetic failure")
    gui.poll()
    assert "synthetic failure" in gui.log.get("1.0", "end")


def test_missing_authorization_blocks_start(gui, monkeypatch):
    messages = []
    monkeypatch.setattr("wallet_recover.gui.messagebox.showwarning",
                        lambda *args, **kwargs: messages.append(args))
    gui.start()
    assert messages and gui.engine is None and not gui.busy


def test_save_exact_bytes_and_refuse_overwrite(gui, tmp_path, monkeypatch):
    output = tmp_path / "password.bin"
    gui.password = b"\xff test \x00"
    monkeypatch.setattr("wallet_recover.gui.messagebox.askokcancel", lambda *a, **kw: True)
    monkeypatch.setattr("wallet_recover.gui.filedialog.asksaveasfilename", lambda **kw: str(output))
    errors = []
    monkeypatch.setattr("wallet_recover.gui.messagebox.showerror", lambda *a, **kw: errors.append(a))
    gui.save()
    assert output.read_bytes() == gui.password
    gui.password = b"overwrite"
    gui.save()
    assert errors and output.read_bytes() == b"\xff test \x00"


def test_new_backend_controls_exist(gui):
    assert gui.words_btn["text"] == "Проверить словарь"
    assert gui.ai_btn["text"] == "Создать AI-словарь…"
    assert gui.hashcat_btn["text"] == "Запустить Hashcat"
    assert gui.provider.get() == "offline"
    assert gui.state_dir.get() == ".wallet-recover-hashcat"
    assert gui.checkpoint.get() == "" and not gui.resume_cpu.get()


def _settle(gui, timeout=5.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        gui.root.update()
        if not gui.busy:
            break
        time.sleep(0.01)
    gui.poll()


def test_check_words_reports_count_and_warnings(gui, tmp_path):
    words = tmp_path / "words"
    words.write_bytes(b"a\nb\n\n")
    gui.words.set(str(words))
    gui.check_words()
    _settle(gui)
    assert not gui.busy
    assert "Кандидатов в словаре: 3" in gui.log.get("1.0", "end")
    assert "Пустых кандидатов: 1" in gui.log.get("1.0", "end")


def test_check_words_missing_file(gui, tmp_path, ):
    gui.words.set(str(tmp_path / "missing"))
    gui.check_words()
    _settle(gui)
    assert "Ошибка" in gui.log.get("1.0", "end")


def test_progress_shows_total_and_percent(gui):
    gui.sink("total", 10)
    gui.poll()
    gui.sink("progress", 5, 2.0)
    gui.poll()
    assert "5 из 10" in gui.status.get()
    assert "50.0%" in gui.status.get()


def test_progress_v03_eta_and_determinate_bar(gui):
    gui.sink("total", 10)
    gui.sink("progress", 5, 2.5, 2.0, 4.0)
    gui.poll()
    assert "ETA 2s" in gui.status.get()
    assert str(gui.bar["mode"]) == "determinate"
    assert gui.bar["value"] == 5


def test_resume_requires_checkpoint_in_gui(gui, tmp_path, monkeypatch):
    from test_runner import make_wallet
    wallet, words = tmp_path / "wallet.dat", tmp_path / "words.txt"
    make_wallet(wallet)
    words.write_bytes(b"wrong\n")
    gui.wallet.set(str(wallet))
    gui.words.set(str(words))
    gui.authorized.set(True)
    gui.resume_cpu.set(True)
    errors = []
    monkeypatch.setattr("wallet_recover.gui.messagebox.showerror", lambda *a, **k: errors.append(a))
    gui.start()
    assert errors and gui.engine is None and not gui.busy


def test_generate_ai_requires_input(gui, monkeypatch):
    messages = []
    monkeypatch.setattr("wallet_recover.gui.messagebox.showerror",
                        lambda *a, **k: messages.append(a))
    gui.generate_ai()
    assert messages


def test_generate_ai_openai_needs_consent(gui, monkeypatch):
    prompts = []
    monkeypatch.setattr("wallet_recover.gui.messagebox.askokcancel",
                        lambda *a, **k: prompts.append(a) or False)
    gui.memory.set("воспоминание")
    gui.provider.set("openai")
    gui.generate_ai()
    assert prompts  # consent declined; nothing ran


def test_generate_ai_refuses_existing_file(gui, tmp_path, monkeypatch):
    existing = tmp_path / "exists.txt"
    existing.write_text("keep")
    gui.memory.set("воспоминание")
    monkeypatch.setattr("wallet_recover.gui.messagebox.askokcancel", lambda *a, **kw: True)
    monkeypatch.setattr("wallet_recover.gui.filedialog.asksaveasfilename",
                        lambda **kw: str(existing))
    errors = []
    monkeypatch.setattr("wallet_recover.gui.messagebox.showerror",
                        lambda *a, **k: errors.append(a))
    gui.generate_ai()
    assert errors and existing.read_text() == "keep"
    assert not gui.busy


def test_hashcat_requires_files_and_consent(gui, tmp_path, monkeypatch):
    errors, warnings = [], []
    monkeypatch.setattr("wallet_recover.gui.messagebox.showerror",
                        lambda *a, **k: errors.append(a))
    monkeypatch.setattr("wallet_recover.gui.messagebox.showwarning",
                        lambda *a, **k: warnings.append(a))
    gui.hash.set(str(tmp_path / "nohash"))
    gui.run_hashcat_action()
    assert errors  # missing files blocked first
    hash_file = tmp_path / "hash.txt"
    words = tmp_path / "words.txt"
    hash_file.write_text("hash")
    words.write_text("x\n")
    warnings.clear()
    gui.hash.set(str(hash_file))
    gui.words.set(str(words))
    gui.run_hashcat_action()
    assert warnings  # missing consent blocked


def test_hashcat_action_runs_and_reports(gui, tmp_path, monkeypatch):
    import shutil
    if shutil.which("hashcat") is None:
        pytest.skip("hashcat not installed")
    hash_file = tmp_path / "hash.txt"
    words = tmp_path / "words.txt"
    hash_file.write_text("hash")
    words.write_text("x\n")
    gui.hash.set(str(hash_file))
    gui.words.set(str(words))
    gui.hashcat_consent.set(True)
    gui.hashcat_btn.configure(state="normal")
    gui.run_hashcat_action()
    _settle(gui)
    assert "Hashcat" in gui.log.get("1.0", "end")
