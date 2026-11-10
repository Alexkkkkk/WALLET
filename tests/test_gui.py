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
