"""Reader and writer for the Sonarworks ``PEQb`` curve container.

Used by ``*.swhp`` headphone profiles, ``*.eqb`` exports, and the ``eqb`` part
of a ``*.swproj``.

The container version selects the body grammar:
  3.0.0.0 / 3.0.0.1  - float64 columns, per-curve Transfer/Delay scalars
  2.0.14.10 / 2.1.3.14 - the same grammar (Sonarworks Reference 3 and 4 read them)
  3.0.0.2            - float32 columns, per-curve flags + parameter map
  2.0.13.30          - two float64 tables, gains and delays, no curve types
  magic ``PEQB``     - no version; left/right gains on a fixed grid

An encrypted body is ``IV || AES-128-CBC(b"Sonarworks" || body)``.

The writers emit 3.0.0.2 (``write``) and 3.0.0.0 / 3.0.0.1 (``write_v1``),
unencrypted.
"""

from __future__ import annotations

import math
import os
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..fileformat import Format, Inspector, file_section, frequency_range
from ..options import Option
from ..report import Section, Table
from . import computerid, crypto

MAGIC = b"PEQb"
LEGACY_MAGIC = b"PEQB"
# Versions with no flag byte, never encrypted.
UNFLAGGED_VERSIONS = ((2, 0, 13, 30), (2, 0, 14, 10), (2, 1, 3, 14), (3, 0, 0, 0))
# Plaintext prefix of an encrypted body; the reader rejects the key without it.
BODY_HEADER = b"Sonarworks"

# CurveType enum (switch table in the curve-name helper).
CURVE_TYPE = {
    0: "None",
    1: "CorrectionLeft",
    2: "CorrectionRight",
    3: "MeasurementLeft",
    4: "MeasurementRight",
    5: "Frame",
    6: "LeftErrorBand",
    7: "RightErrorBand",
    8: "ErrorBand",
    9: "Measurement",
    10: "Correction",
    11: "Target",
    12: "ProcentualFrame",
    13: "Left",
    14: "Right",
    15: "Diff",
    16: "Thd",
    17: "ThdP",
    18: "TargetLeft",
    19: "TargetRight",
}
CURVE_TYPE_ID = {name: i for i, name in CURVE_TYPE.items()}

COMPUTER_ID_OPTION = Option(
    "--computer-id",
    help="computer ID the profile was downloaded for (or SWHP_COMPUTER_ID; "
    "default: this machine's ID)",
)
KEY_OPTION = Option("--key", help="raw AES body key, hex")

# Curve flag bits (3.0.0.2).
F_FREQUENCY, F_RESPONSE, F_GROUP_DELAY, F_PARAMETERS = 1, 2, 4, 8


@dataclass
class Curve:
    curve_type: int
    points: list = field(default_factory=list)  # [(freq, response, group_delay)]
    parameters: dict = field(default_factory=dict)
    flags: int | None = None  # 3.0.0.2 only
    transfer: float | None = None  # 3.0.0.0/1 only
    delay_ms: float | None = None  # 3.0.0.0/1 only

    @property
    def type_name(self) -> str:
        return CURVE_TYPE.get(self.curve_type, f"Unknown({self.curve_type})")


@dataclass
class Peqb:
    version: tuple
    encrypted: bool
    header_size: int
    iv: bytes | None
    ciphertext_len: int
    curves: list = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    trailing: bytes = b""
    decoded: bool = False

    @property
    def version_str(self) -> str:
        """``PEQB`` for the unversioned container."""
        return ".".join(str(v) for v in self.version) if self.version else "PEQB"


# --------------------------------------------------------------------------
# reader
# --------------------------------------------------------------------------
def parse_header(blob: bytes) -> Peqb:
    """Parse the 4-, 8- or 9-byte container header.

    3.0.0.0 and the 2.x versions have no encryption-flag byte; 3.0.0.1 and
    3.0.0.2 do.  ``PEQB`` has no version.
    """
    if blob[:4] == LEGACY_MAGIC:
        return Peqb(
            version=(),
            encrypted=False,
            header_size=4,
            iv=None,
            ciphertext_len=len(blob) - 4,
        )
    if blob[:4] != MAGIC:
        raise ValueError(f"not a PEQb container (magic {blob[:4]!r})")
    version = tuple(blob[4:8])
    if version in UNFLAGGED_VERSIONS:
        header_size, encrypted = 8, False
    else:
        header_size, encrypted = 9, bool(blob[8])
    body = blob[header_size:]
    return Peqb(
        version=version,
        encrypted=encrypted,
        header_size=header_size,
        iv=body[:16] if encrypted else None,
        ciphertext_len=len(body) - 16 if encrypted else len(body),
    )


