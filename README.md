# eqx - Equalizer Converter

![Works - On My Machine](https://img.shields.io/badge/Works-On_My_Machine-2ea44f)
![100% Written by AI](https://img.shields.io/badge/Written_by_AI-100%25-blue)

Inspect and convert equalizer and audio correction files.

## WARNINGS

**!!! Use at your OWN RISK !!!**

Loading audio correction / calibration curves might fry your soundcards, speakers / headphones and ears.
DO check the output levels on all frequencies before playing any audio.

## Supported File Formats

| Software | Format | Extension | `--format` | Read | Write | Notes |
|:---|:---|:---|:---|:-:|:-:|:---|
| [AutoEq](https://autoeq.app/) | Parametric EQ | `.csv` | `autoeq` | ✓ | ✓ | |
| [Dirac Live 3.x](https://www.dirac.com/live/) | Target curve | `.targetcurve` | `targetcurve` | ✓ | ✓ | Untested: no license |
| [Dirac Live Processor](https://www.dirac.com/live/) | Filter slot | `.bin` | `dirac-filter` | ✓ | ✓ | Untested: no license. Write: unsigned |
| Generic | FIR filter | `.wav` | `fir` | ✓ | ✓ | [Equalizer APO](https://sourceforge.net/projects/equalizerapo/), [CamillaDSP](https://github.com/HEnquist/camilladsp), [Roon](https://help.roonlabs.com/portal/en/kb/articles/dsp-engine-convolution#Introduction), ... |
| [IK Multimedia ARC X 2.x](https://www.ikmultimedia.com/products/arcx/) | Session | `.arcXs` | `arcx` | ✓ | ✓ | Untested: no hardware |
| | Analysis | `.arcXa` | `arcx` | ✓ | ✓ | Untested: no hardware |
| IK Multimedia ARC 4 | Analysis | `.arc4a` | `arc4` | ✓ | | Untested: no hardware |
| [REW](https://www.roomeqwizard.com/) | Measurement | `.mdat` | `mdat` | ✓ | ✓ | |
| | Microphone calibration | `.cal`, `.txt` | `rewcal` | ✓ | ✓ | |
| [RME TotalMix FX](https://rme-audio.de/totalmix-fx.html) | [Room EQ preset](https://rme-audio.de/totalmix-fx-room-eq.html) | `.tmreq` | `tmreq` | ✓ | ✓ | Untested: no hardware |
| [Rogue Amoeba SoundSource](https://rogueamoeba.com/soundsource/) | [Headphone EQ custom profile](https://rogueamoeba.com/support/knowledgebase/?showArticle=SoundSource-Custom-HPEQ) | `.txt` | `soundsource` | ✓ | ✓ | |
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
| Sonarworks Reference 3.x, 4.x | Headphone profile | `.swhp` | `peqb` | ✓ | | Untested: no license |
| | Project | `.swproj` | `swproj` | ✓ | ✓ | Untested: no license |
| | Export (including the PEQb 2.x and `PEQB` versions) | `.eqb` | `peqb` | ✓ | | Untested: no license |

Capabilities:
- Read: `inspect` and `convert` input
- Write: `convert` output

## Usage

Options are per-format; use `--help` to list them. Ask your AI friends for help if you don't know how to use it.

Inspect a file:
```shell
uv run eqx inspect <file> [--format <format>] [--full] [...options]
```

List available conversion paths:
```shell
uv run eqx convert --help
```

Convert files (`-i` is repeatable, e.g. left then right):
```shell
uv run eqx convert -i <in-file> [-i <in-file>] -o <out-file> [--from <format>] [--to <format>] [...options]
```

## Development

Testing:
```shell
uv run python -m pytest
```

## Caveats

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
- The filter SoundID Reference plays for the profile (flat target), minimum or linear phase, with Limit Controls, Listening Spot and Safe Headroom
- Projects converted from unsmoothed REW measurements may differ from SoundID above 15 kHz

### Dirac Live

- Projects (`.liveproject`) are not supported: their measurements and filters are encrypted with the Dirac account session key
- `.targetcurve` to `.csv` follows the breakpoints linearly in log frequency; the correction range is not part of the curve

### Dirac Live Processor

- Filter slots are `filter.bin` in `%APPDATA%\Dirac\Dirac_Live_Processor\filters\<slot>\` (macOS: `~/Library/Application Support/Dirac/Dirac_Live_Processor/filters/<slot>/`)
- Filters calculated by Dirac's servers are signed; `inspect` checks the signature. Written slots are unsigned, as the ones the processor writes itself
- Written slots hold a dual-rate filter at 32, 44.1 and 48 kHz, and add 446 samples of latency
- Reading plays each output from its own input; bass management cross terms are left out

### IK Multimedia ARC X

- Only the measured speaker responses are converted; ARC X computes its correction when it loads the file
- Response levels are relative (dB re full scale), not SPL

### IK Multimedia ARC 4

- Only the measured left and right responses are converted; the ARC 4 plug-in computes its correction when it loads the file
- Response levels are relative (dB re the 40 Hz-10 kHz mean), not SPL; the response is already compensated for the microphone, so use a flat `--mic-profile` for `.swproj`
- Analyses older than version 4.0.0 are not supported
