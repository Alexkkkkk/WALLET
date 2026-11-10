"""Binary wordlists. A bare trailing CR is password data, not a terminator."""
import hashlib
import os
from pathlib import Path


def strip_terminator(line):
    if line.endswith(b"\n"):
        line = line[:-1]
        if line.endswith(b"\r"):
            line = line[:-1]
    return line


def iter_offsets(path, offset=0):
    """Yield (candidate, byte offset immediately after its physical line)."""
    with open(path, "rb") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line:
                break
            yield strip_terminator(line), f.tell()


def iter_lines(path):
    for candidate, _ in iter_offsets(path):
        yield candidate


def iter_unique(path, seen=None):
    """SHA-256 keyed dedup. Memory is O(unique); Python overhead exceeds 32 B/item."""
    seen = set() if seen is None else seen
    for candidate in iter_lines(path):
        digest = hashlib.sha256(candidate).digest()
        if digest not in seen:
            seen.add(digest)
            yield candidate


def count(path):
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def fingerprint(path, sample=None):
    """Full SHA-256, including the middle. sample is accepted but never weakens it."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def export_unique(source, output):
    """Exclusive private export; CR-ending passwords need a CRLF terminator."""
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            n = 0
            for candidate in iter_unique(source):
                f.write(candidate + (b"\r\n" if candidate.endswith(b"\r") else b"\n"))
                n += 1
            return n
    except BaseException:
        Path(output).unlink(missing_ok=True)
        raise
