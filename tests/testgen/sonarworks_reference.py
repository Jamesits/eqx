"""Sonarworks Reference 3 and 4 projects, PEQb 2.x and ``PEQB`` exports, headphone profile."""

from __future__ import annotations

import gzip
import struct

from eqx import fmath
from eqx.model import EPOCH, standard_grid
from eqx.soundid import crypto, peqb, swproj

from .common import SIDES, ZERO_IV, peak, rounded_points
from .soundid import TILT_HEADPHONE, headphone_curves, write_peqb_v1

SONARWORKS_PROJ_DIR, SONARWORKS_PEQB_DIR = (
    "sonarworks-reference/swproj",
    "sonarworks-reference/peqb",
)
# All-zero Sonarworks Reference 3 computer ID; it has no "g" prefix.
SONARWORKS_REFERENCE3_COMPUTER_ID = "0" * 40
SONARWORKS_REFERENCE_SPEAKER = {
    "Left": [peak(60, 4, 0.7), peak(3000, -3, 2)],
    "Right": [peak(80, 5, 0.9), peak(9000, 3, 1.5)],
}
# Listening spot per side: (delay ms, gain dB).
SONARWORKS_REFERENCE_SPOT = {"Left": (0.0, -0.5), "Right": (0.15, 0.0)}
# Sonarworks Reference 3 keys its curve parameters by obfuscated enum names.
SONARWORKS_REFERENCE3_TRANSFER = "c9446c5af52167465783108fe622fefc2"
SONARWORKS_REFERENCE3_DELAY = "c371fb88e082a6f1c53faf3a060cf6a1f"
SONARWORKS_REFERENCE4_TIME = "1970-01-01T00:00:00.000000"
GRID_NAME = "LOG_355_20_22000"


def sonarworks_reference_curves(grid=None) -> dict:
    """{side: (measurement, correction)}: (frequency, dB, group delay) points."""
    out = {}
    for side, sections in SONARWORKS_REFERENCE_SPEAKER.items():
        measurement = rounded_points(sections, grid)
        out[side] = (measurement, [(f, -r, -g) for f, r, g in measurement])
    return out


def sonarworks_reference_eqb_curves() -> list:
    """CorrectionLeft, CorrectionRight, MeasurementLeft, MeasurementRight, as
    Reference writes them: correction gain relative to the louder side."""
    curves = sonarworks_reference_curves()
    top = max(gain for _, gain in SONARWORKS_REFERENCE_SPOT.values())
    out = [
        peqb.Curve(
            peqb.CURVE_TYPE_ID["Correction" + side],
            curves[side][1],
            transfer=SONARWORKS_REFERENCE_SPOT[side][1] - top,
            delay_ms=SONARWORKS_REFERENCE_SPOT[side][0],
        )
        for side in SIDES
    ]
    return out + [
        peqb.Curve(
            peqb.CURVE_TYPE_ID["Measurement" + side],
            curves[side][0],
            transfer=0.0,
            delay_ms=0.0,
        )
        for side in SIDES
    ]


def _container(header: str, eqb: bytes, xml: bytes) -> bytes:
    payload = crypto.encrypt(crypto.derive_key(), gzip.compress(xml, mtime=0), ZERO_IV)
    return header.encode() + bytes([swproj.HEADER_TERMINATOR]) + eqb + payload


def _kv(items) -> str:
    return "".join(
        f"<a:KeyValueOfstringstring><a:Key>{k}</a:Key><a:Value>{v}</a:Value>"
        "</a:KeyValueOfstringstring>"
        for k, v in items
    )


