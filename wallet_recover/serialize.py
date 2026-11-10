"""Bounds-checked Bitcoin Core legacy wallet serialization."""
import struct
from dataclasses import dataclass

class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError("Truncated wallet record")
        result = self.data[self.pos:self.pos+n]
        self.pos += n
        return result

    def uint32(self):
        return struct.unpack("<I", self.take(4))[0]

    def size(self):
        tag = self.take(1)[0]
        if tag < 253:
            return tag
        width, minimum = {253: (2, 253), 254: (4, 65536), 255: (8, 2**32)}[tag]
        n = int.from_bytes(self.take(width), "little")
        if n < minimum:
            raise ValueError("Non-canonical CompactSize")
        return n

    def vector(self):
        return self.take(self.size())

    def finish(self):
        if self.pos != len(self.data):
            raise ValueError("Unexpected trailing record data")

@dataclass(frozen=True)
class MasterKey:
    id: int
    ciphertext: bytes
    salt: bytes
    method: int
    iterations: int
    other: bytes

@dataclass(frozen=True)
class EncryptedKey:
    pubkey: bytes
    ciphertext: bytes

def parse_records(records):
    import hashlib
    masters, keys = [], []
    for raw_key, value in records:
        k = Reader(raw_key)
        kind = k.vector()
        if kind == b"mkey":
            key_id = k.uint32()
            k.finish()
            v = Reader(value)
            ciphertext, salt = v.vector(), v.vector()
            method, rounds = v.uint32(), v.uint32()
            other = v.vector()
            v.finish()
            if len(ciphertext) != 48 or len(salt) != 8 or rounds < 1:
                raise ValueError("Invalid mkey lengths or iteration count")
            masters.append(MasterKey(key_id, ciphertext, salt, method, rounds, other))
        elif kind == b"ckey":
            pubkey = k.vector()
            k.finish()
            if not (len(pubkey) == 33 and pubkey[0] in (2, 3) or
                    len(pubkey) == 65 and pubkey[0] == 4):
                raise ValueError("Invalid ckey public key encoding")
            v = Reader(value)
            ciphertext = v.vector()
            # Older records contain only a vector; newer versions append a uint256 checksum.
            remaining = len(value) - v.pos
            if remaining == 32:
                checksum = v.take(32)
                expected = hashlib.sha256(hashlib.sha256(ciphertext).digest()).digest()
                if checksum != expected:
                    raise ValueError("ckey ciphertext checksum mismatch")
            v.finish()
            if len(ciphertext) != 48:
                raise ValueError("Invalid ckey ciphertext length")
            keys.append(EncryptedKey(pubkey, ciphertext))
    return masters, keys
