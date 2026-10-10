#!/usr/bin/env bash
# Instala el agente en la EliteBook (Ubuntu, usuario actual, sin sudo): timer de systemd --user cada 15 min.
# Uso (en la EliteBook): bash ~/Documents/rnp-sismo/agentes/instalar_elitebook.sh
set -euo pipefail
RAIZ="$HOME/Documents/rnp-sismo"
mkdir -p "$HOME/.config/systemd/user" "$RAIZ/logs"
cat > "$HOME/.config/systemd/user/rnp-sismo.service" <<EOF
[Unit]
Description=RNP sismo · agente de ingesta USGS
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$RAIZ
ExecStart=/usr/bin/python3 $RAIZ/agentes/actualizar.py
TimeoutStartSec=180
EOF
cat > "$HOME/.config/systemd/user/rnp-sismo.timer" <<EOF
[Unit]
Description=RNP sismo · cada 15 minutos

[Timer]
OnCalendar=*:0/15
RandomizedDelaySec=30
Persistent=true
Unit=rnp-sismo.service

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now rnp-sismo.timer
loginctl enable-linger "$USER" 2>/dev/null || true
systemctl --user start rnp-sismo.service
systemctl --user list-timers rnp-sismo.timer --no-pager
tail -n 3 "$RAIZ/logs/actualizar.log"