def write_sonarworks_reference4_project() -> bytes:
    """As Sonarworks Reference 4 Measure writes it: one-line header, ``swproj`` listed
    first, curve points without a list wrapper, the ``a:`` prefix undeclared."""
    time = SONARWORKS_REFERENCE4_TIME
    stamp = (
        f"<DbId>0</DbId><DbModificationTime>{time}</DbModificationTime>"
        f"<ModificationTime>{time}</ModificationTime>"
    )
    curves = sonarworks_reference_curves()
    spot = SONARWORKS_REFERENCE_SPOT

    def curve(ctype, points, params):
        pts = "".join(
            f"<AflPoint><Frequency>{f!r}</Frequency><Response>{r!r}</Response>"
            f"<GroupDelay>{g!r}</GroupDelay></AflPoint>"
            for f, r, g in points
        )
        return (
            f"<Curve>{stamp}<Id></Id><Name></Name><CurveType>{ctype}</CurveType>"
            f"<Points>{pts}</Points><Parameters>{_kv(params)}</Parameters></Curve>"
        )

    xml_curves = [
        curve("Measurement" + side, curves[side][0], [("Frequency", GRID_NAME)])
        for side in SIDES
    ]
    xml_curves += [
        curve(
            "Correction" + side,
            curves[side][1],
            [
                ("Transfer", f"{spot[side][1]:f}"),
                ("Delay", f"{spot[side][0] / 1000:f}"),
                ("Frequency", GRID_NAME),
            ],
        )
        for side in SIDES
    ]
    grid = "".join(f"<Value>{f!r}</Value>" for f in standard_grid())
    points = "".join(
        f"<RoomPoint><Channel>{side}</Channel><Distance>0</Distance>"
        "<DistanceInSamples>0</DistanceInSamples><OutputLevel>0</OutputLevel>"
        "<nInputLevel>0</nInputLevel></RoomPoint>"
        for side in SIDES
    )
    room_params = [
        ("SampleRate", "48000"),
        ("RoomReverbation", "0"),
        ("SessionId", "0"),
        ("MeasuredDistanceBetweenSpeakers", "0"),
        ("DistanceBetweenSpeakers", "0"),
    ]
    xml = (
        f'<Project xmlns="{swproj.NS["s"]}" xmlns:i="{swproj.NS["i"]}">{stamp}'
        f'<FrequencyCollections><a:FrequencyCollection xmlns:a="{swproj.NS["p"]}">'
        f"<CollectionInfo><Custom>false</Custom><Name>{GRID_NAME}</Name>"
        "<AssignedName>Default 355</AssignedName><StartFrequency>20</StartFrequency>"
        "<EndFrequency>22000</EndFrequency><PointCount>355</PointCount>"
        f"<Logarithmic>true</Logarithmic></CollectionInfo><Frequencies>{grid}</Frequencies>"
        f"</a:FrequencyCollection></FrequencyCollections><Curves>{''.join(xml_curves)}</Curves>"
        f"<Values></Values><Measurements></Measurements><FrequencyName>{GRID_NAME}</FrequencyName>"
        "<CurveCollections></CurveCollections><MeasurementCollections></MeasurementCollections>"
        f"<RoomMeasurementCollections><RoomMeasurementCollection>{stamp}<Name></Name>"
        f"<Measurements><RoomMeasurement><Points>{points}</Points><Time>{time}</Time>"
        f"<Measurements></Measurements><Parameters>{_kv([('MeasurementType', 'MainPoint')])}"
        f"</Parameters></RoomMeasurement></Measurements><Parameters>{_kv(room_params)}"
        f"</Parameters><FrequencyName>{GRID_NAME}</FrequencyName></RoomMeasurementCollection>"
        "</RoomMeasurementCollections><Name>New Project</Name><Id></Id></Project>"
    ).encode()
    eqb = peqb.write_v1(sonarworks_reference_eqb_curves(), version=(3, 0, 0, 0))
    header = (
        f'<ProjectHeader xmlns="{swproj.NS["s"]}"><Version>3.0.0.0</Version><SupportedVersions/>'
        "<Compressed>true</Compressed><Encrypted>true</Encrypted>"
        "<PasswordProtected>false</PasswordProtected><Parts><ProjectHeaderPart><Type>swproj</Type>"
        "<Size>-1</Size></ProjectHeaderPart><ProjectHeaderPart><Type>eqb</Type>"
        f"<Size>{len(eqb)}</Size></ProjectHeaderPart></Parts></ProjectHeader>"
    )
    return _container(header, eqb, xml)


