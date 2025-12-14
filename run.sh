#!/bin/bash

# --- 1. Find the script's own directory and change to it ---

# Get the directory of the currently executing script
SCRIPT_DIR=$(dirname "$0") # A simpler, often sufficient way, especially for autostart
# A more robust way, especially if the script is symlinked:
# SCRIPT_DIR=$(dirname "$(readlink -f "$BASH_SOURCE")")

# Change the Current Working Directory (CWD) to the script's directory
cd "$SCRIPT_DIR"

# --- 2. Execution and Logging (Now, pwd will reflect the new location) ---
# Capture the *new* current directory
CURRENT_DIR=$(pwd)

. ./venv/bin/activate
python nv_monitor_service.py
