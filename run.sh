#!/usr/bin/env sh
# gyeoljae 실행기 (Linux/macOS). 예: ./run.sh -u kim list   /  ./run.sh serve --port 8130
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$HERE/src${PYTHONPATH:+:$PYTHONPATH}" exec "${PYTHON:-python3}" -m gyeoljae "$@"
