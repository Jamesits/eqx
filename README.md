# eqx - Equalizer Converter

![Works - On My Machine](https://img.shields.io/badge/Works-On_My_Machine-2ea44f)
![100% Written by AI](https://img.shields.io/badge/Written_by_AI-100%25-blue)

Inspect and convert equalizer and audio correction files.

## WARNINGS

**!!! Use at your own risk !!!**

Loading audio correction / calibration curves might fry your soundcards, speakers / headphones and ears.
DO check the output levels on all frequencies before playing any audio.

## Supported File Formats

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

## Usage

Options are per-format; use `--help` to list them. Ask your AI friends for help if you don't know how to use it.

Inspect a file:
```shell
uv run eqx inspect <file> [--format <format>] [--full] [...options]
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
- Calibrations of speakers with distance <50cm are supported via REW
- Calibrations of hidden speakers (thus unable to pass the per-speaker initial calibration in SoundID Reference Measure) are supported via REW
- Calibrations of complicated sound environment (with points unable to pass the grid triangulation check) are supported via REW

`.swhp`
- An active license is required, and the file must be read on the computer with the license

`.swmicpkg`
- 0/30/90 degree curves supported

### IK Multimedia ARC X

- Only the measured speaker responses are converted; ARC X computes its correction when it loads the file
- Response levels are relative (dB re full scale), not SPL
