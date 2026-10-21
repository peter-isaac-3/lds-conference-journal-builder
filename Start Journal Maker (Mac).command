#!/bin/bash
cd "$(dirname "$0")" || exit 1

# Find Python 3 (any recent version is fine).
PY=""
for c in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  osascript -e 'display dialog "Journal Maker needs a free helper program called Python.\n\nA web page will open: download and install Python, then double-click this file again." buttons {"OK"} default button "OK"' >/dev/null 2>&1
  open "https://www.python.org/downloads/macos/"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo
  echo " First-time setup - this takes a minute or two. Please wait..."
  echo
  "$PY" -m venv .venv && ./.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
  if [ $? -ne 0 ]; then
    rm -rf .venv
    echo
    echo " Sorry - setup didn't finish. Check your internet connection and try again."
    read -n 1 -s -r -p " Press any key to close."
    exit 1
  fi
fi

./.venv/bin/python journal_app.py
echo
echo " Journal Maker has stopped. You can close this window."
