#!/bin/zsh
cd -- "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python check_devices.py "$@"
else
    exec python3 check_devices.py "$@"
fi
