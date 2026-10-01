"""Reader and writer for SoundID Reference and Sonarworks Reference 3 / 4
measurement projects (``*.swproj``).

Layout: plain-text XML ``ProjectHeader``, one ESC byte, then the parts listed
in the header.  The ``swproj`` part is the project XML (gzip, then AES-128-CBC);
the optional ``eqb`` part is a PEQb blob (see :mod:`eqx.soundid.peqb`).
"""

from __future__ import annotations

import base64
import binascii
import gzip
import os
import re
import struct
import wave
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path

from ..fileformat import Format, Inspector, file_section, frequency_range
from ..model import MicProfile
from ..options import Option
from ..report import Section, Table
from . import crypto, peqb

HEADER_TERMINATOR = 0x1B  # ESC, ends the plain-text ProjectHeader
NS = {
    "s": "http://www.sonarworks.com",
    "i": "http://www.w3.org/2001/XMLSchema-instance",
    "a": "http://schemas.microsoft.com/2003/10/Serialization/Arrays",
    "l": "http://schemas.datacontract.org/2004/07/System.Collections.ObjectModel",
    "p": "http://schemas.datacontract.org/2004/07/Sonarworks.Project",
}
VERSION = "3.0.0.2"
SUPPORTED_VERSIONS = ("3.0.0.0", "3.0.0.1")
# The version Sonarworks Reference 3 and 4 write.
SONARWORKS_REFERENCE_VERSION = "3.0.0.0"
_ROOT = re.compile(rb"<Project(?=[\s>])")


def parse_xml(raw: bytes) -> ET.Element:
    """Parse project XML.

    Sonarworks Reference 4 Measure uses the ``a:`` prefix in parameters without declaring
    it; it is bound to the Arrays namespace on the root.
    """
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        if "unbound prefix" not in str(exc):
            raise
    m = _ROOT.search(raw)
    if m is None:
        raise ValueError("project XML has an unbound prefix and no <Project> root")
    declared = m.group() + f' xmlns:a="{NS["a"]}"'.encode()
    return ET.fromstring(raw[: m.start()] + declared + raw[m.end() :])