def _read_str(b: bytes, p: int) -> tuple[str, int]:
    # A non-zero tag is an empty string with no length field.
    if b[p] != 0x00:
        return "", p + 1
    (n,) = struct.unpack_from("<I", b, p + 1)
    return b[p + 5 : p + 5 + n].decode("utf-8"), p + 5 + n


def _read_map(b: bytes, p: int) -> tuple[dict, int]:
    (n,) = struct.unpack_from("<I", b, p)
    p += 4
    out = {}
    for _ in range(n):
        k, p = _read_str(b, p)
        v, p = _read_str(b, p)
        out[k] = v
    return out, p


def parse_body_v1(body: bytes) -> tuple[list, dict, bytes]:
    """3.0.0.0 / 3.0.0.1 grammar - float64 columns, Transfer/Delay scalars.

    u32 curveCount
    curveCount x {
        u32 curveType
        f64 transfer
        f64 delay            (milliseconds)
        u32 pointCount
        pointCount x { f64 frequency; f64 response; f64 groupDelay }
    }
    u32 fileParameterCount            (absent if the body ends here)
    fileParameterCount x (String key, String value)
    """
    p = 0
    (ncurves,) = struct.unpack_from("<I", body, p)
    p += 4
    curves = []
    for _ in range(ncurves):
        ctype, transfer, delay, npts = struct.unpack_from("<IddI", body, p)
        p += 4 + 8 + 8 + 4
        vals = struct.unpack_from(f"<{npts * 3}d", body, p)
        p += npts * 24
        pts = [(vals[i * 3], vals[i * 3 + 1], vals[i * 3 + 2]) for i in range(npts)]
        curves.append(Curve(ctype, pts, transfer=transfer, delay_ms=delay))
    params = {}
    if p < len(body):  # optional trailing parameter map
        try:
            params, p = _read_map(body, p)
        except (struct.error, IndexError, ValueError):
            pass  # not a parameter map; left in the tail
    return curves, params, body[p:]


def parse_body_v2(body: bytes) -> tuple[list, dict, bytes]:
    """3.0.0.2 grammar - float32 columns selected by a per-curve flag word.

    u32 curveCount
    curveCount x {
        u32 curveType
        u32 flags
        u32 pointCount        if flags & 7
        u32 parameterCount    if flags & 8
        pointCount x { f32 frequency if&1; f32 response if&2; f32 groupDelay if&4 }
        parameterCount x (String key, String value)
    }
    u32 fileParameterCount
    fileParameterCount x (String key, String value)
    """
    p = 0
    (ncurves,) = struct.unpack_from("<I", body, p)
    p += 4
    curves = []
    for _ in range(ncurves):
        ctype, flags = struct.unpack_from("<2I", body, p)
        p += 8
        npts = nparams = 0
        if flags & 7:
            (npts,) = struct.unpack_from("<I", body, p)
            p += 4
        if flags & F_PARAMETERS:
            (nparams,) = struct.unpack_from("<I", body, p)
            p += 4
        ncol = (
            bool(flags & F_FREQUENCY)
            + bool(flags & F_RESPONSE)
            + bool(flags & F_GROUP_DELAY)
        )
        vals = struct.unpack_from(f"<{npts * ncol}f", body, p)
        p += npts * ncol * 4
        pts = []
        for i in range(npts):
            row = list(vals[i * ncol : (i + 1) * ncol])
            f = row.pop(0) if flags & F_FREQUENCY else None
            r = row.pop(0) if flags & F_RESPONSE else None
            g = row.pop(0) if flags & F_GROUP_DELAY else None
            pts.append((f, r, g))
        cp = {}
        for _ in range(nparams):
            k, p = _read_str(body, p)
            v, p = _read_str(body, p)
            cp[k] = v
        curves.append(Curve(ctype, pts, cp, flags=flags))
    params, p = _read_map(body, p)
    return curves, params, body[p:]