def write_sonarworks_reference3_project() -> bytes:
    """As Sonarworks Reference 3 writes it: DataContract XML, ``eqb`` listed first, PEQb
    3.0.0.1, and the eqb part is the whole stream buffer, zero padded."""
    curves = sonarworks_reference_curves()
    spot = SONARWORKS_REFERENCE_SPOT

    def curve(n, ctype, side, points, params):
        pts = "".join(
            f"<AflPoint><Frequency>{f!r}</Frequency><GroupDelay>{g!r}</GroupDelay>"
            f"<Response>{r!r}</Response></AflPoint>"
            for f, r, g in points
        )
        kv = (
            f'<Parameters xmlns:a="{swproj.NS["a"]}">{_kv(params)}</Parameters>'
            if params
            else f'<Parameters xmlns:a="{swproj.NS["a"]}"/>'
        )
        return (
            '<Curve><DbId i:nil="true"/><DbModificationTime i:nil="true"/>'
            f"<ModificationTime>{EPOCH}</ModificationTime><CurveType>{ctype}</CurveType>"
            f"<Id>00000000-0000-0000-0000-00000000000{n}</Id><Name>{side}</Name>{kv}"
            f'<Points xmlns:a="{swproj.NS["l"]}"><a:list>{pts}</a:list></Points></Curve>'
        )

    xml_curves = [
        curve(i + 1, "Measurement" + side, side, curves[side][0], [])
        for i, side in enumerate(SIDES)
    ]
    xml_curves += [
        curve(
            i + 3,
            "Correction" + side,
            side,
            curves[side][1],
            [
                (SONARWORKS_REFERENCE3_TRANSFER, f"{spot[side][1]:g}"),
                (SONARWORKS_REFERENCE3_DELAY, f"{spot[side][0] / 1000:g}"),
            ],
        )
        for i, side in enumerate(SIDES)
    ]
    xml = (
        f'<Project xmlns="{swproj.NS["s"]}" xmlns:i="{swproj.NS["i"]}"><DbId i:nil="true"/>'
        f'<DbModificationTime i:nil="true"/><ModificationTime>{EPOCH}</ModificationTime>'
        f"<CurveCollections/><Curves>{''.join(xml_curves)}</Curves>"
        f'<FrequencyCollections xmlns:a="{swproj.NS["p"]}"/><FrequencyName i:nil="true"/>'
        "<Id>00000000-0000-0000-0000-000000000000</Id><MeasurementCollections/><Measurements/>"
        "<Name>Tilt</Name><RoomMeasurementCollections/>"
        f'<Values xmlns:a="{swproj.NS["a"]}"/></Project>'
    ).encode()
    eqb = peqb.write_v1(sonarworks_reference_eqb_curves())
    # Sonarworks Reference 3 stores MemoryStream.GetBuffer(): capacity 256, doubled as needed.
    capacity = 256
    while capacity < len(eqb):
        capacity *= 2
    eqb += bytes(capacity - len(eqb))
    header = (
        f'<ProjectHeader xmlns="{swproj.NS["s"]}" xmlns:i="{swproj.NS["i"]}">'
        f'<Version>3.0.0.0</Version><SupportedVersions xmlns:a="{swproj.NS["a"]}"/>'
        "<Compressed>true</Compressed><Encrypted>true</Encrypted>"
        "<PasswordProtected>false</PasswordProtected><Parts><ProjectHeaderPart><Type>eqb</Type>"
        f"<Size>{len(eqb)}</Size></ProjectHeaderPart><ProjectHeaderPart><Type>swproj</Type>"
        "<Size>-1</Size></ProjectHeaderPart></Parts></ProjectHeader>"
    )
    return _container(header, eqb, xml)


def _spot_settings() -> bytes:
    """Gain (percent) and delay (ms) of the left and right side, as PEQb 2.x stores them."""
    spot = SONARWORKS_REFERENCE_SPOT
    return struct.pack(
        "<4d",
        *(100 * fmath.pow(10, spot[s][1] / 20) for s in SIDES),
        *(spot[s][0] for s in SIDES),
    )


def write_peqb_2013() -> bytes:
    """2.0.13.30: gains, delays, then the left and right correction tables."""
    curves = sonarworks_reference_curves()
    out = peqb.MAGIC + bytes((2, 0, 13, 30)) + _spot_settings()
    for side in SIDES:
        points = curves[side][1]
        out += struct.pack("<I", len(points)) + b"".join(
            struct.pack("<3d", *p) for p in points
        )
    return out


def write_peqb_legacy() -> bytes:
    """``PEQB``: left and right corrections on the quadratic grid, then settings."""
    grid = peqb.legacy_grid(355)
    curves = sonarworks_reference_curves(grid)
    out = peqb.LEGACY_MAGIC + struct.pack("<d", len(grid))
    for (_, left, _), (_, right, _) in zip(curves["Left"][1], curves["Right"][1]):
        out += struct.pack("<2d", left, right)
    out += _spot_settings()
    # Playback settings Sonarworks Reference 3 writes; the readers ignore them.
    return out + struct.pack("<6dq", 15, 15, 2000, 1000, 400, 100, 4)


def files() -> dict[str, bytes]:
    body = peqb.body_v1(sonarworks_reference_eqb_curves())
    proj, eqb = SONARWORKS_PROJ_DIR, SONARWORKS_PEQB_DIR
    return {
        f"{proj}/Tilt Sonarworks Reference 4.swproj": write_sonarworks_reference4_project(),
        f"{proj}/Tilt Sonarworks Reference 3.swproj": write_sonarworks_reference3_project(),
        f"{eqb}/Tilt 2.0.13.30.eqb": write_peqb_2013(),
        # 2.0.14.10 ends after the curves; the empty map is 4 bytes.
        f"{eqb}/Tilt 2.0.14.10.eqb": peqb.MAGIC + bytes((2, 0, 14, 10)) + body[:-4],
        f"{eqb}/Tilt 2.1.3.14.eqb": peqb.MAGIC
        + bytes((2, 1, 3, 14))
        + peqb.body_v1(
            sonarworks_reference_eqb_curves(), {"META_SonarworksCalibrated": "true"}
        ),
        f"{eqb}/Tilt PEQB.eqb": write_peqb_legacy(),
        f"{eqb}/Tilt Tilt Wired Average.swhp": write_peqb_v1(
            headphone_curves(TILT_HEADPHONE, 3.0),
            peqb.headphone_parameters("Tilt", "Tilt", True, 3.0),
            (3, 0, 0, 1),
            SONARWORKS_REFERENCE3_COMPUTER_ID,
        ),
    }
