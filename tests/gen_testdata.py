"""Generate the artificial test data in ``testdata/<source>/<format>/``.

    uv run python tests/gen_testdata.py [ROOT]

Every file is built from analytic curves and fixed values: no machine,
device, user or time metadata.  The ``testgen`` modules write the source
files; files that a converter produces from other test files are written by
running that converter (``CONVERSIONS``).

Writers for formats that ``eqx`` only reads live in ``testgen``, not in the
library: encrypted PEQb, PEQb 2.x and ``PEQB``, Custom Target Presets, a REW
``.cal`` with a sensitivity line, the SoundID device exports, Sonarworks
Reference 3 and Sonarworks Reference 4 Measure projects, ARC 4 files,
FuzzMeasure 3 and 2 documents, miniDSP UMIK calibration files, Smaart 7
reference files, Smaart ASCII exports, curve files other than the written
target curves and the dearVR MIX ``hpc.dat``.  A package
(directory) is a dict of its files.
"""

from __future__ import annotations

import sys
from pathlib import Path

from eqx import convert, formats
from eqx.soundid import swproj
from testgen import (audyssey, dirac, ik, minidsp, rationalacoustics, rew, rme, rode,
                     rogueamoeba, sennheiser, soundid, sonarworks_reference)
from testgen.audyssey import MQX_DIR
from testgen.common import ANGLES, COMPUTER_ID, CSV_DIR, FIR_DIR, ROOT, SWPROJ_PASSWORD
from testgen.dirac import DIRAC_FILTER_DIR, TARGETCURVE_DIR
from testgen.ik import ARC4_DIR, ARCX_DIR
from testgen.minidsp import UMIK_DIR
from testgen.rationalacoustics import ASCII_DIR, CURVE_DIR, TRACE_DIR
from testgen.rew import CAL_DIR, MDAT_DIR
from testgen.rme import TMREQ_DIR
from testgen.rode import FUZZMEASURE_DIR
from testgen.rogueamoeba import SOUNDSOURCE_DIR
from testgen.sennheiser import HPC_DIR
from testgen.sonarworks_reference import SONARWORKS_PROJ_DIR
from testgen.soundid import (BIQUAD_JSON_DIR, EXPORT_TXT_DIR, LVND_DIR, MIC_DIR, MICS, PEQB_DIR,
                             PRESET_DIR, PROJ_DIR)

# The writers of the source files, one per directory below the root.
SOURCES = (rew, soundid, sonarworks_reference, rme, rogueamoeba, dirac, ik, audyssey, rode,
           minidsp, rationalacoustics, sennheiser)

