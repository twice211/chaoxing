# Reliability implementation plan

The user approved the five reviewed improvements in order, with existing local work preserved and GitHub updated after verification.

- [x] 1. Atomic database replacement: validate rows first; execute deletion and insertion in one locked SQLite transaction; preserve previous chapter and grade data on failure. Cover validation errors, SQLite failures and successful/empty replacements.
- [x] 2. Reliable desktop requests: identify accepted requests and terminal outcomes; never silently discard queued commands when starting a long task. Associate settings-save feedback with its request. Preserve legacy command/queue compatibility.
- [x] 3. Responsive AI operations: keep all Playwright operations on their owning scheduler thread; run AI work independently, pass plain inputs, deliver completion on the scheduler thread, and ignore cancelled/stale results. Cover pause/cancel responsiveness, failures and shutdown without real API calls.
- [x] 4. Course-scoped results: attach request and course context to desktop events; display grades, discussions, searches and wrong questions for the current course only. Cover course switching and late completion.
- [x] 5. Atomic settings files: render and validate before changing the destination; use a securely created temporary file and atomic replacement; retain the original and clean temporary data on failure. Preserve managed-block content and blank-key behavior.
- [x] 6. Consolidate interfaces and checks: use accurate types for the new request/event boundaries, split only the code needed by the changes, and run existing tests/build through GitHub CI. No broad rewrite or unrelated dependency upgrades.

Each item starts with a regression that fails before the fix, followed by focused tests and a review of its impact. Integration validation includes offline/browser checks, native desktop startup, Python regressions, React typecheck/build and frontend tests. No real platform submissions or paid AI requests are used. Publish a normal follow-up commit from the separate publication copy after a credential scan; retain the user's local configuration and original history.
