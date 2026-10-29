#!/bin/bash
#
# Quick run script for Preparation Recommendation System
#
# Usage:
#   ./run.sh "用助熔剂法制备AlInSe₃" 20  # query and top-k
#   ./run.sh --help                       # Show help
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

# Check if virtual environment exists
if [ ! -d "$PROJECT_ROOT/.venv" ]; then
    echo "Virtual environment not found. Please run install.py first:"
    echo "  python resources/install.py"
    exit 1
fi

# Check if dependencies are installed
if ! "$VENV_PYTHON" -c "import openai" 2>/dev/null; then
    echo "Dependencies not installed. Please install them first:"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# Load environment variables
if [ -f "$PROJECT_ROOT/.env" ]; then
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
fi

# Parse arguments
QUERY="${1:-用助熔剂法制备AlInSe₃}"
TOP_K="${2:-20}"
DEBUG_FLAG=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --debug)
            DEBUG_FLAG="--debug"
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [QUERY] [TOP-K] [OPTIONS]"
            echo ""
            echo "Arguments:"
            echo "  QUERY      Material query (default: 用助熔剂法制备AlInSe₃)"
            echo "  TOP-K      Number of similar materials (default: 20)"
            echo ""
            echo "Options:"
            echo "  --debug    Enable debug mode"
            echo "  --help     Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Run the pipeline
echo "Running Preparation Recommendation Pipeline..."
echo "  Query: $QUERY"
echo "  Top-K: $TOP_K"
echo ""

"$VENV_PYTHON" "$PROJECT_ROOT/runner/run_pipeline.py" \
    --query "$QUERY" \
    --top-k "$TOP_K" \
    $DEBUG_FLAG

echo ""
echo "Pipeline completed! Check the data/ directory for results."