# (input or tuple of inputs, output, converter options); paths relative to
# the root.  In dependency order: a later input may be an earlier output.
# Option "to": the output format, where the extension is shared.
CONVERSIONS = [
    *((f"{MIC_DIR}/{serial}.swmicpkg", f"{CAL_DIR}/{serial} {angle}.txt", {"angle": angle})
      for serial in MICS for angle in ANGLES),
    (f"{MDAT_DIR}/Flat.mdat", f"{PROJ_DIR}/Flat.swproj",
     {"mic_profile": f"{MIC_DIR}/FLAT01.swmicpkg"}),
    (f"{MDAT_DIR}/Bandpass.mdat", f"{PROJ_DIR}/Bandpass.swproj",
     {"mic_profile": f"{MIC_DIR}/TILT01.swmicpkg", "mic_angle": "degrees_30"}),
    (f"{MDAT_DIR}/Room.mdat", f"{PROJ_DIR}/Room.swproj",
     {"mic_profile": f"{MIC_DIR}/TILT01.swmicpkg", "max_boost_db": 6.0,
      "low_cutoff_hz": 40.0, "high_cutoff_hz": 16000.0}),
    (f"{MDAT_DIR}/Left only.mdat", f"{PROJ_DIR}/Left only.swproj",
     {"mic_profile": f"{PROJ_DIR}/Flat.swproj", "reference_spl": 80.0}),
    (f"{MDAT_DIR}/Room.mdat", f"{CSV_DIR}/Room Left.csv", {}),
    (f"{MDAT_DIR}/Room.mdat", f"{CSV_DIR}/Room Right.csv", {"channel": "right"}),
    (f"{PROJ_DIR}/Bandpass.swproj", f"{CSV_DIR}/Bandpass Left.csv", {}),
    (f"{PEQB_DIR}/Tilt Tilt Wired Average.swhp", f"{CSV_DIR}/Tilt Tilt Wired Average Left.csv",
     {"computer_id": COMPUTER_ID}),
    (f"{PRESET_DIR}/Bass and treble.json", f"{CSV_DIR}/Bass and treble.csv", {}),
    (f"{CSV_DIR}/Bass and treble.csv", f"{CAL_DIR}/Bass and treble.txt", {"to": "rewcal"}),
    ((f"{UMIK_DIR}/8100042_90deg.txt", f"{UMIK_DIR}/8100042.txt"), f"{MIC_DIR}/8100042.swmicpkg",
     {}),
    ((f"{CSV_DIR}/Room Left.csv", f"{CSV_DIR}/Room Right.csv"), f"{PROJ_DIR}/Room Left.swproj",
     {"mic_profile": f"{MIC_DIR}/TILT01.swmicpkg"}),
    (f"{CSV_DIR}/Tilt Tilt Wired Average Left.csv", f"{PEQB_DIR}/Tilt Tilt Wired Average Left.swhp",
     {"make": "Tilt", "model": "Tilt", "reference_db": 0.0}),
    (f"{BIQUAD_JSON_DIR}/Tilt Fluid.bin", f"{CSV_DIR}/Tilt Fluid Left.csv", {}),
    (f"{EXPORT_TXT_DIR}/Tilt - Flat.txt", f"{CSV_DIR}/Tilt - Flat Right.csv", {"channel": "right"}),
    (f"{LVND_DIR}/Tilt_192000_Left.bin", f"{CSV_DIR}/Tilt_192000_Left.csv", {}),
    (f"{ARCX_DIR}/Arc.arcXs", f"{CSV_DIR}/Arc Left.csv", {}),
    (f"{ARCX_DIR}/Arc Sub.arcXs", f"{CSV_DIR}/Arc Sub Subwoofer.csv", {"speaker": "Subwoofer"}),
    (f"{ARCX_DIR}/Arc.arcXs", f"{PROJ_DIR}/Arc.swproj",
     {"mic_profile": f"{MIC_DIR}/FLAT01.swmicpkg"}),
    (f"{ARCX_DIR}/Arc 5.1.arcXs", f"{PROJ_DIR}/Arc 5.1.swproj",
     {"mic_profile": f"{MIC_DIR}/FLAT01.swmicpkg"}),
    (f"{ARC4_DIR}/Arc4.arc4a", f"{CSV_DIR}/Arc4 Right.csv", {"channel": "right"}),
    (f"{ARC4_DIR}/Arc4.arc4a", f"{PROJ_DIR}/Arc4.swproj",
     {"mic_profile": f"{MIC_DIR}/FLAT01.swmicpkg"}),
    (f"{PEQB_DIR}/Tilt Tilt Wired Average.swhp", f"{FIR_DIR}/Tilt Tilt Wired Average.wav",
     {"computer_id": COMPUTER_ID}),
    (f"{FIR_DIR}/Tilt Tilt Wired Average.wav", f"{CSV_DIR}/Tilt Tilt Wired Average Right.csv",
     {"channel": "right"}),
    ((f"{CSV_DIR}/Room Left.csv", f"{CSV_DIR}/Room Right.csv"), f"{FIR_DIR}/Room Left.wav",
     {"phase": "linear"}),
    (f"{PROJ_DIR}/Room.swproj", f"{FIR_DIR}/Room.wav", {}),
    (f"{MDAT_DIR}/Bandpass.mdat", f"{SONARWORKS_PROJ_DIR}/Bandpass.swproj",
     {"mic_profile": f"{MIC_DIR}/TILT01.swmicpkg", "mic_angle": "degrees_30", "app": "sonarworks-reference",
      "spot_delay_ms": ["Right=0.15"], "spot_gain_db": ["Left=-0.5"]}),
    (f"{SONARWORKS_PROJ_DIR}/Bandpass.swproj", f"{CSV_DIR}/Bandpass Right.csv", {"channel": "right"}),
    (f"{SONARWORKS_PROJ_DIR}/Bandpass.swproj", f"{FIR_DIR}/Bandpass.wav", {}),
    ((f"{CSV_DIR}/Room Left.csv", f"{CSV_DIR}/Room Right.csv"), f"{MDAT_DIR}/Room Left.mdat", {}),
    (f"{CSV_DIR}/Bass and treble.csv", f"{TMREQ_DIR}/Bass and treble.tmreq", {}),
    (f"{CSV_DIR}/Bass and treble.csv", f"{SOUNDSOURCE_DIR}/Bass and treble.txt",
     {"to": "soundsource"}),
    (f"{SOUNDSOURCE_DIR}/Tilt - Flat.txt", f"{CSV_DIR}/Tilt - Flat.csv", {}),
    ((f"{CSV_DIR}/Room Left.csv", f"{CSV_DIR}/Room Right.csv"), f"{ARCX_DIR}/Room Left.arcXs", {}),
    (tuple(f"{CAL_DIR}/TILT01 {angle}.txt" for angle in ANGLES), f"{MIC_DIR}/TILT02.swmicpkg",
     {"serial": "TILT02"}),
    (f"{TARGETCURVE_DIR}/Tilt.targetcurve", f"{CSV_DIR}/Tilt.csv", {}),
    (f"{CSV_DIR}/Bass and treble.csv", f"{TARGETCURVE_DIR}/Bass and treble.targetcurve", {}),
    (f"{CSV_DIR}/Bass and treble.csv", f"{DIRAC_FILTER_DIR}/Bass and treble.bin",
     {"to": "dirac-filter"}),
    (f"{DIRAC_FILTER_DIR}/Bass and treble.bin", f"{CSV_DIR}/Bass and treble Left.csv", {}),
    (f"{DIRAC_FILTER_DIR}/FIIR.bin", f"{FIR_DIR}/FIIR.wav", {}),
    (f"{MQX_DIR}/Mqx 5.1.mqx", f"{CSV_DIR}/Mqx 5.1 FL.csv", {}),
    (f"{MQX_DIR}/Mqx 5.1.mqx", f"{CSV_DIR}/Mqx 5.1 SW1.csv", {"speaker": "SW1", "position": 1}),
    (f"{MQX_DIR}/Mqx 5.1.mqx", f"{PROJ_DIR}/Mqx 5.1.swproj",
     {"mic_profile": f"{MIC_DIR}/FLAT01.swmicpkg"}),
    ((f"{CSV_DIR}/Room Left.csv", f"{CSV_DIR}/Room Right.csv"), f"{MQX_DIR}/Room Left.mqx",
     {"mic_response": f"{CAL_DIR}/TILT01 degrees_0.txt"}),
    (f"{FUZZMEASURE_DIR}/Fm4.fume4", f"{CSV_DIR}/Fm4 Left.csv", {}),
    (f"{FUZZMEASURE_DIR}/Fm4.fume4", f"{CSV_DIR}/Fm4 Right.csv",
     {"measurement": "right", "spl": True}),
    (f"{FUZZMEASURE_DIR}/Fm3.fume3", f"{CSV_DIR}/Fm3 Left.csv", {}),
    ((f"{CSV_DIR}/Bandpass Left.csv", f"{CSV_DIR}/Bandpass Right.csv"),
     f"{FUZZMEASURE_DIR}/Bandpass Left.fume4", {}),
    (f"{TRACE_DIR}/Tf Left.trf", f"{CSV_DIR}/Tf Left.csv", {}),
    (f"{TRACE_DIR}/Rta.srf", f"{CSV_DIR}/Rta.csv", {"calibrated": True}),
    (f"{ASCII_DIR}/Tf export.txt", f"{CSV_DIR}/Tf export Right.csv", {"trace": "right"}),
    (f"{CURVE_DIR}/Haystack.crv", f"{CSV_DIR}/Haystack.csv", {}),
    (f"{TRACE_DIR}/Ref Left.ref", f"{CSV_DIR}/Ref Left.csv", {}),
    (f"{CSV_DIR}/Bandpass Left.csv", f"{TRACE_DIR}/Bandpass Left.trf", {"fft": 4096}),
    (f"{CSV_DIR}/Bandpass Left.csv", f"{TRACE_DIR}/Bandpass Left.srf",
     {"fft": 4096, "calibration_db": 100.0}),
    (f"{CSV_DIR}/Bass and treble.csv", f"{ASCII_DIR}/Bass and treble.txt",
     {"to": "smaart-ascii"}),
    (f"{CSV_DIR}/Bass and treble.csv", f"{CURVE_DIR}/Bass and treble.crv", {}),
    (f"{HPC_DIR}/hpc.dat", f"{CSV_DIR}/Tilt Studio.csv", {"headphone": "tilt studio"}),
    (f"{HPC_DIR}/hpc.dat", f"{FIR_DIR}/Tilt Stereo.wav", {"headphone": "Tilt Stereo"}),
]
PATH_OPTIONS = ("mic_profile", "target_curve", "mic_response")


