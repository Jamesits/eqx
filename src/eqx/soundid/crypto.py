"""AES-128-CBC with PKCS#7 padding and the SoundID password-to-key derivation."""

from __future__ import annotations

import hashlib
import secrets

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Default password compiled into the SoundID Reference binaries.  Used for
# .swproj files whenever <PasswordProtected> is false.  .swhp bodies use the
# computer ID instead (see ``swhp_key``).
DEFAULT_PASSWORD = b"sonarworks m6As2XFRjA"


def varint(n: int) -> bytes:
    """.NET BinaryWriter 7-bit length prefix."""
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def derive_key(password: bytes = DEFAULT_PASSWORD) -> bytes:
    """AES-128 key = first 16 bytes of SHA-1(varint(len(pw)) || pw)."""
    return hashlib.sha1(varint(len(password)) + password).digest()[:16]


def swhp_key(computer_id: str) -> bytes:
    """AES-128 key of a .swhp body = first 16 bytes of SHA-1(computer ID).

    Unlike ``derive_key`` there is no length prefix.
    """
    return hashlib.sha1(computer_id.encode("ascii")).digest()[:16]


def decrypt_raw(key: bytes, data: bytes) -> bytes:
    """Decrypt ``IV || ciphertext``; keep the padding."""
    iv, ct = data[:16], data[16:]
    if len(ct) % 16:
        raise ValueError("ciphertext is not a multiple of the AES block size")
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(ct) + dec.finalize()


def decrypt(key: bytes, data: bytes) -> bytes:
    """Decrypt ``IV || ciphertext`` and strip PKCS#7 padding."""
    pt = decrypt_raw(key, data)
    pad = pt[-1] if pt else 0
    if not 1 <= pad <= 16 or pt[-pad:] != bytes([pad]) * pad:
        raise ValueError("bad PKCS#7 padding - wrong password or key?")
    return pt[:-pad]


def encrypt(key: bytes, data: bytes, iv: bytes | None = None) -> bytes:
    """PKCS#7-pad and encrypt; return ``IV || ciphertext``."""
    iv = secrets.token_bytes(16) if iv is None else iv
    pad = 16 - len(data) % 16
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return iv + enc.update(data + bytes([pad]) * pad) + enc.finalize()

