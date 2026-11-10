#!/usr/bin/env bash
# Creates .venv, installs system + Python dependencies, then verifies the install.
# Safe to re-run. Does not download wallets, wordlists or models.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== system packages (Berkeley DB, Tk, Xvfb) =="
sudo apt-get update -qq
sudo apt-get install -qq -y libdb-dev libdb++-dev build-essential python3-dev python3-venv python3-tk xvfb

echo "== virtualenv =="
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade -q pip
pip install -q -e '.[ai,test]'

echo "== checks =="
python -m wallet_recover --selftest
xvfb-run -a python -m pytest -q

echo
echo "Готово. Дальше:"
echo "  CLI:  python -m wallet_recover --wallet /путь/wallet-backup.dat --print-mkey"
echo "  GUI:  python -m wallet_recover.gui   (или wallet-recover-gui)"
