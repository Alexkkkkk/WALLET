import ctypes
import ctypes.util
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import pytest
from wallet_recover.crypto import derive_key_iv, decrypt_cbc, public_key, try_password
from wallet_recover.serialize import Reader, parse_records
from wallet_recover.selftest import fixture_records, vector, encrypt, PASSWORD, run
from wallet_recover.bdb import load_wallet
from wallet_recover.cli import candidates, save_password, main

ROOT = Path(__file__).resolve().parents[1]

def test_selftest():
    run()

def test_kdf_matches_openssl_evp_not_pbkdf2():
    name = ctypes.util.find_library('crypto')
    if not name:
        pytest.skip('libcrypto unavailable')
    lib = ctypes.CDLL(name)
    lib.EVP_aes_256_cbc.restype = ctypes.c_void_p
    lib.EVP_sha512.restype = ctypes.c_void_p
    fn = lib.EVP_BytesToKey
    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    fn.restype = ctypes.c_int
    for count in (1, 2, 25000):
        salt = ctypes.create_string_buffer(bytes.fromhex('0011223344556677'))
        password = ctypes.create_string_buffer(b'testpass')
        key, iv = ctypes.create_string_buffer(32), ctypes.create_string_buffer(16)
        assert fn(lib.EVP_aes_256_cbc(), lib.EVP_sha512(), salt, password, 8, count, key, iv) == 32
        assert derive_key_iv(b'testpass', salt.raw[:8], count) == (key.raw, iv.raw)

@pytest.mark.parametrize('compressed', [True, False])
@pytest.mark.parametrize('checksum', [True, False])
def test_records_and_password(compressed, checksum):
    masters, keys = parse_records(fixture_records(compressed, checksum))
    assert try_password(masters, keys, PASSWORD)
    assert not try_password(masters, keys, b'wrong')
    assert not try_password(masters, keys, b'')

def test_public_key_known_generator():
    assert public_key((1).to_bytes(32, 'big')).hex() == '0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798'
    with pytest.raises(ValueError):
        public_key(bytes(32))

def test_public_key_verification_is_required():
    records = fixture_records(checksum=False)
    other_pub = public_key((2).to_bytes(32, 'big'))
    iv = hashlib.sha256(hashlib.sha256(other_pub).digest()).digest()[:16]
    # Valid padding and valid scalar but NOT the public key in this record.
    ciphertext = encrypt(bytes(range(32)), iv, (1).to_bytes(32, 'big'))
    records[1] = (vector(b'ckey') + vector(other_pub), vector(ciphertext))
    masters, keys = parse_records(records)
    assert not try_password(masters, keys, PASSWORD)

def test_multiple_master_keys():
    records = fixture_records()
    bad = records[0][1]
    bad = bad[:1] + bytes([bad[1] ^ 1]) + bad[2:]
    records.insert(0, (vector(b'mkey') + struct.pack('<I', 2), bad))
    masters, keys = parse_records(records)
    assert len(masters) == 2
    assert try_password(masters, keys, PASSWORD)

@pytest.mark.parametrize('data', [b'', b'\xfd', b'\xfd\x01\x00', b'\xfe\xff\xff\x00\x00', b'\xff\x01'])
def test_bad_compactsize(data):
    with pytest.raises(ValueError):
        Reader(data).size()

def test_truncated_vector():
    with pytest.raises(ValueError):
        Reader(b'\x05ab').vector()

def test_checksum_corruption():
    records = fixture_records()
    key, value = records[1]
    records[1] = key, value[:-1] + bytes([value[-1] ^ 1])
    with pytest.raises(ValueError, match='checksum'):
        parse_records(records)

def test_trailing_ckey_bytes():
    records = fixture_records(checksum=False)
    key, value = records[1]
    records[1] = key, value + b'x'
    with pytest.raises(ValueError):
        parse_records(records)

def test_padding():
    key, iv = bytes(32), bytes(16)
    from Crypto.Cipher import AES
    invalid = AES.new(key, AES.MODE_CBC, iv).encrypt(b'x' * 15 + b'\x02')
    assert decrypt_cbc(key, iv, invalid) is None
    assert decrypt_cbc(key, iv, b'x') is None
    assert decrypt_cbc(key, iv, encrypt(key, iv, bytes(32))) == bytes(32)

def test_no_ckey_and_unknown_kdf():
    masters, keys = parse_records(fixture_records())
    with pytest.raises(ValueError):
        try_password(masters, [], PASSWORD)
    with pytest.raises(ValueError):
        try_password([replace(masters[0], method=1)], keys, PASSWORD)

def test_lines_and_exclusive_output(tmp_path):
    path = tmp_path / 'candidates'
    path.write_bytes(b' leading trailing \r\n\n\xff\nlast')
    assert list(candidates(path)) == [b' leading trailing ', b'', b'\xff', b'last']
    output = tmp_path / 'secret'
    save_password(output, b'\xff secret ')
    assert output.read_bytes() == b'\xff secret '
    if os.name == 'posix':
        assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        save_password(output, b'overwrite')
    assert output.read_bytes() == b'\xff secret '

def make_bdb(path):
    from bsddb3 import db
    handle = db.DB()
    handle.open(str(path), 'main', db.DB_BTREE, db.DB_CREATE)
    for key, value in fixture_records():
        handle.put(key, value)
    handle.close()

def invoke(*args):
    return subprocess.run([sys.executable, '-m', 'wallet_recover', *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, timeout=45)

def test_bdb_cli_two_processes_and_source_unchanged(tmp_path):
    wallet = tmp_path / 'wallet.dat'
    make_bdb(wallet)
    original = wallet.read_bytes()
    masters, keys = load_wallet(wallet)
    assert try_password(masters, keys, PASSWORD)
    words, output = tmp_path / 'words', tmp_path / 'result'
    words.write_bytes(b'wrong\n' + PASSWORD + b'\n')
    result = invoke('--wallet', wallet, '--wordlist', words, '--jobs', 2, '--save', output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_bytes() == PASSWORD
    assert PASSWORD.decode() not in result.stdout
    assert wallet.read_bytes() == original
    assert not list(tmp_path.glob('__db.*'))
    inspect = invoke('--wallet', wallet, '--print-mkey')
    assert inspect.returncode == 0
    again = invoke('--wallet', wallet, '--wordlist', words, '--save', output)
    assert again.returncode == 2
    words.write_bytes(b'wrong\n')
    exhausted = invoke('--wallet', wallet, '--wordlist', words, '--jobs', 1)
    assert exhausted.returncode == 1

def test_sqlite_refused(tmp_path):
    wallet = tmp_path / 'wallet.dat'
    wallet.write_bytes(b'SQLite format 3\x00')
    with pytest.raises(ValueError, match='SQLite'):
        load_wallet(wallet)

def test_invalid_jobs():
    with pytest.raises(SystemExit) as exc:
        main(['--jobs', '0'])
    assert exc.value.code == 2
