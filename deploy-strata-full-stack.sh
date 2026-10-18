#!/usr/bin/env bash

# ==============================================================================
# Script Name: deploy-strata-full-stack.sh
# Description: Installs official Strata Engine + CamTechLinear Strata-Deck UI
# Features: Non-root enforcement, UFW auto-detection, systemd isolation
# ==============================================================================

set -euo pipefail

# --- Repository sources (single source of truth) ---
STRATA_REPO="https://github.com/Niko1221/Strata.git"
DECK_REPO="https://github.com/CamTechLinear/strata-deck.git"

# --- Color Definitions ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0;m'

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}     Strata Engine & Deck Full-Stack Installer      ${NC}"
echo -e "${BLUE}====================================================${NC}"

# --- Step 1: System Verification ---
echo -e "\n${YELLOW}[1/8] Running initial system checks...${NC}"

if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}ERROR: Do not run this script as root or with sudo.${NC}"
    echo "Local execution units require standard user-space allocations."
    exit 1
fi

for cmd in git python3 ss g++ make sudo; do
    if ! command -v "$cmd" &> /dev/null; then
        echo -e "${RED}ERROR: Essential tool '$cmd' is missing.${NC}"
        echo "Install prerequisites: sudo apt update && sudo apt install -y git python3 python3-venv python3-dev iproute2 build-essential"
        exit 1
    fi
done

# Ensure Python 3.11+
PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$(echo -e "3.11\n$PYTHON_VER" | sort -V | head -n1)" != "3.11" ]; then
    echo -e "${RED}ERROR: Python 3.11+ is required. Found: $PYTHON_VER${NC}"
    exit 1
fi

# venv module is a separate package on Debian/Ubuntu — probe it, don't assume
if ! python3 -m venv --help &> /dev/null; then
    echo -e "${RED}ERROR: python3-venv module is missing.${NC}"
    echo "Install it: sudo apt install -y python3-venv"
    exit 1
fi
echo -e "${GREEN}✓ Environment validated successfully.${NC}"

# --- Step 2: Main Engine Deployment ---
echo -e "\n${YELLOW}[2/8] Deploying official Strata Inference Engine...${NC}"
STRATA_DIR="$HOME/Strata"

if [ -d "$STRATA_DIR" ]; then
    echo -e "${GREEN}✓ Found existing Strata engine directory at $STRATA_DIR.${NC}"
    cd "$STRATA_DIR"
    if [ ! -f "setup.py" ] && [ ! -f "setup.sh" ]; then
        echo -e "${RED}ERROR: $STRATA_DIR exists but appears corrupted or empty.${NC}"
        exit 1
    fi
else
    echo "Cloning official Niko1221/Strata engine repository..."
    git clone "$STRATA_REPO" "$STRATA_DIR"
    cd "$STRATA_DIR"
    if [ -f "setup.sh" ]; then
        chmod +x setup.sh
    fi
    echo -e "${GREEN}✓ Core engine files staged.${NC}"
fi

# --- Step 3: Verify Core Engine Status ---
echo -e "\n${YELLOW}[3/8] Verifying core engine configuration...${NC}"
SETTINGS_FILE="$HOME/.config/strata/settings.json"

if [ ! -f "$SETTINGS_FILE" ]; then
    echo -e "${YELLOW}Notice: Core engine hardware configurations have not been finalized.${NC}"
    echo "Launching the interactive Strata script to size your VRAM/RAM allocations."
    echo -e "${BLUE}----------------------------------------------------${NC}"

    # always run setup from the engine home, never from wherever
    # the installer happened to be invoked
    cd "$STRATA_DIR"
    if [ -f "./setup.sh" ]; then
        ./setup.sh
    elif [ -f "setup.py" ]; then
        python3 setup.py
    else
        echo -e "${RED}ERROR: no setup.sh or setup.py in $STRATA_DIR.${NC}"
        exit 1
    fi

    echo -e "${BLUE}----------------------------------------------------${NC}"
    echo -e "${GREEN}✓ Engine initial setup concluded.${NC}"
