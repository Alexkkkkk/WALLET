"""Read only an isolated copy of a closed legacy BDB wallet.

Backend selection:
  * ``berkeleydb``  -- current PyPI name; Windows binary availability is unconfirmed,
                       a native Berkeley DB installation may be required;
  * ``bsddb3``      -- legacy name, source build on Linux/macOS.
Both expose the same ``db`` module API, so the rest of the code is identical.
"""
from pathlib import Path
import shutil
import tempfile

from .serialize import parse_records

BACKENDS = ("berkeleydb", "bsddb3")
_backend_error = None


def bdb_module():
    """Возвращает модуль db из первого доступного драйвера Berkeley DB."""
    global _backend_error
    errors = []
    for name in BACKENDS:
        try:
            from importlib import import_module
            module = import_module(name + ".db")
            return module
        except Exception as exc:  # ImportError or a broken native library
            errors.append("%s: %s" % (name, exc))
    _backend_error = "; ".join(errors)
    raise ValueError(
        "Не найден драйвер Berkeley DB. Установите его командой: "
        "pip install berkeleydb  (Windows) или  pip install bsddb3  (Linux). Подробности: "
        + _backend_error
    )


def backend_name():
    """Имя используемого драйвера или None, если ни один не установлен."""
    for name in BACKENDS:
        try:
            from importlib import import_module
            import_module(name + ".db")
            return name
        except Exception:
            continue
    return None


def load_wallet(wallet_path):
    source = Path(wallet_path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("Wallet must be a regular file")
    with source.open("rb") as f:
        if f.read(16) == b"SQLite format 3\x00":
            raise ValueError("SQLite/descriptor wallets are not supported")
    db = bdb_module()
    with tempfile.TemporaryDirectory(prefix="wallet-recover-") as directory:
        copy = Path(directory) / "wallet.dat"
        before = source.stat()
        shutil.copyfile(source, copy)
        try:
            copy.chmod(0o600)
        except OSError:
            pass  # Windows: 0600 is not representable, file stays user-scoped
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
