#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: %s RUN_ID\n' "$0" >&2
  exit 64
fi

RUN_ID=$1
if [[ ! "$RUN_ID" =~ ^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9_-])?$ ]]; then
  printf 'invalid run id: %s\n' "$RUN_ID" >&2
  exit 64
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_REPOSITORY_ROOT=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
REPOSITORY_ROOT=${PALIMPSEST_ROOT:-$DEFAULT_REPOSITORY_ROOT}
PYTHON_BIN=${PYTHON:-python}
MODEL=openai-codex/gpt-5.6-luna
SUITE="$REPOSITORY_ROOT/palimpsest/factory/evaluation/suites/transcribe/omp-extension-development-v1.yaml"
CASE_ID=vat-borg-cin-361-f004r-transcribe-development
OUTPUT_ROOT=${PALIMPSEST_AUTORESEARCH_OUTPUT_ROOT:-"$SCRIPT_DIR/out"}
OUTPUT_DIR="$OUTPUT_ROOT/$RUN_ID"
CANDIDATE_DIR="$OUTPUT_DIR/candidates"
OBJECT_ROOT=${PALIMPSEST_OBJECT_ROOT:-"$REPOSITORY_ROOT/library/evaluations/objects"}
mkdir -p -- "$CANDIDATE_DIR" "$OBJECT_ROOT"

BASELINE=$(
  "$PYTHON_BIN" "$SCRIPT_DIR/render_candidate.py" \
    --source "$SCRIPT_DIR/baseline.ts" \
    --role baseline \
    --model "$MODEL" \
    --output-dir "$CANDIDATE_DIR"
)
CHALLENGER=$(
  "$PYTHON_BIN" "$SCRIPT_DIR/render_candidate.py" \
    --source "$SCRIPT_DIR/extension.ts" \
    --role challenger \
    --model "$MODEL" \
    --output-dir "$CANDIDATE_DIR"
)

cd -- "$REPOSITORY_ROOT"
"$PYTHON_BIN" -m palimpsest bench fetch \
  --suite "$SUITE" \
  --asset-root "$REPOSITORY_ROOT/palimpsest/factory/evaluation" \
  --object-root "$OBJECT_ROOT" \
  > "$OUTPUT_DIR/fetch.log"

REPORT_PATH=$(
  "$PYTHON_BIN" -m palimpsest bench run \
    --suite "$SUITE" \
    --baseline "$BASELINE" \
    --challenger "$CHALLENGER" \
    --run-id "$RUN_ID" \
    --cases "$CASE_ID" \
    --max-cost 2.0 \
    --executor subprocess \
    --workers 1 \
    --db "$OUTPUT_DIR/factory.db" \
    --runs-root "$OUTPUT_DIR/runs" \
    --asset-root "$REPOSITORY_ROOT/palimpsest/factory/evaluation" \
    --object-root "$OBJECT_ROOT"
)

"$PYTHON_BIN" "$SCRIPT_DIR/emit_metrics.py" "$REPORT_PATH"
