#!/bin/sh
# Install Strata Deck on the Strata host (run as the same user that runs Strata).
# Creates a venv, installs deps, and a systemd USER service (no sudo needed).
set -e
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/pip install -q -r backend/requirements.txt

mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/strata-deck.service <<'EOF'
[Unit]
Description=Strata Deck - web control panel for the Strata engine
After=network.target

[Service]
WorkingDirectory=%h/strata-deck
ExecStart=%h/strata-deck/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8090
Environment=STRATA_DIR=%h/Strata
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now strata-deck
# keep the user service alive after logout:
loginctl enable-linger "$(whoami)" 2>/dev/null || true

echo
echo "Strata Deck -> http://$(hostname):8090"
