# eqx - Equalizer Converter

![Works - On My Machine](https://img.shields.io/badge/Works-On_My_Machine-2ea44f)
![100% Written by AI](https://img.shields.io/badge/Written_by_AI-100%25-blue)
![Project Status - Feature Complete](https://img.shields.io/badge/Project_Status-Feature_Complete-2ea44f)

Inspect and convert equalizer, mic and room correction files. Calibrate in any software, apply correction to every software.

## WARNINGS

**!!! Use at your OWN RISK !!!**

Loading audio correction / calibration curves might fry your soundcards, speakers / headphones and ears.
DO check the output levels on all frequencies before playing any audio.

## Supported File Formats

| Software | Format | Extension | `--format` | Read | Write | Notes |
|:---|:---|:---|:---|:-:|:-:|:---|
| [Audyssey MultEQ-X](https://audyssey.com/) | Project | `.mqx` | `mqx` | ✓ | ✓ | Untested: no hardware |
| [AutoEq](https://autoeq.app/) | Parametric EQ | `.csv` | `autoeq` | ✓ | ✓ | |
| [Dayton Audio](https://www.daytonaudio.com/) UMM-6, iMM-6, OmniMic, EMM-6 | Microphone calibration | `.txt`, `.omm` | `dayton` | ✓ | | |
| [Dirac Live 3.x](https://www.dirac.com/live/) | Target curve | `.targetcurve` | `targetcurve` | ✓ | ✓ | Untested: no license |
| [Dirac Live Processor](https://www.dirac.com/live/) | Filter slot | `.bin` | `dirac-filter` | ✓ | ✓ | Untested: no license |
| [FuzzMeasure 4](https://www.rodetest.com/) | Document | `.fume4` | `fuzzmeasure` | ✓ | ✓ | Tested demo features only |
| FuzzMeasure 3 | Document | `.fume3` | `fuzzmeasure` | ✓ | | Tested demo features only |
| FuzzMeasure 2 | Document | `.fume` | `fuzzmeasure` | ✓ | | Untested: software unavailable |
| Generic | FIR filter | `.wav` | `fir` | ✓ | ✓ | [Equalizer APO](https://sourceforge.net/projects/equalizerapo/), [CamillaDSP](https://github.com/HEnquist/camilladsp), [Audio Hijack](https://rogueamoeba.com/support/manuals/audiohijack/?page=advancedblocks#fir-filter), [Roon](https://help.roonlabs.com/portal/en/kb/articles/dsp-engine-convolution#Introduction), ... |
| [IK Multimedia ARC X 2.x](https://www.ikmultimedia.com/products/arcx/) | Session | `.arcXs` | `arcx` | ✓ | ✓ | Untested: no hardware |
| | Analysis | `.arcXa` | `arcx` | ✓ | ✓ | Untested: no hardware |
| IK Multimedia ARC 4 | Analysis | `.arc4a` | `arc4` | ✓ | | Untested: no hardware |
| [miniDSP UMIK Series](https://www.minidsp.com/products/acoustic-measurement) | Microphone calibration | `.txt` | `umik` | ✓ | | |
| [Rational Acoustics Smaart 9](https://www.rationalacoustics.com/pages/smaart) | Transfer function trace | `.trf` | `smaart-trf` | ✓ | ✓ | |
| | Spectrum trace | `.srf` | `smaart-srf` | ✓ | ✓ | |
| | ASCII export, Import ASCII | `.txt` | `smaart-ascii` | ✓ | ✓ | |
| | Target curve | `.crv` | `smaart-curve` | ✓ | ✓ | |
| | Microphone correction curve | `.crv` | `smaart-curve` | ✓ | | |
| Rational Acoustics Smaart 7 and older | Reference file | `.ref` | `smaart-ref` | ✓ | | Untested: software unavailable |
| [REW](https://www.roomeqwizard.com/) | Measurement | `.mdat` | `mdat` | ✓ | ✓ | |
| | Microphone calibration | `.cal`, `.txt` | `rewcal` | ✓ | ✓ | |
| [RME TotalMix FX](https://rme-audio.de/totalmix-fx.html) | [Room EQ preset](https://rme-audio.de/totalmix-fx-room-eq.html) | `.tmreq` | `tmreq` | ✓ | ✓ | Untested: no hardware |
| [Rogue Amoeba SoundSource](https://rogueamoeba.com/soundsource/) | [Headphone EQ custom profile](https://rogueamoeba.com/support/knowledgebase/?showArticle=SoundSource-Custom-HPEQ) | `.txt` | `soundsource` | ✓ | ✓ | |
| Sennheiser dearVR MIX | Headphone compensation filters (built-in) | `hpc.dat` | `dearvr-hpc` | ✓ | | |
| Sonarworks Reference 3.x, 4.x | Headphone profile | `.swhp` | `peqb` | ✓ | | Untested: no license |
| | Project | `.swproj` | `swproj` | ✓ | ✓ | Untested: no license |
| | Export (including the PEQb 2.x and `PEQB` versions) | `.eqb` | `peqb` | ✓ | | Untested: no license |
| [SoundID Reference 5.x](https://www.sonarworks.com/soundid-reference) | Headphone profile | `.swhp` | `peqb` | ✓ | ✓ | |
| | Project | `.swproj` | `swproj` | ✓ | ✓ | |
| | Microphone package | `.swmicpkg` | `swmicpkg` | ✓ | ✓ | |
| | Target preset | `.json` | `targetpreset` | ✓ | | |
| | Export: ADAM Audio A Series | `.adam` | `soundid-export-biquad-xml` | ✓ | | Untested: no hardware |
| | Export: Dolby Atmos Renderer | `.txt` | `soundid-export-txt` | ✓ | | Untested: no license |
| | Export: Fluid Audio | `.bin` | `soundid-export-biquad-json` | ✓ | | Untested: no hardware |
| | Export: Grace Design m908 | `.bin` | `soundid-export-peq-json` | ✓ | | Untested: no hardware |
| | Export: Lynx Aurora | `.bin` | `soundid-export-peq-json` | ✓ | | Untested: no hardware |
| | Export: MERGING+ANUBIS | `.bin` | `soundid-export-biquad-json` | ✓ | | Untested: no hardware |
| | Export: SPQ DSP (AVID MTRX, DAD) | `.txt` | `soundid-export-txt` | ✓ | | Untested: no hardware |
| | Export: Wayne Jones AUDIO | `.bin` | `soundid-export-lvnd` | ✓ | | Untested: no hardware |

Capabilities:
- Read: `inspect` and `convert` input
- Write: `convert` output

For use with other applications, try convert with [AutoEq](https://github.com/jaakkopasanen/AutoEq/wiki/Choosing-an-Equalizer-App).

## Usage

Options are per-format; use `--help` to list them. Ask your AI friends for help if you don't know how to use it.

Inspect a file:
```shell
uv run eqx inspect <file> [--format <format>] [--full] [--graph auto|on|off] [...options]
```

List available conversion paths:
```shell
uv run eqx convert --help
```

Convert files (`-i` is repeatable, e.g. left then right):
```shell
uv run eqx convert -i <in-file> [-i <in-file>] -o <out-file> [--from <format>] [--to <format>] [...options]
```

You can use it as a Python library too.

## Development

Testing:
```shell
uv run pytest
```

## Caveats

### Audyssey MultEQ-X

- Only the measured speaker responses are converted; `inspect` lists the target curve components. MultEQ-X computes its filters when it transfers them to the AVR
- Response levels are relative (dB re full scale), not SPL
- Measurements include the microphone; MultEQ-X compensates them for its ACM1H. `--mic-response` takes the microphone's REW calibration file (e.g. the generic ACM1HB one) and subtracts it (`.mqx` to `.csv`) or adds it (`.csv` to `.mqx`); `.swproj` uses `--mic-profile`
- MultEQ-X saves over a project without truncating the file; the leftover text after the project is ignored
- Written projects hold one position of the front left and right speakers; deselect the AVR's other channels in MultEQ-X

### Dayton Audio

- Converts to `.swmicpkg` (`-i <serial>.txt`, `-i <serial>.omm`); the 30 and 90 degree tables are copies of the 0 degree table. A 90 degree file named `<serial>_90deg.txt` gives the 90 degree table
- Usable as `--mic-profile` of `.swproj` conversions, without `--mic-angle`
- The sensitivity (`Sens Factor`, EMM-6 `*1000Hz`) and the phase are shown by `inspect`, not converted

### Dirac Live

- Projects (`.liveproject`) are not supported: their measurements and filters are encrypted with the Dirac account session key
- `.targetcurve` to `.csv` follows the breakpoints linearly in log frequency; the correction range is not part of the curve

### Dirac Live Processor

- Filter slots are `filter.bin` in `%APPDATA%\Dirac\Dirac_Live_Processor\filters\<slot>\` (macOS: `~/Library/Application Support/Dirac/Dirac_Live_Processor/filters/<slot>/`)
- Filters calculated by Dirac's servers are signed; `inspect` checks the signature. Written slots are unsigned, as the ones the processor writes itself
- Written slots hold a dual-rate filter at 32, 44.1 and 48 kHz, and add 446 samples of latency
- Reading plays each output from its own input; bass management cross terms are left out

### FuzzMeasure

- `.fume4` and `.fume3` documents are folders (macOS packages); copy or unzip the whole folder. `eqx` writes `.fume4` as a folder
- The response is FuzzMeasure's frequency response of the measurement's analysis window. The microphone calibration stored with a measurement is subtracted where FuzzMeasure applies it; `--no-mic-calibration` leaves it out. Levels are dB re full scale; `--spl` uses FuzzMeasure's SPL scale
- Written documents hold one measurement per input and one frequency response graph. The mid band sits at 0 dB re full scale; the SPL reference level makes the SPL graph show the curves' own levels

### IK Multimedia ARC X

- Only the measured speaker responses are converted; ARC X computes its correction when it loads the file
- Response levels are relative (dB re full scale), not SPL

### IK Multimedia ARC 4

- Only the measured left and right responses are converted; the ARC 4 plug-in computes its correction when it loads the file
- Response levels are relative (dB re the 40 Hz-10 kHz mean), not SPL; the response is already compensated for the microphone, so use a flat `--mic-profile` for `.swproj`
- Analyses older than version 4.0.0 are not supported

### miniDSP UMIK

- Converts to `.swmicpkg` (`-i <serial>.txt [-i <serial>_90deg.txt]`); the 30 degree table is a copy of the 0 degree table
- Usable as `--mic-profile` of `.swproj` conversions; pass the `_90deg` file for the 90 degree table, without `--mic-angle`
- The sensitivity (`Sens Factor`) is shown by `inspect`, not converted

### Rational Acoustics Smaart

- Smaart keeps its files in `Documents/Smaart Suite/`: traces in `Data/Transfer Function/` and `Data/Spectrum/`, target curves in `TargetCurves/`, microphone correction curves in `MicCorrectionCurves/`
- `.trf` / `.srf` / `.ref` to `.csv`: the trace's points, unresampled; bins below the coherence threshold are left out. Spectrum levels are dB as stored; `--calibrated` adds the trace's calibration offset. `--mtw` converts the MTW data set
- `.csv` to `.trf`: a minimum-phase transfer function with coherence 1, FFT 32k (`--fft`); load it with File > Import > Trace Data File
- `.csv` to `.srf`: a spectrum trace; `--calibration-db` stores a calibration offset so that Plot Calibrated Levels shows the curve's values
- `.csv` to `.txt` (`--to smaart-ascii`): load it with File > Import > Import ASCII
- `.csv` to `.crv`: a transfer function target curve, or a spectrum target curve with `--band`; load it with File > Import > Target Curve. Reading a `.crv` does not apply its `Offset`
- Smaart 7 and older reference files (`.ref`) with fixed points per octave (FPPO) data are not supported
- Convert curves to `.wav` as IR: use `--encoding pcm24`

### Sennheiser dearVR MIX

- The built-in Spatial Headphone Compensation filters are in `hpc.dat`: macOS `/Library/Application Support/DearReality/dearVRMix/`; or extract it from the installer's `dearVRMixIRData.pkg`
- `inspect --full` lists the headphones only; `--headphone <name>` shows one headphone's filters and gain
- Converts one headphone's filter to `.wav` (as stored) or `.csv` (its gain): `--headphone <name> [--phase minimum|linear] [--rate <Hz>]`. The rate must be one of the file's (44.1-192 kHz)
- The plug-in's gain trim (default -6 dB) and shelves are not applied

### SoundID Reference 5.x

`.swproj`
- Calibrations of speakers with distance <50cm are supported via REW
- Calibrations of hidden speakers (thus unable to pass the per-speaker initial calibration in SoundID Reference Measure) are supported via REW
- Calibrations of complicated sound environment (with points unable to pass the grid triangulation check) are supported via REW

`.swhp`
- An active license is required, and the file must be read on the computer with the license

`.swmicpkg`
- 0/30/90 degree curves supported

`.swhp`, `.swproj` to FIR `.wav`
- The filter SoundID Reference plays for the profile (flat target), minimum, linear or mixed phase (SoundID's Zero Latency, Linear Phase, Mixed), with Limit Controls, Listening Spot and Safe Headroom
