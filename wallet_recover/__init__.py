"""WALLET — локальное восстановление пароля legacy Bitcoin Core wallet.dat.

Единственный источник версии для всего проекта: ``pyproject.toml`` читает
значение отсюда через ``[tool.setuptools.dynamic]``, CLI отдаёт его по
``--version``, а тест ``tests/test_sync.py`` сверяет его с README.
"""

__version__ = "0.4.2"

__all__ = ["__version__", "Decryptor", "derive_key_iv", "decrypt_cbc",
           "public_key", "try_password", "WalletError", "KDFError", "DecryptError",
           "VerifyError", "BDBError", "CheckpointError"]


def __getattr__(name):
    if name in {"Decryptor", "derive_key_iv", "decrypt_cbc", "public_key", "try_password"}:
        from . import crypto
        return getattr(crypto, name)
    if name in {"WalletError", "KDFError", "DecryptError", "VerifyError", "BDBError", "CheckpointError"}:
        from . import errors
        return getattr(errors, name)
    raise AttributeError(name)
