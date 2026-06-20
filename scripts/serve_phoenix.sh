#!/usr/bin/env bash
# Start local Arize Phoenix (trace UI + OTLP collector).
# Runs in an isolated uvx env so it doesn't conflict with arize-phoenix-otel in the project venv.
set -euo pipefail
echo "[phoenix] Starting local server → http://localhost:6006"
echo "[phoenix] Press Ctrl-C to stop."
exec uvx --from arize-phoenix phoenix serve
