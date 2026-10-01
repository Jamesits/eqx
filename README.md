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
- FIR filter (Equalizer APO, CamillaDSP, Roon, ...)
    - `.wav`
- IK Multimedia ARC X 2.x
    - Session `.arcXs`
    - Analysis `.arcXa`
- IK Multimedia ARC 4
    - Analysis `.arc4a`
- REW
    - `.mdat`
    - `.cal`/`.txt`
- RME TotalMix FX
    - Room EQ preset `.tmreq`
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
- Sonarworks Reference 3.x, 4.x
    - `.swhp`
    - `.swproj`
    - `.eqb` (including the PEQb 2.x and `PEQB` versions)

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
