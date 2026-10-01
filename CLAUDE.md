# Pantry inventory — rules for Claude Code

## Source of truth

`SPEC-v2_0-pantry-inventory.md` is authoritative. Older spec files (v1.0 and earlier) are superseded. If any are in the repo, ignore them wherever they disagree with v2.0.

## Who I am

I'm a Computer Engineering student building this to learn. Prefer plain, readable code over clever code, and comment the *why* at non-obvious spots.

## Files I write by hand

Never edit, rewrite, or "fix" these. You may read them, and review them when I ask.

- `server/app/scans.py` — if it doesn't exist yet, you may create the stub described in the build prompt. Once it exists, don't change it.
- `server/tests/test_core.py` — same rule: you may create it containing only a comment, then leave it alone.
- `scanner/client/frame.py` — framing (spec 5.3)
- `scanner/client/queue.py` — in-flight buffer (spec 5.5)
- `scanner/client/backoff.py` — retry timing (spec 5.6)
- `scanner/client/main.py` — the loop, and wiring the modules together (spec 5.1)
- `scanner/tests/test_queue.py`

Same rule as `scans.py` for all five: Claude may create the stub with the
documented interface, then never touch it again.

The scanner client is Python (pyserial), not C. The C file list this section
used to carry is gone with it; see the v2.1 addendum in the spec. `hw.py` is
now Claude's, because Python has no header/implementation split and what is
left is pure plumbing.

## When I ask for a review of my files

Explain each problem and why it's a problem, with line numbers. Don't write the fix unless I ask for it.

## Scope

Don't add features, endpoints, dependencies, or files the spec doesn't call for. If something seems missing, ask me.

## Working style

Work one stage at a time. At the end of each stage: run the tests, commit, and stop for my review before starting the next one.
