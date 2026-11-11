"""Release metadata must not confuse runtime versions with Windows versions."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from wallet_recover import __version__

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
NOTES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NOTES)


def test_release_notes_distinguish_windows_and_python_packages():
    notes = NOTES.release_notes(__version__)
    assert "WALLET-v%s-windows10-11-x64.zip" % __version__ in notes
    assert "Windows 7 SP1" in notes
    assert "НЕ подходит" in notes
    assert "готовой проверенной EXE-сборки пока нет" in notes
    assert "`.whl`" in notes and "НЕ готовые приложения Windows" in notes
    assert "Windows Server 2022" in notes
    assert ".zip.sha256" in notes


def test_release_notes_derive_links_for_future_versions():
    notes = NOTES.release_notes("1.2.3")
    assert "/download/v1.2.3/WALLET-v1.2.3-windows10-11-x64.zip" in notes
    assert "/blob/v1.2.3/README.md" in notes


def test_release_notes_reject_mismatched_tag():
    env = dict(os.environ, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v999.0.0")
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "release_notes.py")],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "does not match package version" in result.stderr


def test_release_notes_script_runs_without_installed_project(tmp_path):
    env = dict(os.environ, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v" + __version__)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "release_notes.py")],
                            cwd=str(tmp_path), env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr
    assert "WALLET-v%s-windows10-11-x64.zip" % __version__ in result.stdout
