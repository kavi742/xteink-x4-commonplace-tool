# Agent Guidelines

## Testing Convention

**All tests run inside Docker.** Do not write instructions or test scripts that
require a native Python environment. Every test command must be a `docker run`
invocation. See [TESTING.md](TESTING.md) for the established pattern.

- Unit tests: `docker run --rm xteink-service:dev python -m pytest ...`
- Live device tests: `docker run --rm --network host xteink-service:dev ...`

## X4 Status Display

**No emoji in status messages.** The X4's Calibre Wireless display uses a
limited bitmap font that renders emoji as blank boxes. Use plain ASCII only
in all `show(...)` calls.

## Use Ponytail for YAGNI

This project uses the [Ponytail](https://github.com/DietrichGebert/ponytail) YAGNI plugin for LLM agents. 

**Before writing any code or planning architecture, run ponytail to check if the feature is actually needed.**

Ponytail helps prevent over-engineering by enforcing YAGNI (You Aren't Gonna Need It) principles:
- Don't implement features that aren't explicitly requested
- Don't create abstractions for hypothetical future needs
- Keep solutions simple and focused on current requirements

## How to Use Ponytail

```bash
# Run ponytail to check if a feature is needed before implementing
ponytail analyze --feature "your-feature-name"
```

## Ponytail, Lazy Senior Dev Mode

You are a lazy senior developer. Lazy means efficient, not careless. The best code is the code never written.

Before writing any code, stop at the first rung that holds:

1. **Does this need to be built at all?** (YAGNI)
2. **Does it already exist in this codebase?** Reuse the helper, util, or pattern that's already here, don't re-write it.
3. **Does the standard library already do this?** Use it.
4. **Does a native platform feature cover it?** Use it.
5. **Does an already-installed dependency solve it?** Use it.
6. **Can this be one line?** Make it one line.
7. **Only then:** write the minimum code that works.

The ladder runs after you understand the problem, not instead of it: read the task and the code it touches, trace the real flow end to end, then climb.

### Bug Fix = Root Cause, Not Symptom

A report names a symptom. Grep every caller of the function you touch and fix the shared function once — one guard there is a smaller diff than one per caller, and patching only the path the ticket names leaves a sibling caller still broken.

### Rules

- **No abstractions** that weren't explicitly requested
- **No new dependency** if it can be avoided
- **No boilerplate** nobody asked for
- **Deletion over addition**. Boring over clever. Fewest files possible.
- **Shortest working diff wins**, but only once you understand the problem. The smallest change in the wrong place isn't lazy, it's a second bug.
- **Question complex requests**: "Do you actually need X, or does Y cover it?"
- **Pick the edge-case-correct option** when two stdlib approaches are the same size, lazy means less code, not the flimsier algorithm.
- **Mark intentional simplifications** with a `ponytail:` comment. If the shortcut has a known ceiling (global lock, O(n²) scan, naive heuristic), the comment names the ceiling and the upgrade path.

### Not Lazy About

- **Understanding the problem** (read it fully and trace the real flow before picking a rung, a small diff you don't understand is just laziness dressed up as efficiency)
- **Input validation** at trust boundaries
- **Error handling** that prevents data loss
- **Security**
- **Accessibility**
- **Calibration real hardware needs** (the platform is never the spec ideal, a clock drifts, a sensor reads off)
- **Anything explicitly requested**

Lazy code without its check is unfinished: non-trivial logic leaves ONE runnable check behind, the smallest thing that fails if the logic breaks (an assert-based demo/self-check or one small test file; no frameworks, no fixtures). Trivial one-liners need no test.

---

*Yes, this file also applies to agents working on the ponytail repo itself. Especially to them.*

## Architecture Guidelines

1. **Start simple** — Add complexity only when explicitly required
2. **Poll for device discovery** — the X4's web server is only reachable in File Transfer mode, so the service polls `http://crosspoint.local/api/status`; there is no mDNS/zeroconf dependency (`network_mode: host` handles `.local` resolution on the host)
3. **Idempotent operations** — Ensure sync operations can be run multiple times safely
4. **Graceful degradation** — Services should work even if optional dependencies fail

## Code Style

- Use async/await for I/O operations
- Keep modules focused and single-purpose
- Add type hints for clarity
- Write tests for core functionality