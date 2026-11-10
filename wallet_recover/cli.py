"""Offline wordlist runner; no network access or telemetry."""
import argparse
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
from .bdb import load_wallet
from .crypto import try_password

_MASTERS = _KEYS = None

def init_worker(masters, keys):
    global _MASTERS, _KEYS
    _MASTERS, _KEYS = masters, keys

def worker(password):
    return password if try_password(_MASTERS, _KEYS, password) else None

def candidates(path):
    # Only remove the line ending; preserve spaces and empty passwords.
    with open(path, "rb") as f:
        for line in f:
            if line.endswith(b"\n"):
                line = line[:-1]
                if line.endswith(b"\r"):
                    line = line[:-1]
            yield line

def positive(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n

def save_password(path, password):
    # Atomic exclusive creation: never overwrite a wallet, wordlist or existing secret.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(password)

def main(argv=None):
    p = argparse.ArgumentParser(description="Offline recovery for your own legacy Bitcoin Core wallet")
    p.add_argument("--wallet", help="Closed wallet backup (BDB)")
    p.add_argument("--wordlist", help="Binary/UTF-8 candidates, one per line")
    p.add_argument("--jobs", type=positive, default=max(1, os.cpu_count() or 1))
    p.add_argument("--save", help="NEW output file for exact password bytes (mode 0600)")
    p.add_argument("--print-mkey", action="store_true", help="Inspect parameters without recovery")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.selftest:
            from .selftest import run
            run()
            print("Self-test passed (synthetic records; not a real wallet)")
            return 0
        if not args.wallet:
            p.error("--wallet is required")
        masters, keys = load_wallet(args.wallet)
        if not masters:
            raise ValueError("No mkey: unencrypted, unsupported or incomplete wallet; not a recovery result")
        for m in masters:
            print(f"mkey {m.id}: method={m.method}, rounds={m.iterations}, salt={m.salt.hex()}")
        print(f"ckey records: {len(keys)}")
        if args.print_mkey:
            return 0
        if any(m.method != 0 or m.other for m in masters):
            raise ValueError("Unsupported KDF parameters; only method 0 without extra parameters supported")
        if not keys:
            raise ValueError("No ckey: cannot confirm a password; refusing padding-only matches")
        if not args.wordlist:
            p.error("--wordlist is required for recovery")
        if not Path(args.wordlist).is_file():
            raise ValueError("Wordlist does not exist")
        if args.save and os.path.lexists(args.save):
            raise ValueError("--save file already exists; choose a new path")
        start, completed, found = time.monotonic(), 0, None
        context = mp.get_context("spawn")
        with context.Pool(args.jobs, init_worker, (masters, keys)) as pool:
            for result in pool.imap_unordered(worker, candidates(args.wordlist), chunksize=16):
                completed += 1
                if result is not None:
                    found = result
                    break
                if completed % 500 == 0:
                    elapsed = time.monotonic() - start
                    print(f"Completed {completed}; {completed/max(elapsed, .001):.1f} candidates/s", flush=True)
        elapsed = time.monotonic() - start
        if found is None:
            print(f"Not found: {completed} completed candidates in {elapsed:.1f}s")
            return 1
        print(f"Confirmed by public-key match after {completed} completed results ({elapsed:.1f}s)")
        if args.save:
            save_password(args.save, found)
            print("Password saved as exact bytes (no newline); protect the output file")
        else:
            print("Password bytes:", repr(found))
            print("Password hex:", found.hex())
        return 0
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
