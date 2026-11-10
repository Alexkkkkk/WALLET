"""Bitcoin Core method 0 KDF and public-key-verified decryption."""
import hashlib
import hmac
from dataclasses import dataclass
from .errors import KDFError, VerifyError

try:
    from coincurve import PublicKey as _FastPublicKey
except ImportError:
    _FastPublicKey = None

from Crypto.Cipher import AES
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

def derive_key_iv(password, salt, iterations):
    if len(salt) != 8 or iterations < 1:
        raise KDFError("Invalid KDF parameters")
    digest = hashlib.sha512(password + salt).digest()
    for _ in range(iterations - 1):
        digest = hashlib.sha512(digest).digest()
    return digest[:32], digest[32:48]

def decrypt_cbc(key, iv, ciphertext):
    if not ciphertext or len(ciphertext) % 16:
        return None
    plain = AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)
    pad = plain[-1]
    if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
        return None
    return plain[:-pad]

def public_key(secret, compressed=True):
    scalar = int.from_bytes(secret, "big")
    if len(secret) != 32 or not 0 < scalar < SECP256K1_N:
        raise ValueError("Invalid secp256k1 secret")
    if _FastPublicKey is not None:
        return _FastPublicKey.from_valid_secret(secret).format(compressed=compressed)
    fmt = PublicFormat.CompressedPoint if compressed else PublicFormat.UncompressedPoint
    return ec.derive_private_key(scalar, ec.SECP256K1()).public_key().public_bytes(Encoding.X962, fmt)

def verify_master_key(master, keys):
    for entry in keys:
        iv = hashlib.sha256(hashlib.sha256(entry.pubkey).digest()).digest()[:16]
        secret = decrypt_cbc(master, iv, entry.ciphertext)
        if secret is None or len(secret) != 32:
            continue
        try:
            derived = public_key(secret, len(entry.pubkey) == 33)
        except ValueError:
            continue
        if hmac.compare_digest(derived, entry.pubkey):
            return True
    return False

@dataclass(frozen=True)
class _MasterSlot:
    salt: bytes
    iterations: int
    ciphertext: bytes


@dataclass(frozen=True)
class _KeySlot:
    iv: bytes
    ciphertext: bytes
    pubkey: bytes
    compressed: bool


class Decryptor:
    """Reusable wallet state. Each spawned worker constructs its own instance.

    SHA-512 retains the security defaults: usedforsecurity=False is not a
    general speed switch and does not promise to skip per-call FIPS tests.
    """
    def __init__(self, masters, keys):
        masters, keys = tuple(masters), tuple(keys)
        if not masters:
            raise KDFError("No mkey records")
        if any(m.method != 0 or m.other or len(m.salt) != 8
               or not isinstance(m.iterations, int) or m.iterations < 1 for m in masters):
            raise KDFError("Unsupported or malformed KDF parameters; method 0 required")
        self._masters = tuple(_MasterSlot(m.salt, m.iterations, m.ciphertext) for m in masters)
        self._slots = tuple(_KeySlot(
            hashlib.sha256(hashlib.sha256(k.pubkey).digest()).digest()[:16],
            k.ciphertext, k.pubkey, len(k.pubkey) == 33) for k in keys)
        if not self._slots:
            raise VerifyError("No ckey records: cannot confirm a password")

    def try_password(self, password):
        for m in self._masters:
            key, iv = derive_key_iv(password, m.salt, m.iterations)
            master = decrypt_cbc(key, iv, m.ciphertext)
            if master is None or len(master) != 32:
                continue
            for slot in self._slots:
                secret = decrypt_cbc(master, slot.iv, slot.ciphertext)
                if secret is None or len(secret) != 32:
                    continue
                try:
                    derived = public_key(secret, slot.compressed)
                except ValueError:
                    continue
                if hmac.compare_digest(derived, slot.pubkey):
                    return True
        return False


def try_password(masters, keys, password):
    """Compatibility wrapper. Use Decryptor for repeated attempts."""
    return Decryptor(masters, keys).try_password(password)