def _gain_db(percent: float) -> float:
    """A legacy linear gain, 100 = 0 dB, in dB."""
    return 20 * math.log10(percent / 100) if percent > 1e-5 else -100.0


def _corrections(left: list, right: list, gains: tuple, delays: tuple) -> list[Curve]:
    return [
        Curve(CURVE_TYPE_ID[name], points, transfer=_gain_db(gain), delay_ms=delay)
        for name, points, gain, delay in (
            ("CorrectionLeft", left, gains[0], delays[0]),
            ("CorrectionRight", right, gains[1], delays[1]),
        )
    ]


def parse_body_2013(body: bytes) -> tuple[list, dict, bytes]:
    """2.0.13.30 grammar - left and right correction tables.

    f64 gainL, f64 gainR      linear, 100 = 0 dB
    f64 delayL, f64 delayR    milliseconds
    u32 nL; nL x { f64 frequency; f64 response; f64 groupDelay }
    u32 nR; nR x { ... }
    """
    gain_l, gain_r, delay_l, delay_r = struct.unpack_from("<4d", body, 0)
    p, tables = 32, []
    for _ in range(2):
        (n,) = struct.unpack_from("<I", body, p)
        vals = struct.unpack_from(f"<{n * 3}d", body, p + 4)
        tables.append([tuple(vals[i * 3 : i * 3 + 3]) for i in range(n)])
        p += 4 + n * 24
    return _corrections(*tables, (gain_l, gain_r), (delay_l, delay_r)), {}, body[p:]


# The unversioned container ends with 10 float64 settings and 8 unused bytes.
LEGACY_SETTINGS_SIZE = 10 * 8 + 8


def legacy_grid(n: int) -> list[float]:
    """The frequencies of an unversioned ``PEQB``: quadratic from 20 Hz to 22 kHz."""
    return [20 + 21980 * ((i + 1) / (n + 2)) ** 2 for i in range(n)]


def parse_body_legacy(body: bytes) -> tuple[list, dict, bytes]:
    """``PEQB`` grammar - responses on ``legacy_grid``.

    f64 count                 a double, not an integer
    count x { f64 left; f64 right }
    f64 gainL, f64 gainR      linear, 100 = 0 dB
    f64 delayL, f64 delayR    milliseconds
    6 x f64, 8 bytes          playback settings, not used
    """
    (count,) = struct.unpack_from("<d", body, 0)
    n = int(count)
    vals = struct.unpack_from(f"<{2 * n}d", body, 8)
    p = 8 + 16 * n
    gain_l, gain_r, delay_l, delay_r = struct.unpack_from("<4d", body, p)
    grid = legacy_grid(n)
    left = [(f, vals[2 * i], 0.0) for i, f in enumerate(grid)]
    right = [(f, vals[2 * i + 1], 0.0) for i, f in enumerate(grid)]
    p = min(len(body), p + LEGACY_SETTINGS_SIZE)
    return _corrections(left, right, (gain_l, gain_r), (delay_l, delay_r)), {}, body[p:]


def parse_body(body: bytes, version: tuple) -> tuple[list, dict, bytes]:
    """Try the grammar matching ``version`` first, then the other one."""
    if version == ():
        return parse_body_legacy(body)
    if version == (2, 0, 13, 30):
        return parse_body_2013(body)
    grammars = (
        [parse_body_v2, parse_body_v1]
        if version >= (3, 0, 0, 2)
        else [parse_body_v1, parse_body_v2]
    )
    last = None
    for g in grammars:
        try:
            curves, params, tail = g(body)
            if not tail:
                return curves, params, tail
            last = (curves, params, tail)
        except (struct.error, IndexError, ValueError) as e:
            last = last or e
    if isinstance(last, tuple):
        return last
    raise ValueError(f"could not parse body: {last}")


