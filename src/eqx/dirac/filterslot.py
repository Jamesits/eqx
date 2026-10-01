"""Reader and writer for Dirac Live Processor filter slots (``filter.bin``).

The processor keeps one file per slot in
``%APPDATA%\\Dirac\\Dirac_Live_Processor\\filters\\<slot>\\filter.bin``
(macOS: ``~/Library/Application Support/Dirac/Dirac_Live_Processor/...``).

Layout, integers little-endian::

    "CARD" "RTRP" u32 version (1 or 2) [u32 signed (version 2)]
    payload

Payload: a ``dio.endpoint.FilterSlot`` protobuf.  Signed payload: ``FX F1 1F``,
u8 1, u16 BE signature length (256), RSA-PSS SHA-256 signature, u32 BE key
id, then the protobuf; the signature covers the key id and the protobuf.
Dirac's cloud signs the filters it calculates; the processor writes
unsigned files itself and loads both.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import protobuf
from ..fileformat import Format, Inspector, file_section
from ..report import Section, Table

MAGIC, PRODUCT = b"CARD", b"RTRP"
SIGNED_MAGIC = b"FX\xf1\x1f"
SIGNATURE_SIZE = 256
FILE_NAME = "filter.bin"
NUM_SLOTS = 8

# Embedded in DiracLiveProcessor.exe; verifies signed slots of key id 1.
PUBLIC_KEYS = {
    1: """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAp0TIKV+1Mhd3of6RIC5f