def _inflate(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":  # gzip
        return zlib.decompress(data, 16 + zlib.MAX_WBITS)
    if data[:1] == b"\x78":  # zlib
        return zlib.decompress(data)
    return zlib.decompress(data, -zlib.MAX_WBITS)  # raw deflate


# --------------------------------------------------------------------------
# reader
# --------------------------------------------------------------------------
@dataclass
class ProjectHeader:
    version: str
    supported_versions: list
    compressed: bool
    encrypted: bool
    password_protected: bool
    parts: list  # [(type, declared_size)]
    text: str
    size: int  # bytes consumed, terminator included


def parse_header(blob: bytes) -> ProjectHeader:
    end = blob.find(bytes([HEADER_TERMINATOR]))
    if end < 0:
        raise ValueError("no 0x1B header terminator - not a swproj file")
    text = blob[:end].decode("utf-8")
    root = ET.fromstring(text)

    def txt(tag):
        e = root.find("s:" + tag, NS)
        return e.text if e is not None else None

    def flag(tag):
        return (txt(tag) or "false").strip().lower() == "true"

    parts = []
    for p in root.findall("s:Parts/s:ProjectHeaderPart", NS):
        parts.append(
            (
                p.findtext("s:Type", namespaces=NS),
                int(p.findtext("s:Size", namespaces=NS)),
            )
        )

    return ProjectHeader(
        version=txt("Version"),
        supported_versions=[
            e.text for e in root.findall("s:SupportedVersions/a:string", NS)
        ],
        compressed=flag("Compressed"),
        encrypted=flag("Encrypted"),
        password_protected=flag("PasswordProtected"),
        parts=parts,
        text=text,
        size=end + 1,
    )


def split_parts(blob: bytes, header: ProjectHeader) -> dict:
    """Return {part_type: (offset, size)}.

    Physical order is: every part with an explicit size, in <Parts> order,
    followed by the single part declared with Size == -1, which takes the
    remainder of the file.  (The reader seeks past sized parts, then reads
    whatever is left.)
    """
    off = header.size
    out, deferred = {}, None
    for ptype, size in header.parts:
        if size == -1:
            if deferred is not None:
                raise ValueError("more than one Size=-1 part")
            deferred = ptype
        else:
            out[ptype] = (off, size)
            off += size
    if deferred is not None:
        out[deferred] = (off, len(blob) - off)
    return out


PASSWORD_OPTION = Option("--password", help="project password (or SWPROJ_PASSWORD)")


def password_bytes(text: str | None) -> bytes | None:
    """The project password: ``text``, else SWPROJ_PASSWORD; None for the default."""
    text = text or os.environ.get("SWPROJ_PASSWORD")
    return text.encode() if text else None


def _derive_key(password: bytes | None) -> bytes:
    return crypto.derive_key(
        password if password is not None else crypto.DEFAULT_PASSWORD
    )


def _key(header: ProjectHeader, password: bytes | None) -> bytes:
    if header.password_protected and password is None:
        raise ValueError("project is password protected; supply the password")
    return _derive_key(password)


def decode_part(
    data: bytes, header: ProjectHeader, password: bytes | None = None
) -> bytes:
    """Decrypt (AES-128-CBC) and/or decompress (gzip) a part payload."""
    if header.encrypted:
        data = crypto.decrypt(_key(header, password), data)
    if header.compressed:
        data = _inflate(data)
    return data


class SwProj:
    def __init__(self, blob: bytes, password: bytes | None = None):
        self.blob = blob
        self.header = parse_header(blob)
        self.layout = split_parts(blob, self.header)
        self._password = password
        self._xml = None
        self._eqb = None

    @classmethod
    def open(cls, path, password: bytes | None = None) -> SwProj:
        return cls(Path(path).read_bytes(), password)

    def part(self, name: str) -> bytes:
        off, size = self.layout[name]
        return self.blob[off : off + size]

    @property
    def xml(self) -> bytes:
        if self._xml is None:
            self._xml = decode_part(self.part("swproj"), self.header, self._password)
        return self._xml

    @property
    def eqb(self) -> peqb.Peqb | None:
        """The embedded PEQb.  It is never compressed; when encrypted it uses the project key."""
        if "eqb" not in self.layout:
            return None
        if self._eqb is None:
            self._eqb = peqb.read(self.part("eqb"), _key(self.header, self._password))
        return self._eqb

    def tree(self) -> ET.Element:
        return parse_xml(self.xml)

    def recordings(self):
        """Yield (index, params, float32 samples) for every <RawData> present."""
        raw = self.xml.decode("utf-8")
        for i, m in enumerate(
            re.finditer(r"<Measurement>(.*?)</Measurement>", raw, re.DOTALL)
        ):
            body = m.group(1)
            rd = re.search(r"<RawData>(.*?)</RawData>", body, re.DOTALL)
            if not rd:
                continue
            pcm = base64.b64decode(rd.group(1))
            params = dict(
                re.findall(r"<a:Key>(.*?)</a:Key>\s*<a:Value>(.*?)</a:Value>", body)
            )
            ch = re.search(r"<Channel>(.*?)</Channel>", body)
            params["Channel"] = ch.group(1) if ch else ""
            yield (
                i,
                params,
                struct.unpack(f"<{len(pcm) // 4}f", pcm[: len(pcm) // 4 * 4]),
            )


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
def write(
    xml: bytes,
    eqb: bytes | None = None,
    password: bytes | None = None,
    iv: bytes = bytes(16),
    version: str = VERSION,
) -> bytes:
    """Build a compressed, encrypted container.

    ``password`` None uses the built-in default and marks the project as not
    password protected.  ``version`` ``SONARWORKS_REFERENCE_VERSION`` lists the ``eqb``
    part first and no supported versions, as Sonarworks Reference 3 does.
    """
    if version not in (VERSION, SONARWORKS_REFERENCE_VERSION):
        raise ValueError(
            f"project version must be {VERSION} or {SONARWORKS_REFERENCE_VERSION}"
        )
    key = _derive_key(password)
    # A fixed IV keeps the output reproducible.  SoundID also writes projects
    # with a zero IV; the key is public, so a random IV protects nothing.
    swproj = crypto.encrypt(key, gzip.compress(xml, mtime=0), iv)
    parts = [
        "\t\t<ProjectHeaderPart><Type>swproj</Type><Size>-1</Size></ProjectHeaderPart>\n"
    ]
    if eqb is not None:
        part = f"\t\t<ProjectHeaderPart><Type>eqb</Type><Size>{len(eqb)}</Size></ProjectHeaderPart>\n"
        # The Sonarworks Reference 3 plug-in adds every earlier Size, -1 included, to the
        # eqb offset, so there the eqb part must be listed first.
        parts.insert(0 if version == SONARWORKS_REFERENCE_VERSION else 1, part)
    supported = "".join(
        f"\t\t<a:string>{v}</a:string>\n"
        for v in SUPPORTED_VERSIONS
        if version == VERSION
    )
    header = (
        f'<ProjectHeader xmlns="{NS["s"]}" xmlns:a="{NS["a"]}">\n'
        f"\t<Version>{version}</Version>\n"
        f"\t<SupportedVersions>\n{supported}\t</SupportedVersions>\n"
        "\t<Compressed>true</Compressed>\n"
        "\t<Encrypted>true</Encrypted>\n"
        f"\t<PasswordProtected>{'false' if password is None else 'true'}</PasswordProtected>\n"
        f"\t<Parts>\n{''.join(parts)}\t</Parts>\n"
        "</ProjectHeader>\n\n"
    ).encode()
    # The Size=-1 part is physically last, after every sized part.
    return header + bytes([HEADER_TERMINATOR]) + (eqb or b"") + swproj


# --------------------------------------------------------------------------
# curves
# --------------------------------------------------------------------------
def key_values(
    element: ET.Element, path: str = "a:KeyValueOfstringstring"
) -> list[tuple[str, str]]:
    """The DataContract string pairs at ``path`` below ``element``, in order."""
    return [
        (kv.findtext("a:Key", namespaces=NS), kv.findtext("a:Value", namespaces=NS))
        for kv in element.findall(path, NS)
    ]


def _curve_params(curve: ET.Element) -> dict[str, str]:
    return dict(key_values(curve, "s:Parameters/a:KeyValueOfstringstring"))


def mic_profiles(project: SwProj) -> list[MicProfile]:
    """The microphone tables SoundID used for the measurement.

    They are stored decrypted, as ``Correction`` curves named ``<serial> <angle>``
    with parameter ``MicDegrees = <angle>``.
    """
    profiles = []
    for curve in project.tree().findall("s:Curves/s:Curve", NS):
        if curve.findtext("s:CurveType", namespaces=NS) != "Correction":
            continue
        params = _curve_params(curve)
        angle = params.get("MicDegrees")
        if not angle:
            continue
        name = curve.findtext("s:Name", default="", namespaces=NS)
        serial = name[: -len(angle)].strip() if name.endswith(angle) else name
        points = [
            (
                float(p.findtext("s:Frequency", namespaces=NS)),
                float(p.findtext("s:Response", namespaces=NS)),
            )
            for p in _afl_points(curve.find("s:Points", NS))
        ]
        profiles.append(MicProfile.from_points(serial, angle, points))
    return profiles


# Curve types of the measurements of a Sonarworks Reference 3 / 4 project.
SIDE_MEASUREMENTS = {"MeasurementLeft": "Left", "MeasurementRight": "Right"}


def measurement_curves(project: SwProj) -> dict[str, list[tuple]]:
    """{channel name: points} of the measurement curves (level-normalized responses).

    SoundID names a ``Measurement`` by its ``ChannelName``; Sonarworks Reference 3 / 4
    write ``MeasurementLeft`` and ``MeasurementRight``.
    """
    curves = {}
    for curve in project.tree().findall("s:Curves/s:Curve", NS):
        ctype = curve.findtext("s:CurveType", namespaces=NS)
        if ctype == "Measurement":
            name = _curve_params(curve).get("ChannelName", "")
        elif ctype in SIDE_MEASUREMENTS:
            name = SIDE_MEASUREMENTS[ctype]
        else:
            continue
        curves[name] = _points(curve.find("s:Points", NS))
    return curves


def mic_profile(project: SwProj, angle: str) -> MicProfile:
    profiles = mic_profiles(project)
    for profile in profiles:
        if profile.angle == angle:
            return profile
    available = ", ".join(p.angle for p in profiles) or "none"
    raise ValueError(
        f"project has no microphone {angle!r} table; available: {available}"
    )


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _afl_points(elem: ET.Element) -> list[ET.Element]:
    """The ``AflPoint``s of a ``Points`` element; Sonarworks Reference 4 writes curve points
    without the ``list`` wrapper."""
    return elem.findall("l:list/s:AflPoint", NS) or elem.findall("s:AflPoint", NS)


def _points(elem: ET.Element) -> list[tuple]:
    return [
        tuple(
            float(p.findtext(f"s:{name}", default="nan", namespaces=NS))
            for name in ("Frequency", "Response", "GroupDelay")
        )
        for p in _afl_points(elem)
    ]


def _raw_data(text: str | None) -> str:
    if not text:
        return "empty"
    try:
        n = len(base64.b64decode(text))
    except (binascii.Error, ValueError):
        return f"{len(text)} characters, not base64"
    return f"{n:,} bytes ({n // 4:,} float32 samples)"


def _xml_sections(elem: ET.Element, title: str, trail: str = "") -> list[Section]:
    """One section per element with scalar content; containers are walked.

    ``trail`` holds the indexed ancestors, e.g. ``RoomMeasurement[3]/``; the
    unindexed wrappers (``Curves``, ``Measurements``) are left out of titles.
    """
    fields, table, children = [], None, []
    counts: dict[str, int] = {}
    for child in elem:
        counts[_local(child.tag)] = counts.get(_local(child.tag), 0) + 1
    seen: dict[str, int] = {}
    for child in elem:
        tag = _local(child.tag)
        index = seen[tag] = seen.get(tag, -1) + 1
        label = f"{tag}[{index}]" if counts[tag] > 1 else tag
        if tag == "RawData":
            fields.append((tag, _raw_data(child.text)))
        elif len(child) == 0:
            fields.append((tag, (child.text or "").strip()))
        elif tag == "Parameters":
            fields += [(f"param {key}", value) for key, value in key_values(child)]
        elif tag == "Points" and _afl_points(child):
            table = Table(peqb.POINT_COLUMNS, _points(child))
            fields += [
                ("points", len(table.rows)),
                ("range", frequency_range(table.rows)),
            ]
        elif len({_local(c.tag) for c in child}) == 1 and all(
            len(c) == 0 for c in child
        ):
            values = [(c.text or "").strip() for c in child]
            fields.append((tag, f"{len(values)} values: {' '.join(values)}"))
        else:
            children.append((child, label))
    sections = []
    if fields or table:
        name = elem.findtext("s:Name", namespaces=NS)
        heading = trail + title
        sections.append(
            Section(f"{heading} {name}" if name else heading, fields, table)
        )
    if title.endswith("]"):
        trail += title + "/"
    for child, label in children:
        sections += _xml_sections(child, label, trail)
    return sections


def _write_wav(path: Path, samples, rate: int = 48000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(
                struct.pack("<h", max(-32768, min(32767, int(s * 32767))))
                for s in samples
            )
        )


class SwprojInspector(Inspector):
    options = (
        PASSWORD_OPTION,
        Option(
            "--xml", type=Path, metavar="FILE", help="write the project XML to FILE"
        ),
        Option(
            "--wav",
            type=Path,
            metavar="DIR",
            help="write each recording (RawData) as WAV to DIR",
        ),
    )

    def __init__(
        self,
        password: str | None = None,
        xml: Path | None = None,
        wav: Path | None = None,
    ):
        self.password = password_bytes(password)
        self.xml = xml
        self.wav = wav

    def inspect(self, path: Path) -> list[Section]:
        proj = SwProj.open(path, self.password)
        h = proj.header
        declared = dict(h.parts)
        container = Section(
            "container",
            [
                ("version", h.version),
                ("supported versions", ", ".join(h.supported_versions)),
                ("compressed", h.compressed),
                ("encrypted", h.encrypted),
                ("password protected", h.password_protected),
                ("payload XML", f"{len(proj.xml):,} bytes"),
            ],
            Table(
                ["part", "offset", "size", "declared size"],
                [(t, o, s, declared[t]) for t, (o, s) in proj.layout.items()],
            ),
            raw=h.text,
        )
        sections = [file_section(Path(path), proj.blob), container]
        sections += _xml_sections(proj.tree(), "Project")
        if proj.eqb is not None:
            blob = proj.part("eqb")
            sections += peqb.inspect_sections(proj.eqb, blob, "", prefix="eqb part: ")

        written = []
        if self.xml is not None:
            self.xml.parent.mkdir(parents=True, exist_ok=True)
            self.xml.write_bytes(proj.xml)
            written.append(("xml", f"{self.xml} ({len(proj.xml):,} bytes)"))
        if self.wav is not None:
            self.wav.mkdir(parents=True, exist_ok=True)
            for i, params, samples in proj.recordings():
                ch = params.get("Channel") or params.get("ChannelIndex") or "x"
                fn = self.wav / f"rec{i:03d}_ch{ch}.wav"
                _write_wav(fn, samples)
                written.append(("wav", f"{fn} ({len(samples)} samples)"))
        if written:
            sections.append(Section("written", written))
        return sections


FORMAT = Format(
    "swproj",
    (".swproj",),
    "SoundID / Sonarworks Reference 3, 4 measurement project",
    SwprojInspector,
)
