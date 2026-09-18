Before editing the answer file, use OpenCode's native `general` subagent tool exactly once.
Ask the subagent to read `/app/delegation-fixture.txt` and report its exact contents back to you.
Wait for the subagent response, then create `/app/answer.txt` containing exactly
`OPENCODE_DELEGATION_OK` followed by a newline. Verify the bytes and finish.