vENNV5FNBwogmTN+47bLcMybuqAQxbMoSsXyvJUsn3XJTx2tLXpklpowU5yFoCQ/
GjaPi4HlqND9mzpTkpjoHd22Rk1R9h89ilGRZFeAr5eJfOoZQR/mKgzz0aplCAlZ
xwvrxuJJbL38naIrTPH/2P0vWGIb75cq+JScednZLmluHWp1E8EmMJFK3a5QBJjG
3H06TdSXOowkboLqfRw4+ReVVWW8Un2Klw0vPO5+D6hQveZ6TO2qecJCfqqYH4co
o2EHcwHqrD1+QGM4aGfe9bfJ+tMxiWHwKd1KKQOf2dnL5eqe3ogN3GoNfQFSPD7U
bQIDAQAB
-----END PUBLIC KEY-----
"""
}

FILTER_TYPES = (
    "FIR_FILTER",
    "IIR_FILTER",
    "FIIR_FILTER",
    "DUAL_RATE_FILTER",
    "PARAMETRIC_IIR_FILTER",
    "DELAYGAIN_FILTER",
    "MULTI_RATE_FILTER",
    "RESAMPLE_FILTER",
)
BIQUAD_TYPES = (
    "Allpass",
    "PeakNotch",
    "HP",
    "LP",
    "HighShelf",
    "LowShelf",
    "BandPass",
    "BandStop",
)
DISPOSITIONS = (
    "UNDEFINED",
    "LIVE",
    "BASS_MANAGEMENT",
    "INPUT_EQ",
    "OUTPUT_EQ",
    "SPEAKER_ALIGNMENT",
    "INPUT_GAIN_DELAY",
)
LIVE = 1

# The dio.endpoint messages a slot uses, from the descriptors in the processor.
SCHEMA: protobuf.Schema = {
    "UUID": {"low": (1, "uint64", ""), "high": (2, "uint64", "")},
    "SlotMetadata": {
        "endpoint_manufacturer": (1, "string", ""),
        "endpoint_model": (2, "string", ""),
        "endpoint_uuid": (3, "UUID", ""),
        "required_capabilities": (4, "int64", "repeated"),
        "extra": (5, "string", "map:string"),
        "is_trial_filter": (6, "bool", ""),
    },
    "SlotInfo": {
        "slot_index": (1, "int32", ""),
        "name": (2, "string", ""),
        "filter_type_name": (3, "string", ""),
        "num_inputs": (4, "int32", ""),
        "num_outputs": (5, "int32", ""),
        "max_delay": (6, "int32", ""),
        "min_gain": (7, "float", ""),
        "max_gain": (8, "float", ""),
        "num_sections": (9, "int32", ""),
        "creation_time": (10, "int64", ""),
        "unique_id": (11, "UUID", ""),
        "description": (12, "string", ""),
        "sub_sampling_factor": (13, "int32", "repeated"),
        "slot_metadata": (15, "SlotMetadata", ""),
    },
    "FIRInfo": {
        "variant": (1, "int32", ""),
        "samplerate": (2, "float", ""),
        "num_fir_taps": (3, "int32", ""),
    },
    "IIRInfo": {
        "variant": (1, "int32", ""),
        "samplerate": (2, "float", ""),
        "num_biquads": (3, "int32", ""),
    },
    "FIIRInfo": {
        "variant": (1, "int32", ""),
        "samplerate": (2, "float", ""),
        "num_fir_taps": (3, "int32", ""),
        "num_biquads": (4, "int32", ""),
    },
    "DelayGainInfo": {
        "variant": (1, "int32", ""),
        "samplerate": (2, "float", ""),
        "max_delay": (3, "int32", ""),
        "max_gain": (4, "float", ""),
    },
    "DRFIRInfo": {
        "variant": (1, "int32", ""),
        "samplerate": (2, "float", ""),
        "num_fir_taps_hi": (3, "int32", ""),
        "num_fir_taps_lo": (4, "int32", ""),
        "sub_sampling_factor": (5, "int32", ""),
        "cascade_number": (6, "int32", ""),
        "delay_hi_min": (7, "int32", ""),
        "delay_hi_max": (8, "int32", ""),
        "allow_individual_delays": (9, "bool", ""),
    },
    "DRFIRCOMMON": {
        "info": (1, "DRFIRInfo", ""),
        "low_pass": (2, "FIR", ""),
        "section_index": (3, "int32", ""),
    },
    "MRFIRLayerInfo": {
        "num_fir_taps": (1, "int32", ""),
        "max_bulk_delay": (2, "int32", ""),
        "resample_info_variant": (3, "int32", ""),
    },
    "MRFIRInfo": {
        "variant": (1, "int32", ""),
        "samplerate": (2, "float", ""),
        "layers": (3, "MRFIRLayerInfo", "repeated"),
    },
    "FIR": {"info": (1, "FIRInfo", ""), "fir_taps": (2, "float", "repeated")},
    "Biquad": {
        "a1": (1, "float", ""),
        "a2": (2, "float", ""),
        "b0": (3, "float", ""),
        "b1": (4, "float", ""),
        "b2": (5, "float", ""),
    },
    "ParametricBiquad": {
        "type": (1, "enum", ""),
        "gain": (2, "float", ""),
        "Q": (3, "float", ""),
        "center_frequency": (4, "float", ""),
    },
    "IIR": {"info": (1, "IIRInfo", ""), "biquads": (2, "Biquad", "repeated")},
    "ParametricIIR": {
        "info": (1, "IIRInfo", ""),
        "biquads": (2, "ParametricBiquad", "repeated"),
    },
    "FIIR": {"info": (1, "FIIRInfo", ""), "fir": (2, "FIR", ""), "iir": (3, "IIR", "")},
    "DRFIR": {
        "info": (1, "DRFIRInfo", ""),
        "fir_hi": (2, "FIR", ""),
        "fir_lo": (3, "FIR", ""),
        "delay_hi": (4, "int32", ""),
        "max_gain": (5, "float", ""),
    },
    "DelayGain": {
        "info": (1, "DelayGainInfo", ""),
        "delay": (2, "int32", ""),
        "gain": (3, "float", ""),
    },
    "MRFIRLayer": {"fir": (1, "FIR", ""), "bulk_delay": (2, "int32", "")},
    "MRFIR": {"info": (1, "MRFIRInfo", ""), "layers": (2, "MRFIRLayer", "repeated")},
    "FilterDesignInfo": {
        "section_index": (1, "int32", ""),
        "filter_design_delay_ms": (2, "float", ""),
    },
    "ChannelNames": {
        "inputs": (1, "string", "repeated"),
        "outputs": (2, "string", "repeated"),
    },
    "ResampleStage": {
        "factor": (1, "int32", ""),
        "iir": (2, "IIR", ""),
        "fir": (3, "FIR", ""),
    },
    "ResampleCoeffsCommon": {
        "variant": (1, "int32", ""),
        "type": (2, "enum", ""),
        "stages": (3, "ResampleStage", "repeated"),
    },
    "FilterCell": {
        "input_idx": (1, "int32", ""),
        "output_idx": (2, "int32", ""),
        "iir": (3, "IIR", ""),
        "parametric_iir": (4, "ParametricIIR", ""),
        "fiir": (5, "FIIR", ""),
        "drfir": (6, "DRFIR", ""),
        "fir": (7, "FIR", ""),
        "delay_gain": (8, "DelayGain", ""),
        "mrfir": (9, "MRFIR", ""),
        "cell_delay": (20, "int32", ""),
    },
    "FilterCommon": {
        "drfir": (1, "DRFIRCOMMON", ""),
        "resample": (2, "ResampleCoeffsCommon", ""),
    },
    "FilterMatrix": {"cells": (1, "FilterCell", "repeated")},
    "FilterSection": {
        "disposition": (1, "enum", ""),
        "filter_type": (2, "enum", ""),
        "rates": (3, "FilterMatrix", "map:int32"),
        "num_inputs": (4, "int32", ""),
        "num_outputs": (5, "int32", ""),
        "channel_names": (6, "ChannelNames", ""),
    },
    "GainDelay": {
        "input_idx": (1, "int32", ""),
        "gain_db": (2, "float", ""),
        "delay_ms": (3, "float", ""),
    },
    "FilterCrossover": {"input_idx": (1, "int32", ""), "frequency": (2, "float", "")},
    "FilterSlot": {
        "info": (1, "SlotInfo", ""),
        "sections": (2, "FilterSection", "map:int32"),
        "gain_delay": (3, "GainDelay", "repeated"),
        "common": (4, "FilterCommon", "repeated"),
        "crossover_info": (5, "FilterCrossover", "repeated"),
        "design_info": (6, "FilterDesignInfo", "map:int32"),
    },
}
CELL_FILTERS = ("iir", "parametric_iir", "fiir", "drfir", "fir", "delay_gain", "mrfir")


@dataclass
class Signature:
    key_id: int
    signature: bytes
    signed: bytes  # key id + payload, the signed bytes

    def verify(self) -> bool:
        """True if Dirac's embedded public key accepts the signature."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        if self.key_id not in PUBLIC_KEYS:
            return False
        key = serialization.load_pem_public_key(PUBLIC_KEYS[self.key_id].encode())
        try:
            key.verify(
                self.signature,
                self.signed,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO
                ),
                hashes.SHA256(),
            )
        except InvalidSignature:
            return False
        return True