def decrypt_body(key: bytes, data: bytes) -> bytes:
    """``IV || ciphertext`` -> body without the ``Sonarworks`` prefix.

    The reader ignores the padding, so it is stripped only when it is valid
    PKCS#7.
    """
    pt = crypto.decrypt_raw(key, data)
    if not pt.startswith(BODY_HEADER):
        raise ValueError(
            "decrypted body has no 'Sonarworks' header - wrong computer ID or key?"
        )
    pad = pt[-1]
    if 1 <= pad <= 16 and pt[-pad:] == bytes([pad]) * pad:
        pt = pt[:-pad]
    return pt[len(BODY_HEADER) :]


def read(blob: bytes, key: bytes | None = None) -> Peqb:
    """Parse a PEQb blob.

    An encrypted body without ``key`` is left undecoded (``decoded`` False).
    """
    hdr = parse_header(blob)
    if hdr.encrypted:
        if key is None:
            return hdr
        body = decrypt_body(key, blob[hdr.header_size :])
    else:
        body = blob[hdr.header_size :]
    hdr.curves, hdr.parameters, hdr.trailing = parse_body(body, hdr.version)
    hdr.decoded = True
    return hdr


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
def _write_str(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return b"\x00" + struct.pack("<I", len(encoded)) + encoded


def _write_map(values: Iterable[tuple]) -> bytes:
    items = list(values)
    out = bytearray(struct.pack("<I", len(items)))
    for k, v in items:
        out += _write_str(str(k)) + _write_str(str(v))
    return bytes(out)


def write(curves: Iterable[Curve], parameters: dict | None = None) -> bytes:
    """Serialize curves as an unencrypted PEQb 3.0.0.2 blob.

    ``Curve.flags`` selects the stored columns; when None it is derived from
    which point columns are present and whether parameters exist.
    """
    curves = list(curves)
    body = bytearray(struct.pack("<I", len(curves)))
    for c in curves:
        flags = c.flags
        if flags is None:
            flags = 0
            if c.points:
                f, r, g = c.points[0]
                flags |= (
                    (F_FREQUENCY if f is not None else 0)
                    | (F_RESPONSE if r is not None else 0)
                    | (F_GROUP_DELAY if g is not None else 0)
                )
            if c.parameters:
                flags |= F_PARAMETERS
        body += struct.pack("<2I", c.curve_type, flags)
        if flags & 7:
            body += struct.pack("<I", len(c.points))
        if flags & F_PARAMETERS:
            body += struct.pack("<I", len(c.parameters))
        if flags & 7:
            for f, r, g in c.points:
                if flags & F_FREQUENCY:
                    body += struct.pack("<f", float(f))
                if flags & F_RESPONSE:
                    body += struct.pack("<f", float(r))
                if flags & F_GROUP_DELAY:
                    body += struct.pack("<f", float(g))
        if flags & F_PARAMETERS:
            for k, v in c.parameters.items():
                body += _write_str(str(k)) + _write_str(str(v))
    body += _write_map((parameters or {}).items())
    return MAGIC + bytes((3, 0, 0, 2)) + b"\x00" + bytes(body)


def body_v1(curves: Iterable[Curve], parameters: dict | None = None) -> bytes:
    """3.0.0.0 / 3.0.0.1 body; ``transfer`` and ``delay_ms`` None are written as 0."""
    curves = list(curves)
    body = bytearray(struct.pack("<I", len(curves)))
    for c in curves:
        body += struct.pack(
            "<IddI", c.curve_type, c.transfer or 0.0, c.delay_ms or 0.0, len(c.points)
        )
        for point in c.points:
            body += struct.pack("<3d", *(0.0 if v is None else float(v) for v in point))
    return bytes(body + _write_map((parameters or {}).items()))


def write_v1(
    curves: Iterable[Curve],
    parameters: dict | None = None,
    version: tuple = (3, 0, 0, 1),
) -> bytes:
    """Serialize curves as an unencrypted PEQb 3.0.0.1 blob (the ``.swhp`` version)
    or 3.0.0.0 blob (the ``eqb`` part of a Sonarworks Reference 3 / 4 project)."""
    if version not in ((3, 0, 0, 0), (3, 0, 0, 1)):
        raise ValueError(f"write_v1 writes 3.0.0.0 or 3.0.0.1, not {version}")
    flag = b"" if version == (3, 0, 0, 0) else b"\x00"
    return MAGIC + bytes(version) + flag + body_v1(curves, parameters)


# --------------------------------------------------------------------------
# headphone profiles
# --------------------------------------------------------------------------
def frame_response(frequency: float) -> float:
    """SoundID's frame: 0 dB at 20 Hz and 22 kHz, 15 dB between 40 Hz and 19 kHz."""
    return 15.0 * max(
        0.0,
        min(
            1.0,
            math.log(frequency / 20) / math.log(2),
            math.log(22000 / frequency) / math.log(22000 / 19000),
        ),
    )


def headphone_boost_cap(frequency: float) -> float:
    """SoundID's playback limit on headphone correction boost, dB (read off its graph).

    Profiles store the correction uncapped; SoundID applies this when it plays.
    """
    if frequency < 40:
        return 12.0 * max(0.0, frequency) / 40
    if frequency > 20000:
        return 12.0 * max(0.0, 22000 - frequency) / 2000
    return 12.0


def headphone_curves(left: list, right: list, band_db: float) -> list[Curve]:
    """The 7 curves of a downloaded ``.swhp``.

    ``left`` / ``right``: correction points ``(frequency, dB, group delay s)``.
    """
    curves = [
        Curve(CURVE_TYPE_ID["CorrectionLeft"], left, transfer=0.0, delay_ms=0.0),
        Curve(CURVE_TYPE_ID["CorrectionRight"], right, transfer=0.0, delay_ms=0.0),
        Curve(
            CURVE_TYPE_ID["Frame"],
            [(f, frame_response(f), 0.0) for f, _, _ in left],
            transfer=math.nan,
            delay_ms=math.nan,
        ),
    ]
    for side, points in (("LeftErrorBand", left), ("RightErrorBand", right)):
        for sign in (-1, 1):
            curves.append(
                Curve(
                    CURVE_TYPE_ID[side],
                    [(f, r + sign * band_db, g) for f, r, g in points],
                    transfer=0.0,
                    delay_ms=0.0,
                )
            )
    return curves


def headphone_parameters(
    make: str, model: str, average: bool, band_db: float, mode: str = "Wired"
) -> dict:
    """File parameters of a downloaded ``.swhp``, in their order."""
    flag = lambda b: "true" if b else "false"
    return {
        "META_Manufacturer": make,
        "META_Model": model,
        "META_Mode": mode,
        "META_SonarworksAverage": flag(average),
        "META_SonarworksCalibrated": flag(not average),
        "META_FixedErrorBandRange": f"{band_db:.1f}",
        "META_App": "Reference4+",
        "Headphone_Calibration": "true",
        "META_IsIndividual": flag(not average),
        "META_IsAverage": flag(average),
    }


def key_for(computer_id: str | None = None, key: str | None = None) -> bytes | None:
    """The body key from a hex ``key`` or a ``computer_id``; None if neither is given."""
    if computer_id and key:
        raise ValueError("--computer-id and --key are mutually exclusive")
    if key:
        return bytes.fromhex(key)
    return crypto.swhp_key(computer_id) if computer_id else None


def body_key(computer_id: str | None = None, key: str | None = None) -> bytes | None:
    """``key_for``; without a ``key``, ``computer_id`` defaults to SWHP_COMPUTER_ID."""
    if not key:
        computer_id = computer_id or os.environ.get("SWHP_COMPUTER_ID")
    return key_for(computer_id, key)


def read_local(blob: bytes) -> tuple[Peqb, str]:
    """Decode with each computer ID of this machine (SoundID, Sonarworks Reference 4, Sonarworks Reference 3).

    Returns the container and, when no ID matches, why it is not decoded.
    """
    try:
        ids = computerid.local().values()
    except (OSError, ValueError) as exc:
        return parse_header(blob), f"no key supplied ({exc})"
    for cid in dict.fromkeys(ids.values()):
        try:
            return read(blob, crypto.swhp_key(cid)), ""
        except ValueError:
            pass
    return parse_header(blob), (
        f"no computer ID of this machine matches: "
        f"{', '.join(dict.fromkeys(ids.values()))}"
    )


def decode(blob: bytes, key: bytes | None) -> tuple[Peqb, str]:
    """The container and why the body is not decoded; an encrypted body without
    ``key`` is decoded with this machine's IDs, if one matches."""
    if key is not None or not parse_header(blob).encrypted:
        return read(blob, key), ""
    return read_local(blob)


def open_decoded(
    blob: bytes, computer_id: str | None = None, key: str | None = None
) -> Peqb:
    """Read and decode ``blob``; an encrypted body without a key uses this machine's IDs."""
    p, why = decode(blob, body_key(computer_id, key))
    if not p.decoded:
        raise ValueError(
            f"body is encrypted and no key is given "
            f"(--computer-id / --key / SWHP_COMPUTER_ID); {why}"
        )
    return p


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
POINT_COLUMNS = ["frequency Hz", "response dB", "group delay s"]


def inspect_sections(p: Peqb, blob: bytes, why: str, prefix: str = "") -> list[Section]:
    fields = [
        ("version", p.version_str),
        ("header", f"{p.header_size} bytes ({blob[: p.header_size].hex(' ')})"),
        ("encrypted", p.encrypted),
    ]
    if p.encrypted:
        fields += [
            ("iv", p.iv.hex()),
            (
                "ciphertext",
                f"{p.ciphertext_len} bytes ({p.ciphertext_len // 16} AES blocks)",
            ),
        ]
    else:
        fields.append(("body", f"{p.ciphertext_len} bytes"))
    if not p.decoded:
        fields.append(("body", f"not decoded ({why})"))
        return [Section(f"{prefix}PEQb", fields)]
    # Sonarworks Reference 3 pads the eqb part of a project with zeros.
    padding = " (zeros)" if p.trailing and not p.trailing.strip(b"\0") else ""
    fields += [
        ("curves", len(p.curves)),
        ("trailing", f"{len(p.trailing)} unparsed bytes{padding}"),
    ]
    fields += [(f"param {k}", v) for k, v in p.parameters.items()]
    sections = [Section(f"{prefix}PEQb", fields)]
    for i, c in enumerate(p.curves):
        cf = [
            ("type", f"{c.curve_type} ({c.type_name})"),
            ("points", len(c.points)),
            ("range", _range(c.points)),
        ]
        if c.flags is not None:
            cf.append(("flags", f"{c.flags:#06b}"))
        else:
            cf += [("transfer", c.transfer), ("delay ms", c.delay_ms)]
        cf += [(f"param {k}", v) for k, v in c.parameters.items()]
        sections.append(
            Section(
                f"{prefix}PEQb curve [{i}] {c.type_name}",
                cf,
                Table(POINT_COLUMNS, c.points),
            )
        )
    return sections


def _range(points) -> str:
    points = [p for p in points if p[0] is not None]
    return frequency_range(points)


def _write_csv(p: Peqb, directory: Path) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for i, c in enumerate(p.curves):
        fn = directory / f"curve{i:02d}_{c.type_name}.csv"
        with open(fn, "w", encoding="utf-8") as fh:
            fh.write("frequency_hz,response_db,group_delay_s\n")
            fh.writelines(
                ",".join("" if v is None else repr(v) for v in point) + "\n"
                for point in c.points
            )
        written.append(f"{fn} ({len(c.points)} points)")
    return written


class PeqbInspector(Inspector):
    options = (
        COMPUTER_ID_OPTION,
        KEY_OPTION,
        Option(
            "--csv", type=Path, metavar="DIR", help="write each curve as CSV to DIR"
        ),
    )

    def __init__(
        self,
        computer_id: str | None = None,
        key: str | None = None,
        csv: Path | None = None,
    ):
        self.key = body_key(computer_id, key)
        self.csv = csv

    def inspect(self, path: Path) -> list[Section]:
        blob = Path(path).read_bytes()
        # A mismatch of this machine's IDs is not an error here.
        p, why = decode(blob, self.key)
        sections = [file_section(path, blob), *inspect_sections(p, blob, why)]
        if self.csv is not None:
            if not p.decoded:
                raise ValueError(
                    f"cannot write CSV: body is encrypted, {why} "
                    "(--computer-id / --key / SWHP_COMPUTER_ID)"
                )
            sections.append(
                Section("written", [("csv", w) for w in _write_csv(p, self.csv)])
            )
        return sections


FORMAT = Format(
    "peqb",
    (".swhp", ".eqb"),
    "SoundID PEQb curve container (headphone profile, export)",
    PeqbInspector,
)
