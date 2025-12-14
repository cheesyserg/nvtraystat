#!/bin/bash

# --- Configuration ---
VENV_NAME="venv" # Name of the virtual environment directory
REQUIREMENTS_FILE="requirements.txt" # Name of the requirements file

echo "--- Starting Python Environment Setup Script ---"

# --- 1. Check for Python Installation ---
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
    echo "Python 3 found."
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
    echo "Using default 'python' command. Ensure it's the desired version."
else
    echo "Error: Python (python3 or python) is not installed."
    echo "Please install Python and run the script again."
    exit 1
fi

# --- 2. Check for virtualenv Installation ---
# Note: In modern Python, 'python3 -m venv' is often preferred,
# but we check for 'virtualenv' as requested.
if ! command -v virtualenv &>/dev/null; then
    echo "Error: 'virtualenv' is not installed."
    echo "Please install it using: $PYTHON_CMD -m pip install virtualenv"
    echo "or ensure 'python3 -m venv' is available if you modify the script."
    exit 1
fi
echo "virtualenv found."

# --- 3. Check for requirements.txt file ---
if [ ! -f "$REQUIREMENTS_FILE" ]; then
    echo "Error: The required file '$REQUIREMENTS_FILE' was not found in the current directory."
    echo "Please create a '$REQUIREMENTS_FILE' file with your dependencies."
    exit 1
fi
echo "'$REQUIREMENTS_FILE' found."

# --- 4. Create and Activate Virtual Environment ---

# Check if the virtual environment already exists
if [ -d "$VENV_NAME" ]; then
    echo "Virtual environment '$VENV_NAME' already exists. Skipping creation."
else
    echo "⚙️ Creating virtual environment '$VENV_NAME'..."
    virtualenv "$VENV_NAME"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create virtual environment."
        exit 1
    fi
    echo "Virtual environment created successfully."
fi

# Activate the virtual environment
# Note: The activation is temporary for this script's execution.
# To activate it permanently in your current shell, you must run:
# source ./$VENV_NAME/bin/activate
echo "Activating virtual environment..."
source "./$VENV_NAME/bin/activate"

# --- 5. Install Dependencies ---
echo "Installing modules from '$REQUIREMENTS_FILE'..."
pip install -r "$REQUIREMENTS_FILE"

# Check the exit status of the pip command
if [ $? -eq 0 ]; then
    echo "All modules installed successfully."
else
    echo "Error: Failed to install one or more modules."
    echo "Check the errors above for details."
fi

# --- 6. Deactivate Virtual Environment ---
# It's good practice to deactivate after the work is done within the script.
deactivate
echo "Virtual environment deactivated."

echo "--- Script Finished ---"
echo "You can now manually activate the environment by running: source $VENV_NAME/bin/activate"
