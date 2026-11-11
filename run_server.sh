#!/usr/bin/env bash
cd "$(dirname "$0")"
pip install -r requirements.txt --quiet
python3 server/c2.py