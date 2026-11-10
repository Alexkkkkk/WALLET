"""Versioned atomic metadata only. No passwords, keys or candidate bytes."""
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile

from .errors import CheckpointError


@dataclass
class Checkpoint:
    wordlist: str
    wordlist_size: int  # bytes, not candidate count
    wallet_fingerprint: str
    consumed: int
    bytes_offset: int
    elapsed_s: float
    rate_ps: float
    finished: bool = False  # True only for an exhausted wordlist
    wordlist_fingerprint: str = ""
    total: int = 0
    version: int = 1

    def validate(self):
        if type(self.version) is not int or self.version != 1:
            raise CheckpointError("Unsupported checkpoint version")
        for name in ("wordlist_size", "consumed", "bytes_offset", "total"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise CheckpointError("Invalid checkpoint " + name)
        if (self.consumed > self.total or self.bytes_offset > self.wordlist_size
                or (self.finished and self.consumed != self.total)):
            raise CheckpointError("Checkpoint bounds are inconsistent")
        for name in ("elapsed_s", "rate_ps"):
            value = getattr(self, name)
            if (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
                raise CheckpointError("Invalid checkpoint " + name)
        if type(self.finished) is not bool or not isinstance(self.wordlist, str):
            raise CheckpointError("Invalid checkpoint metadata")
        for value in (self.wallet_fingerprint, self.wordlist_fingerprint):
            if (not isinstance(value, str) or len(value) != 64
                    or any(c not in "0123456789abcdef" for c in value)):
                raise CheckpointError("Invalid checkpoint fingerprint")

    def save(self, path):
        self.validate()
        path = Path(path).expanduser()
        if path.is_symlink():
            raise CheckpointError("Checkpoint must not be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            # mkstemp creates mode 0600 before any metadata is written.
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=True, allow_nan=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, path)
            if os.name == "posix":
                directory = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @classmethod
    def load(cls, path):
        try:
            path = Path(path)
            if path.is_symlink() or path.stat().st_size > 65536:
                raise CheckpointError("Unsafe checkpoint file")
            data = json.loads(path.read_text(encoding="utf-8"))
            cp = cls(**data)
            cp.validate()
            return cp
        except (OSError, TypeError, ValueError) as exc:
            raise CheckpointError("Cannot load checkpoint: " + str(exc)) from exc
