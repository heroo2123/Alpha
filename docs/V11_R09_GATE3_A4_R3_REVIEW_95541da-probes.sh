#!/bin/sh
set -eu
cd /tmp/alpha-v11-gate3-a4-r3-review-95541da
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/tmp/alpha-v11-gate3-a4-r3-review-95541da
exec timeout 120 /home/alphaadmin/AlphaV11_Dev/venv/bin/python /tmp/alpha-v11-gate3-a4-r3-review-95541da-runner.py /tmp/alpha-v11-gate3-a4-r3-review-95541da-probes.py
