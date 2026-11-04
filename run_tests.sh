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
export ELETTRA_TESTING=1

# Load environment variables from test.env if it exists
if [ -f "tests/test.env" ]; then
    echo "Loading environment variables from tests/test.env..."
    set -a  # automatically export all variables
    source tests/test.env
    set +a  # stop automatically exporting
fi

# Fail before pytest collection if a local run does not use the dedicated
# frontend/backend/database staging stack. CI is validated against its own
# ephemeral PostgreSQL service by the same script.
python test_support/staging_targets.py

# Run pytest with any arguments passed to this script
echo "Running tests..."
pytest "$@"
