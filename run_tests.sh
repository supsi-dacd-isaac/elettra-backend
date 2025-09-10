#!/bin/bash
# Test runner script that loads environment variables and runs pytest

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
fi

# Set PYTHONPATH
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

# Load environment variables from test.env if it exists
if [ -f "tests/test.env" ]; then
    echo "Loading environment variables from tests/test.env..."
    source tests/test.env
fi

# Run pytest with any arguments passed to this script
echo "Running tests..."
pytest "$@"
