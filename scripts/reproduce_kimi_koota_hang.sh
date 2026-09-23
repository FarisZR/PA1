#!/usr/bin/env bash
# Replay the verifier of the Kimi K3 Pi trial on koota-composite-trait-aspects.
#
# The trial's verifier timed out after 1,800 s, so Pier recorded no reward. This
# script repeats the verifier's new-test step offline: it applies the model's
# patch (data/kimi-koota-verifier-audit/model.patch) and the task's hidden
# test.patch to the task image and runs tests/aspect.test.ts with a time limit.
# With --reference it applies the task's reference solution instead, as a
# control. No model is called and the container has no network.
#
#   scripts/reproduce_kimi_koota_hang.sh <task-image> <deepswe-task-dir> [--reference]
#
# <task-image> is the built environment image of the task, for example
# koota-composite-trait-aspects__jwznka9-main:latest from the original run.
# <deepswe-task-dir> is ../DeepSWE/tasks/koota-composite-trait-aspects.
# Exit status 124 means the test run hit the time limit.
set -euo pipefail

image=${1:?task image}
task_dir=$(cd "${2:?DeepSWE task directory}" && pwd)
mode=${3:-model}
limit=${LIMIT_SECONDS:-120}
patch="$(cd "$(dirname "$0")/.." && pwd)/data/kimi-koota-verifier-audit/model.patch"

if [ "$mode" = "--reference" ]; then
    apply='git apply --binary /work/solution.patch'
else
    # The grader resets files touched by test.patch before applying it.
    apply='git apply --binary /work/model.patch && rm -f packages/core/tests/aspect.test.ts'
fi

docker run --rm --network none \
    -v "$patch:/work/model.patch:ro" \
    -v "$task_dir/solution/solution.patch:/work/solution.patch:ro" \
    -v "$task_dir/tests/test.patch:/work/test.patch:ro" \
    "$image" bash -lc "
        set -e
        cd /app
        $apply
        git apply /work/test.patch
        cd packages/core
        set +e
        timeout $limit pnpm vitest run tests/aspect.test.ts
        status=\$?
        echo \"vitest exit status: \$status\"
        exit \$status
    "
