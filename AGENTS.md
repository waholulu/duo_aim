# Repository Guidelines

## Project Structure & Module Organization

This repository contains a macOS capture-card and Core ML pose-detection experiment. `benchmark.swift` measures latency; `capture_probe.swift` and `inspect_capture.swift` inspect video input. The `aim/` directory contains the streaming detector (`detector.swift`), feedback policy (`policy.py`), launcher (`run.py`), and Windows UDP receiver (`windows_receiver.py`). Root-level Python scripts inspect models and summarize or annotate benchmark output. Unit tests live in `tests/test_aim.py`. Generated binaries, models, and run artifacts belong in ignored paths such as `results/`, `*.mlpackage/`, and `*.pt`.

## Build, Test, and Development Commands

Use Apple Silicon macOS with Swift command-line tools and Python 3.12. Create a local environment with `python3.12 -m venv .venv` and `.venv/bin/python -m pip install -r requirements-lock.txt`. Follow `README.md` to export the Core ML model before running capture code.

- `swiftc -O aim/VisionCore.swift benchmark.swift -o benchmark` builds the latency benchmark.
- `./benchmark yolo11n-pose-320-fp16.mlpackage 960 1000 my-run all` records a benchmark in `results/`.
- `./start_aim.command --seconds 10` runs a short detection-only session; the launcher builds `aim/detector` when needed.
- `.venv/bin/python -m unittest discover -s tests` runs the unit tests without capture hardware.

## Coding Style & Naming Conventions

Use four-space indentation in new Python and Swift code. Follow existing `snake_case` for Python functions, variables, and script names; use `PascalCase` for classes. Keep capture and model inference in Swift, and keep target selection and movement decisions in `aim/policy.py` so they can be tested without hardware. No formatter or linter is configured; keep changes focused and readable.

## Testing Guidelines

Tests use Python's standard `unittest` framework. Add `test_...` methods to `tests/test_aim.py` for policy or packet-validation changes, including rejection and boundary cases. Run the unit tests before opening a pull request. For capture, model, or live-input changes, also report the hardware and command used for manual verification; there is no configured coverage threshold.

## Commit & Pull Request Guidelines

The Git history contains only one commit, so it does not establish a message convention. Use a short imperative subject describing the change. Pull requests should explain the behavior changed, list test or benchmark results, and note required hardware or model files. Include annotated frames or result excerpts when changing detection quality or timing claims; do not commit generated captures, weights, logs, or secrets.

## Security & Configuration

Keep `AIM_TOKEN` out of commits and logs. The launcher defaults to detection-only mode; use `--live` only when intentionally testing mouse output. For remote control, configure the same token on both machines and follow `aim/README.md`.
