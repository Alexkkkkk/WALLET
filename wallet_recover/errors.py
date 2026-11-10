"""Recoverable errors; ValueError compatibility is retained for v0.2 callers."""


class WalletError(ValueError):
    """Base class for recoverable input and wallet errors."""


class KDFError(WalletError):
    """Unsupported or malformed KDF parameters."""


class DecryptError(WalletError):
    """Malformed encrypted data."""


class VerifyError(WalletError):
    """Public-key verification cannot be performed."""


class BDBError(WalletError):
    """Berkeley DB backend or file access problem."""


class CheckpointError(WalletError):
    """Invalid, incompatible or unsafe checkpoint."""