@dataclass
class FilterSlotFile:
    version: int
    slot: dict[str, Any] = field(default_factory=dict)  # FilterSlot message
    signature: Signature | None = None


def read(data: bytes) -> FilterSlotFile:
    if len(data) < 12 or data[:4] != MAGIC:
        raise ValueError("filter file incorrect format: no CARD magic")
    if data[4:8] != PRODUCT:
        raise ValueError(f"filter file incorrect product: {data[4:8]!r}")
    version = struct.unpack_from("<I", data, 8)[0]
    if version not in (1, 2):
        raise ValueError(f"filter file incorrect version: {version}")
    signed, pos = 0, 12
    if version == 2:
        if len(data) < 16:
            raise ValueError("truncated filter file")
        signed, pos = struct.unpack_from("<I", data, 12)[0], 16
    payload, signature = data[pos:], None
    if signed:
        head = 7 + SIGNATURE_SIZE + 4
        if len(payload) < head or payload[:4] != SIGNED_MAGIC or payload[4] != 1:
            raise ValueError("filter signature error: no signature block")
        if struct.unpack_from(">H", payload, 5)[0] != SIGNATURE_SIZE:
            raise ValueError("filter signature error: signature size")
        key_id = struct.unpack_from(">I", payload, 7 + SIGNATURE_SIZE)[0]
        signature = Signature(
            key_id, payload[7 : 7 + SIGNATURE_SIZE], payload[7 + SIGNATURE_SIZE :]
        )
        payload = payload[head:]
    try:
        slot = protobuf.decode(SCHEMA, "FilterSlot", payload)
    except (ValueError, UnicodeDecodeError, KeyError, struct.error) as exc:
        raise ValueError(f"Failed to parse filter data: {exc}") from exc
    return FilterSlotFile(version, slot, signature)


def load(path) -> FilterSlotFile:
    return read(Path(path).read_bytes())


def sniff(data: bytes) -> bool:
    return data[:8] == MAGIC + PRODUCT