def run_conversion(root: Path, source: str | tuple[str, ...], target: str,
                   options: dict) -> convert.Result:
    options = {k: root / v if k in PATH_OPTIONS else v for k, v in options.items()}
    to = options.pop("to", None)
    paths = [root / s for s in ((source,) if isinstance(source, str) else source)]
    kind = formats.detect(paths[0])
    pair = convert.find(kind, to or formats.detect(Path(target), (t for s, t in convert.CONVERTERS
                                                                   if s == kind)))
    return pair(**options).convert(paths)


def generate(root: Path = ROOT) -> list[Path]:
    """Write every file below ``root``; return the paths."""
    written = []
    for module in SOURCES:
        for rel, data in module.files().items():
            written += _write(root / rel, data)
    for source, target, options in CONVERSIONS:
        written += _write(root / target, run_conversion(root, source, target, options).data)

    flat = swproj.SwProj.open(root / PROJ_DIR / "Flat.swproj")
    written += _write(root / PEQB_DIR / "Flat.eqb", flat.part("eqb"))
    written += _write(root / PROJ_DIR / "Flat password.swproj",
                      swproj.write(flat.xml, flat.part("eqb"), SWPROJ_PASSWORD.encode()))
    return written


def _write(path: Path, data: bytes | dict[str, bytes]) -> list[Path]:
    """Write a file or a package's files; return the paths."""
    if isinstance(data, dict):
        return [p for name, contents in data.items() for p in _write(path / name, contents)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return [path]


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT
    for path in generate(root):
        print(f"{path.stat().st_size:>9,}  {path.relative_to(root).as_posix()}")
