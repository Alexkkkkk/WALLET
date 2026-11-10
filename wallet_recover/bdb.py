"""Read only an isolated copy of a closed legacy BDB wallet."""
from pathlib import Path
import shutil
import tempfile
from .serialize import parse_records

def load_wallet(wallet_path):
    source = Path(wallet_path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("Wallet must be a regular file")
    with source.open("rb") as f:
        if f.read(16) == b"SQLite format 3\x00":
            raise ValueError("SQLite/descriptor wallets are not supported")
    try:
        from bsddb3 import db
    except ImportError as exc:
        raise ValueError("Install bsddb3 and Berkeley DB development headers") from exc
    with tempfile.TemporaryDirectory(prefix="wallet-recover-") as directory:
        copy = Path(directory) / "wallet.dat"
        before = source.stat()
        shutil.copyfile(source, copy)
        copy.chmod(0o600)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Wallet changed while copying; close Bitcoin Core first")
        database = db.DB()
        try:
            database.open(str(copy), "main", db.DB_BTREE, db.DB_RDONLY)
            cursor = database.cursor()
            try:
                def records():
                    entry = cursor.first()
                    while entry is not None:
                        yield entry
                        entry = cursor.next()
                return parse_records(records())
            finally:
                cursor.close()
        except db.DBError as exc:
            raise ValueError("Cannot read BDB wallet copy: " + str(exc)) from exc
        finally:
            database.close()
