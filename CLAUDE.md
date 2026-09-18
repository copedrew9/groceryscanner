# Pantry inventory — rules for Claude Code

## Source of truth

`SPEC-v2_0-pantry-inventory.md` is authoritative. Older spec files (v1.0 and earlier) are superseded. If any are in the repo, ignore them wherever they disagree with v2.0.

## Who I am

I'm a Computer Engineering student building this to learn. Prefer plain, readable code over clever code, and comment the *why* at non-obvious spots.

## Files I write by hand

Never edit, rewrite, or "fix" these. You may read them, and review them when I ask.

- `server/app/scans.py` — if it doesn't exist yet, you may create the stub described in the build prompt. Once it exists, don't change it.
- `server/tests/test_core.py` — same rule: you may create it containing only a comment, then leave it alone.
- `scanner/src/hw.h`
- `scanner/src/frame.c`, `scanner/src/frame.h`
- `scanner/src/queue.c`, `scanner/src/queue.h`
- `scanner/src/backoff.c`, `scanner/src/backoff.h`
- `scanner/src/main.c`
- `scanner/tests/test_queue.c`

## When I ask for a review of my files

Explain each problem and why it's a problem, with line numbers. Don't write the fix unless I ask for it.

## Scope

Don't add features, endpoints, dependencies, or files the spec doesn't call for. If something seems missing, ask me.

## Working style

Work one stage at a time. At the end of each stage: run the tests, commit, and stop for my review before starting the next one.
