# Modern desktop implementation plan

1. Preserve the existing scheduler behavior in `ui/scheduler.py` and re-export it from the legacy workbench. Existing scheduler and discussion regression tests must continue to pass.
2. Write failing `tests/test_web_bridge.py` coverage for the bridge's command allowlist, type/range validation, key privacy/preservation, cancellation and SQLite snapshots. Implement `ui/web_bridge.py` with the same command queue and settings backend; test again.
3. Build React/TypeScript source under `frontend/`, with a pywebview bridge adapter and study workspace, exercises, grades, discussion confirmation, search, wrong questions and editable settings. Type-check and build the local bundle, with no external CDN dependencies.
4. Add `ui/web_desktop.py`, the required pywebview dependency and the default main entry point. Preserve `--legacy-gui` and CLI compatibility. Build output is the sole static server root.
5. Test startup and teardown in a real native window, frontend interaction and visual layout with Playwright, then run `selftest`, `btest` and discussion regressions. Update README with startup/build instructions.
6. Copy only approved publication files to the existing separate GitHub publication repository, create a normal follow-up commit, and push main without force. Verify remote commit/tree, frontend bundle and absence of private files.

Implementation proceeds in the current session as authorized. Frontend work and the Python bridge have separate file ownership and a fixed contract, allowing independent implementation using the dispatching-parallel-agents workflow.
