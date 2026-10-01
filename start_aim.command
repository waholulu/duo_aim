#!/bin/zsh
cd -- "${0:A:h}"
exec .venv/bin/python aim/run.py "$@"
