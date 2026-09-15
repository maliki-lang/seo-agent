# Phase 0 baseline test report

Recorded before tracking-layer source edits on 2026-09-15.

## Environment

- Python: 3.9.6 (repo `.venv`, Apple Command Line Tools)
- Package manager: pip via `data_sources/requirements.txt`
- Branch: `feat/tracking-data-layer`
- Command used: `.venv/bin/python -m pytest -q tests`

## Results

| Command | Result |
|---|---|
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m compileall -q -f .` | exit 0 |
| `.venv/bin/python -m pytest -q tests` | 3 failed, 18 passed |
| `.venv/bin/python -m data_sources.modules.eval.regression run` | 1/1 passed |

## Pre-existing failures (not introduced by this work)

All three failures are in `tests/test_dataforseo_resilience.py`. They stub a retired `_post` DataForSEO task interface. The live module `data_sources/modules/dataforseo.py` is a Serper wrapper that calls `session.post`.

HANDOVER.md §7 already documents this mismatch. Passing tests at baseline: GSC config, GA4 compat, review-loop parser, research_quick_wins helpers.

## Notes

- ruff and mypy are not configured in this repository.
- System Python 3.9 is past Google client EOL; tests still import on 3.9.
- No live source pulls were attempted in this baseline.
