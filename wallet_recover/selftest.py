"""Synthetic fixtures only. Never load or generate real funds here."""
import struct
import hashlib
from Crypto.Cipher import AES
from .serialize import parse_records
from .crypto import derive_key_iv, public_key, try_password

PASSWORD = b"synthetic-test-only"

def vector(data):
    if len(data) >= 253:
        raise ValueError("Fixture vectors must be short")
    return bytes([len(data)]) + data

def encrypt(key, iv, plain):
    pad = 16 - len(plain) % 16
    return AES.new(key, AES.MODE_CBC, iv).encrypt(plain + bytes([pad]) * pad)

def fixture_records(compressed=True, checksum=True, rounds=2):
    master, salt = bytes(range(32)), bytes(range(8))
    key, iv = derive_key_iv(PASSWORD, salt, rounds)
    mvalue = vector(encrypt(key, iv, master)) + vector(salt) + struct.pack("<II", 0, rounds) + b"\x00"
    secret = (1).to_bytes(32, "big")
    pubkey = public_key(secret, compressed)
    iv = hashlib.sha256(hashlib.sha256(pubkey).digest()).digest()[:16]
    encrypted = encrypt(master, iv, secret)
    value = vector(encrypted)
    if checksum:
        value += hashlib.sha256(hashlib.sha256(encrypted).digest()).digest()
    return [(vector(b"mkey") + struct.pack("<I", 1), mvalue),
            (vector(b"ckey") + vector(pubkey), value)]

def run():
    # Independent EVP_BytesToKey (libcrypto) reference vector: not PBKDF2.
    key, iv = derive_key_iv(b"testpass", bytes.fromhex("0011223344556677"), 25000)
    if key.hex() != "214a464d266223d688a822040430223b5b649fa04bf245e17f378449b36fe11a":
        raise RuntimeError("KDF key self-test failed")
    if iv.hex() != "51ed9e1602ef1ba8a4fb05b8238dae7c":
        raise RuntimeError("KDF IV self-test failed")
    for compressed in (True, False):
        for checksum in (True, False):
            masters, keys = parse_records(fixture_records(compressed, checksum))
            if not try_password(masters, keys, PASSWORD) or try_password(masters, keys, b"wrong"):
                raise RuntimeError("Recovery self-test failed")
