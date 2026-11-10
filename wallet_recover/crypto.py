"""Bitcoin Core method 0 KDF and public-key-verified decryption."""
import hashlib
import hmac
from Crypto.Cipher import AES
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

def derive_key_iv(password, salt, iterations):
    if len(salt) != 8 or iterations < 1:
        raise ValueError("Invalid KDF parameters")
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

def try_password(masters, keys, password):
    if not keys:
        raise ValueError("No ckey records: cannot confirm a password")
    for entry in masters:
        if entry.method != 0 or entry.other:
            raise ValueError("Unsupported derivation method or parameters")
        key, iv = derive_key_iv(password, entry.salt, entry.iterations)
        master = decrypt_cbc(key, iv, entry.ciphertext)
        if master is not None and len(master) == 32 and verify_master_key(master, keys):
            return True
    return False
