#!/usr/bin/env python3
"""Verify a frozen portable build; only public synthetic fixtures are used."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wallet_recover import __version__
from wallet_recover.bdb import bdb_module
from wallet_recover.selftest import fixture_records, PASSWORD


def clean_environment():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("CONDA", "PYTHON", "OPENAI")):
            env.pop(key, None)
    # No source imports or native DLL fallback from the build environment.
    windows = env.get("SystemRoot", r"C:\Windows")
    env["PATH"] = os.pathsep.join([windows, str(Path(windows) / "System32")])
    env["PYTHONIOENCODING"] = "cp1252:strict"
    return env


def has_wallet_window(exe):
    """Check for a visible WALLET window owned by this executable, not just uptime."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    found = []

    @callback_type
    def visit(hwnd, _):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        if user32.IsWindowVisible(hwnd) and title.value.startswith("WALLET"):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = kernel32.OpenProcess(0x1000, False, pid.value)
            if handle:
                try:
                    image = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(image))
                    if kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                        if os.path.normcase(image.value) == os.path.normcase(str(exe)):
                            found.append(title.value)
                finally:
                    kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows(visit, 0)
    return bool(found)


def main():
    exe = Path(sys.argv[1]).resolve(strict=True)
    env = clean_environment()
    with tempfile.TemporaryDirectory(prefix="wallet-bundle-check-") as directory:
        work = Path(directory)

        def run(*args):
            result = subprocess.run([str(exe)] + list(args), cwd=str(work), env=env,
                                    capture_output=True, timeout=120)
            print(result.stdout.decode("utf-8", errors="backslashreplace"))
            print(result.stderr.decode("utf-8", errors="backslashreplace"))
            if result.returncode != 0:
                raise RuntimeError("EXE failed with code %s: %s" % (result.returncode, args))
            return result.stdout

        assert __version__.encode("ascii") in run("--version")
        run("--selftest")
        candidates = work / "ai-candidates.txt"
        run("--ai-generate", str(candidates), "--memory", "build-check",
            "--ai-provider", "offline", "--ai-limit", "20")
        generated = candidates.read_bytes()
        assert generated and len(generated.splitlines()) <= 20

        wallet = work / "synthetic-backup.dat"
        db = bdb_module().DB()
        db.open(str(wallet), "main", bdb_module().DB_BTREE, bdb_module().DB_CREATE)
        try:
            for key, value in fixture_records():
                db.put(key, value)
        finally:
            db.close()
        before = wallet.read_bytes()
        words = work / "synthetic-words.txt"
        words.write_bytes(b"wrong\nhunter2\n" + PASSWORD + b"\n")
        output = work / "found.bin"
        run("--wallet", str(wallet), "--wordlist", str(words), "--jobs", "2",
            "--save", str(output))
        assert output.read_bytes() == PASSWORD
        assert wallet.read_bytes() == before, "Wallet input was modified"

        if os.name == "nt":
            gui = subprocess.Popen([str(exe)], cwd=str(work), env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if gui.poll() is not None:
                        stdout, stderr = gui.communicate()
                        raise RuntimeError("GUI exited early: %r %r" % (stdout, stderr))
                    if has_wallet_window(exe):
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError("No visible WALLET GUI window appeared")
            finally:
                subprocess.run(["taskkill", "/PID", str(gui.pid), "/T", "/F"],
                               env=env, capture_output=True, timeout=15)
                gui.communicate(timeout=15)
    print("Portable EXE checks passed: version, self-test, AI, BDB recovery/jobs=2, GUI")


if __name__ == "__main__":
    main()