else
    echo -e "${GREEN}✓ Engine settings validated ($SETTINGS_FILE found).${NC}"
fi

# --- Step 4: Web UI Attachment Download ---
echo -e "\n${YELLOW}[4/8] Setting up Strata Deck application layer...${NC}"
DECK_DIR="$HOME/strata-deck"

if [ -d "$DECK_DIR" ]; then
    echo "Updating existing Strata Deck installation..."
    cd "$DECK_DIR"
    if ! git pull --ff-only; then
        echo -e "${RED}ERROR: local changes in $DECK_DIR diverge from the repo.${NC}"
        echo "Resolve them (git status / git stash) and re-run."
        exit 1
    fi
else
    echo "Cloning CamTechLinear/strata-deck interface repository..."
    git clone "$DECK_REPO" "$DECK_DIR"
    cd "$DECK_DIR"
fi

# --- Step 5: Isolated Dependencies Compilation ---
echo -e "\n${YELLOW}[5/8] Provisioning Web dashboard virtual runtime...${NC}"
rm -rf .venv
python3 -m venv .venv
"$DECK_DIR"/.venv/bin/pip install --upgrade pip --quiet
# one source of truth: the repo's pinned requirements.txt
"$DECK_DIR"/.venv/bin/pip install -r "$DECK_DIR"/backend/requirements.txt --quiet
echo -e "${GREEN}✓ Isolated dependencies verified.${NC}"

# --- Step 6: Process Management Hooks ---
echo -e "\n${YELLOW}[6/8] Enabling Linux user space persistence...${NC}"
loginctl enable-linger "$(whoami)"

# --- Step 7: Systemd Service Unit Assembly ---
echo -e "\n${YELLOW}[7/8] Instantiating systemd dashboard service tracking...${NC}"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

SERVICE_FILE="$SYSTEMD_USER_DIR/strata-deck.service"

cat << EOF > "$SERVICE_FILE"
[Unit]
Description=Strata Deck - Web Control Panel for Strata Inference Engine
After=network.target

[Service]
Type=simple
WorkingDirectory=$DECK_DIR
Environment=STRATA_DIR=$STRATA_DIR
Environment=STRATA_SETTINGS=$SETTINGS_FILE
ExecStart=$DECK_DIR/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8090
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable strata-deck.service
systemctl --user restart strata-deck.service

# --- Step 8: Safe Firewall Adjustment ---
echo -e "\n${YELLOW}[8/8] Adjusting network firewall rules...${NC}"
if sudo ufw status | grep -q "Status: active"; then
    echo "UFW firewall is active. Prompting to open administrative port 8090..."
    sudo ufw allow 8090/tcp comment 'Strata Deck Web UI'
    echo -e "${GREEN}✓ Firewall rule applied successfully.${NC}"
else
    echo -e "${YELLOW}Notice: UFW firewall is inactive/disabled. Skipping port declaration.${NC}"
fi

sleep 1.5

if systemctl --user is-active strata-deck.service &> /dev/null; then
    echo -e "${GREEN}====================================================${NC}"
    echo -e "${GREEN}      FULL STACK DEPLOYED — EVERYTHING RUNNING       ${NC}"
    echo -e "${GREEN}====================================================${NC}"
    echo -e "\nAccess your administrative control panel at:"
    echo -e "${BLUE}👉 http://$(hostname -I | awk '{print $1}'):8090${NC}"
    echo -e "\nComponent Locations:"
    echo -e "  - Strata Engine Home: $STRATA_DIR"
    echo -e "  - Dashboard Web App:  $DECK_DIR"
    echo -e "\nUseful Maintenance Commands:"
    echo "  - Stream Live Dashboard Logs:  journalctl --user -u strata-deck.service -f"
    echo "  - Recheck Process Core Status: systemctl --user status strata-deck.service"
    echo -e "${GREEN}====================================================${NC}"
else
    echo -e "${RED}Deployment anomaly: The systemd service encountered runtime faults.${NC}"
    echo "Please debug utilizing: journalctl --user -u strata-deck.service -n 50"
    exit 1
fi