def write(slot: dict[str, Any]) -> bytes:
    """An unsigned slot file, as the processor writes one (version 2, flag 0)."""
    return (
        MAGIC
        + PRODUCT
        + struct.pack("<II", 2, 0)
        + protobuf.encode(SCHEMA, "FilterSlot", slot)
    )


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------
def _enum(names: tuple[str, ...], value: int) -> str:
    return names[value] if 0 <= value < len(names) else str(value)


def _cell_row(cell: dict) -> tuple:
    kind = next((k for k in CELL_FILTERS if k in cell), "")
    body = cell.get(kind, {})
    taps = lambda f: len((f or {}).get("fir_taps", []))
    if kind == "drfir":
        detail = (
            taps(body.get("fir_hi")),
            taps(body.get("fir_lo")),
            body.get("delay_hi", 0),
        )
    elif kind == "fiir":
        detail = (
            taps(body.get("fir")),
            len(body.get("iir", {}).get("biquads", [])),
            "",
        )
    elif kind in ("iir", "parametric_iir"):
        detail = ("", len(body.get("biquads", [])), "")
    elif kind == "fir":
        detail = (taps(body), "", "")
    elif kind == "delay_gain":
        detail = ("", "", body.get("delay", 0))
    elif kind == "mrfir":
        detail = (
            sum(taps(layer.get("fir")) for layer in body.get("layers", [])),
            "",
            "",
        )
    else:
        detail = ("", "", "")
    return (cell.get("input_idx", 0), cell.get("output_idx", 0), kind, *detail)


class FilterSlotInspector(Inspector):
    def inspect(self, path: Path) -> list[Section]:
        path = Path(path)
        data = path.read_bytes()
        f = read(data)
        sig = f.signature
        signed = (
            f"key {sig.key_id}, {'valid' if sig.verify() else 'INVALID'}"
            if sig
            else "no"
        )
        info = f.slot.get("info", {})
        meta = info.get("slot_metadata", {})
        sections = [
            file_section(path, data, ("version", f.version), ("signed", signed)),
            Section(
                "slot",
                [
                    ("index", info.get("slot_index", 0)),
                    ("name", info.get("name", "")),
                    ("description", info.get("description", "")),
                    ("filter type", info.get("filter_type_name", "")),
                    ("inputs", info.get("num_inputs", 0)),
                    ("outputs", info.get("num_outputs", 0)),
                    ("creation time", info.get("creation_time", 0)),
                    ("manufacturer", meta.get("endpoint_manufacturer", "")),
                    ("model", meta.get("endpoint_model", "")),
                    ("trial filter", meta.get("is_trial_filter", False)),
                    (
                        "required capabilities",
                        ", ".join(map(str, meta.get("required_capabilities", []))),
                    ),
                ],
            ),
        ]
        for key, s in sorted(f.slot.get("sections", {}).items()):
            names = s.get("channel_names", {})
            for rate, matrix in sorted(s.get("rates", {}).items()):
                sections.append(
                    Section(
                        f"section {key} {rate} Hz",
                        [
                            (
                                "disposition",
                                _enum(DISPOSITIONS, s.get("disposition", 0)),
                            ),
                            (
                                "filter type",
                                _enum(FILTER_TYPES, s.get("filter_type", 0)),
                            ),
                            (
                                "inputs",
                                ", ".join(names.get("inputs", []))
                                or s.get("num_inputs", 0),
                            ),
                            (
                                "outputs",
                                ", ".join(names.get("outputs", []))
                                or s.get("num_outputs", 0),
                            ),
                            ("cells", len(matrix.get("cells", []))),
                        ],
                        Table(
                            [
                                "input",
                                "output",
                                "type",
                                "FIR taps",
                                "low-rate taps / sections",
                                "delay",
                            ],
                            [_cell_row(c) for c in matrix.get("cells", [])],
                        ),
                    )
                )
        if f.slot.get("gain_delay"):
            sections.append(
                Section(
                    "gain and delay per output",
                    [],
                    Table(
                        ["output", "gain dB", "delay ms"],
                        [
                            (i, g.get("gain_db", 0.0), g.get("delay_ms", 0.0))
                            for i, g in enumerate(f.slot["gain_delay"])
                        ],
                    ),
                )
            )
        return sections


FORMAT = Format(
    "dirac-filter",
    (".bin",),
    "Dirac Live Processor filter slot",
    FilterSlotInspector,
    sniff,
)
