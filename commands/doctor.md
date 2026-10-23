---
description: Check the repository against the dev-factory contract with spec-doctor
argument-hint: "[--no-run]"
---

Run:

    python3 "${CLAUDE_PLUGIN_ROOT}/tools/spec_doctor.py" . $ARGUMENTS

(`--no-run` skips executing the declared build and test commands.)

Report each finding with the file and the identifier it names, grouped by file. Do not fix
anything unless the owner asks.

Say plainly what `spec-doctor` cannot see: it checks shapes and identifiers, not prose. A
sentence that miscounts rules or cites a stale section is caught by `/dev-factory:review`, not
here.
