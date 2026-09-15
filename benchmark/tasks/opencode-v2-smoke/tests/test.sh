#!/bin/sh
set -eu

printf 'OPENCODE_V2_OK\n' | cmp -s - /app/answer.txt
mkdir -p /logs/verifier
printf '1\n' > /logs/verifier/reward.txt
