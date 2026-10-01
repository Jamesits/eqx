# eqx - Equalizer Converter

![Works - On My Machine](https://img.shields.io/badge/Works-On_My_Machine-2ea44f)
![100% Written by AI](https://img.shields.io/badge/Written_by_AI-100%25-blue)

Inspect and convert equalizer and audio correction files.

## WARNINGS

**!!! Use at your own risk !!!**

Loading audio correction / calibration curves might fry your soundcards, speakers / headphones and ears.
DO check the output levels on all frequencies before playing any audio.

## Supported File Formats

| Software | Format | Extension | `--format` | Read | Write | Notes |
|:---|:---|:---|:---|:-:|:-:|:---|
| AutoEq | Parametric EQ | `.csv` | `autoeq` | ✓ | ✓ | |
| Generic | FIR filter | `.wav` | `fir` | ✓ | ✓ | Equalizer APO, CamillaDSP, Roon, ... |
| IK Multimedia ARC X 2.x | Session | `.arcXs` | `arcx` | ✓ | ✓ | |
| IK Multimedia ARC X 2.x | Analysis | `.arcXa` | `arcx` | ✓ | ✓ | |
| IK Multimedia ARC 4 | Analysis | `.arc4a` | `arc4` | ✓ | | |
| REW | Measurement | `.mdat` | `mdat` | ✓ | ✓ | |
| REW | Microphone calibration | `.cal`, `.txt` | `rewcal` | ✓ | ✓ | |
| RME TotalMix FX | Room EQ preset | `.tmreq` | `tmreq` | ✓ | ✓ | |
| Rogue Amoeba SoundSource | Headphone EQ custom profile | `.txt` | `soundsource` | ✓ | ✓ | |
| SoundID Reference 5.x | Headphone profile | `.swhp` | `peqb` | ✓ | ✓ | |
| SoundID Reference 5.x | Project | `.swproj` | `swproj` | ✓ | ✓ | |
| SoundID Reference 5.x | Microphone package | `.swmicpkg` | `swmicpkg` | ✓ | ✓ | |
| SoundID Reference 5.x | Target preset | `.json` | `targetpreset` | ✓ | | |
| SoundID Reference 5.x | Export: ADAM Audio A Series | `.adam` | `soundid-export-biquad-xml` | ✓ | | |
| SoundID Reference 5.x | Export: Dolby Atmos Renderer | `.txt` | `soundid-export-txt` | ✓ | | |
| SoundID Reference 5.x | Export: Fluid Audio | `.bin` | `soundid-export-biquad-json` | ✓ | | |
| SoundID Reference 5.x | Export: Grace Design m908 | `.bin` | `soundid-export-peq-json` | ✓ | | |
| SoundID Reference 5.x | Export: Lynx Aurora | `.bin` | `soundid-export-peq-json` | ✓ | | |
| SoundID Reference 5.x | Export: MERGING+ANUBIS | `.bin` | `soundid-export-biquad-json` | ✓ | | |
| SoundID Reference 5.x | Export: SPQ DSP (AVID MTRX, DAD) | `.txt` | `soundid-export-txt` | ✓ | | |
| SoundID Reference 5.x | Export: Wayne Jones AUDIO | `.bin` | `soundid-export-lvnd` | ✓ | | |
| Sonarworks Reference 3.x, 4.x | Headphone profile | `.swhp` | `peqb` | ✓ | | |
| Sonarworks Reference 3.x, 4.x | Project | `.swproj` | `swproj` | ✓ | ✓ | |
| Sonarworks Reference 3.x, 4.x | Export (including the PEQb 2.x and `PEQB` versions) | `.eqb` | `peqb` | ✓ | | |

Read: `inspect` and `convert` input. Write: `convert` output. `convert` takes the same values in `--from` / `--to`; `uv run eqx convert --help` lists the conversions.

## Usage

Options are per-format; use `--help` to list them. Ask your AI friends for help if you don't know how to use it.

Inspect a file:
```shell
uv run eqx inspect <file> [--format <format>] [--full] [...options]
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

### IK Multimedia ARC X

- Only the measured speaker responses are converted; ARC X computes its correction when it loads the file
- Response levels are relative (dB re full scale), not SPL

### IK Multimedia ARC 4

- Only the measured left and right responses are converted; the ARC 4 plug-in computes its correction when it loads the file
- Response levels are relative (dB re the 40 Hz-10 kHz mean), not SPL; the response is already compensated for the microphone, so use a flat `--mic-profile` for `.swproj`
- Analyses older than version 4.0.0 are not supported
