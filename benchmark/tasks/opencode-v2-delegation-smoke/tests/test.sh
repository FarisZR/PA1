#!/usr/bin/env bash
set -euo pipefail

printf 'OPENCODE_DELEGATION_OK\n' | cmp -s - /app/answer.txt
mkdir -p /logs/verifier
printf '1\n' > /logs/verifier/reward.txt
