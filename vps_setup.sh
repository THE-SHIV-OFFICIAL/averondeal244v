#!/bin/bash
# Ubuntu VPS one-time setup. Run inside /root/averonbot
set -e
apt update && apt install -y python3 python3-venv python3-pip
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo ">> .env bana di, ab 'nano .env' se keys bharo aur script dobara chalao"; exit 0; }
cp averonbot.service /etc/systemd/system/averonbot.service
systemctl daemon-reload
systemctl enable --now averonbot
systemctl status averonbot --no-pager
