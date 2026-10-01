"""Encryption of SoundID partner exports and the partner keys.

An encrypted export is ``IV || AES-256-CBC(plaintext)`` with PKCS#7 padding.
The AES key is SHA-256 of the partner's key string.
"""

from __future__ import annotations

import functools
import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import crypto


@dataclass(frozen=True)
class Partner:
    id: str  # SoundID's partner id
    name: str
    format: str  # eqx format id of the plaintext
    key: str | None  # None: the key is the device serial number


PARTNERS = (
    Partner(
        "fluid",
        "Fluid Audio",
        "soundid-export-biquad-json",
        "5F3DD05ABB3B8FB122885C75E01B73560F8C01D930879FD183796F7DDD94F69A",
    ),
    Partner("merging", "MERGING+ANUBIS", "soundid-export-biquad-json", None),
    Partner(
        "grace-design",
        "Grace Design m908",
        "soundid-export-peq-json",
        "2sh3g08azj2cllsg5tato4wmdec8kpwj8etn6uxk76c4i5w5p36p71sw39vlm86h",
    ),
    Partner(
        "lynx-aurora",
        "Lynx Aurora",
        "soundid-export-peq-json",
        "sjvajtcac1abjrr2w2zx3k1ambn6ljhk",
    ),
    # SoundID stores the key string only hashed: lower-case hex SHA-256 of this.
    Partner(
        "adam",
        "ADAM Audio A Series",
        "soundid-export-biquad-xml",
        hashlib.sha256(b"6ABE9E4A59EB434B997B4B6FD2B1055F").hexdigest(),
    ),
)
MERGING = next(p for p in PARTNERS if p.id == "merging")
# SoundID accepts "A" and six digits, adds a missing "A" and upper-cases it.
SERIAL_COUNT = 1_000_000


def key(key_string: str) -> bytes:
    return hashlib.sha256(key_string.encode("utf-8")).digest()


def serial_number(value: str) -> str:
    """The serial number as SoundID uses it in the key."""
    value = value.strip().upper()
    if not value.startswith("A"):
        value = "A" + value
    if len(value) != 7 or not value[1:].isdigit():
        raise ValueError(f"serial number {value!r} is not 'A' and six digits")
    return value


def encrypt(key_string: str, plaintext: bytes, iv: bytes | None = None) -> bytes:
    return crypto.encrypt(key(key_string), plaintext, iv)


def _first_block(aes_key: bytes, data: bytes) -> bytes:
    block = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor().update(data[16:32])
    return bytes(a ^ b for a, b in zip(block, data[:16]))


def _plausible(block: bytes) -> bool:
    """The first plaintext block of every known export: JSON or XML text."""
    return block[:1] in (b"{", b"<") and all(
        32 <= c < 127 or c in (9, 10, 13) for c in block
    )


def _decrypt(key_string: str, data: bytes) -> bytes | None:
    aes_key = key(key_string)
    if len(data) < 32 or len(data) % 16 or not _plausible(_first_block(aes_key, data)):
        return None
    try:
        return crypto.decrypt(aes_key, data)
    except ValueError:
        return None


@functools.lru_cache(maxsize=8)
def find_serial(data: bytes) -> str | None:
    """Search every serial number for the one that decrypts ``data``."""
    if len(data) < 32 or len(data) % 16:
        return None
    for n in range(SERIAL_COUNT):
        serial = f"A{n:06d}"
        if (
            _plausible(_first_block(key(serial), data))
            and _decrypt(serial, data) is not None
        ):
            return serial
    return None


def open_export(
    data: bytes, serial: str | None = None, search: bool = True
) -> tuple[bytes, Partner, str]:
    """Return (plaintext, partner, key string).

    ``serial`` is tried as the MERGING key; without it every serial number is
    searched (a few seconds) unless ``search`` is false.
    """
    for partner in PARTNERS:
        if (
            partner.key is not None
            and (plain := _decrypt(partner.key, data)) is not None
        ):
            return plain, partner, partner.key
    if serial is not None:
        serial = serial_number(serial)
        if (plain := _decrypt(serial, data)) is not None:
            return plain, MERGING, serial
        raise ValueError(f"not a SoundID export, or not for serial number {serial}")
    if search and (found := find_serial(data)) is not None:
        return _decrypt(found, data), MERGING, found
    raise ValueError("not an encrypted SoundID export, or its key is not known")


def open_as(
    data: bytes,
    format_id: str,
    kind: str,
    serial: str | None = None,
    search: bool = True,
) -> tuple[bytes, str]:
    """(plaintext, encryption description) of an export of the format ``format_id``,
    named ``kind`` in the error."""
    plain, partner, key = open_export(data, serial, search)
    if partner.format != format_id:
        raise ValueError(f"a {partner.name} export is not a {kind}")
    serial_note = f", serial number {key}" if partner.key is None else ""
    return plain, f"AES-256-CBC, {partner.name} key{serial_note}"


def partner_format(data: bytes) -> str | None:
    """The eqx format id of an encrypted export, or None."""
    try:
        _plain, partner, _key = open_export(data)
    except ValueError:
        return None
    return partner.format
