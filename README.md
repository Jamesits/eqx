# eqx - Equalizer Converter

![Works - On My Machine](https://img.shields.io/badge/Works-On_My_Machine-2ea44f)
![100% Written by AI](https://img.shields.io/badge/Written_by_AI-100%25-blue)

Inspect and convert euqalizer and audio correction files.

Supported file formats:

- AutoEq
    - `.csv`
- IK Multimedia ARC X 2.x
    - Session `.arcXs`
    - Analysis `.arcXa`
- RME TotalMix FX
    - Room EQ preset `.tmreq`
- REW
    - `.mdat`
    - `.cal`/`.txt`
- SoundID Reference 5.x
    - `.swhp`
    - `.swproj`
    - `.swmicpkg`
    - Target preset `.json`
    - Device exports
        - ADAM Audio A Series `.adam`
        - Dolby Atmos Renderer `.txt`
        - Fluid Audio `.bin`
        - Grace Design m908 `.bin`
        - Lynx Aurora `.bin`
        - MERGING+ANUBIS `.bin`
        - SPQ DSP (AVID MTRX, DAD) `.txt`
        - Wayne Jones AUDIO `.bin`

## WARNINGS

**!!! Use at your own risk !!!**

Loading audio correction / calibration curves might fry your soundcards, speakers / headphones and ears.
DO check the output levels on all frequencies before playing any audio.

## Usage

Options are per-format; use `--help` to list them.

Inspect a file:
```shell
uv run eqx inspect <file> [--format <format>] [--full]
```

Convert a file:
```shell
uv run eqx convert <in-file> -o <out-file> [--from <format>] [--to <format>] [...options]
```

## Development

Testing:
```shell
uv run pytest
```

## Caveats

### SoundID Reference 5.x

`.swproj`
- Calibrations of speakers with distance <50cm are supported
- Calibrations of complicated sound environment are supported

`.swhp`
- An active license is required, and the file must be read on the computer with the license

`.swmicpkg`
- 0/30/90 degree curves supported

Device exports
- MERGING exports are encrypted with the device serial number; without `--serial-number` every number is tried (a few seconds)

### IK Multimedia ARC X

- Only the measured speaker responses are converted; ARC X computes its correction when it loads the file
- Response levels are relative (dB re full scale), not SPL
