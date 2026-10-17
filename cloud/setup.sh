#!/bin/bash
# The Ledger on a small Linux box (2026-10-16): the Discord trade manager + Version 1 tick every minute,
# the scan on the Central-time slots. Run once as root on a fresh Ubuntu 24.04 server:
#   bash setup.sh
# Before running, put the secrets at /root/.stockcharter.env (copied from the Mac; never in the repo).
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -q && apt-get install -yq python3 git
timedatectl set-timezone America/Chicago
[ -d /root/ledger ] || git clone -q git@github.com:arringtonc/stockcharter-scan.git /root/ledger
cp /root/ledger/cloud/run-scan.sh /root/run-scan.sh && chmod +x /root/run-scan.sh
chmod 600 /root/.stockcharter.env
for unit in ledger-tm ledger-scan; do
  cp /root/ledger/cloud/$unit.service /root/ledger/cloud/$unit.timer /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now ledger-tm.timer ledger-scan.timer
systemctl list-timers --no-pager | grep ledger
echo "ok -- the bot runs every minute; the scan on the CT slots"
