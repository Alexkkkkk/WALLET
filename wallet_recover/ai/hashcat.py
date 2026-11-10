"""Optional, strictly local Hashcat adapter for legacy Bitcoin wallet hashes.

This module never downloads a binary/model, makes a network request, or uploads any
wallet/hash/candidate. Hash extraction is intentionally a separate user-controlled
step using a trusted local bitcoin2john.py installation.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Sequence

HASHCAT_MODE_BITCOIN_WALLET = "11300"


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


def run_hashcat(hash_file: str | Path, wordlist: str | Path,
                state_dir: str | Path, executable: str = "hashcat",
                resume: bool = False) -> int:
    """Run installed Hashcat locally; return its exit code without printing secrets."""
    binary = shutil.which(executable)
    if binary is None:
        raise FileNotFoundError("Hashcat не найден в PATH; установите и запустите его локально")
    state = Path(state_dir).expanduser().resolve()
    command = build_hashcat_command(hash_file, wordlist, state_dir, executable=binary,
                                    resume=resume)
    previous_umask = os.umask(0o077)
    try:
        # Hashcat may print status/progress but not the recovered plaintext in attack mode.
        # Keep its output attached to the local terminal and invoke without a shell.
        result = subprocess.run(command, check=False, cwd=str(state))
    finally:
        os.umask(previous_umask)
    return result.returncode
