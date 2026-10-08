# React desktop migration

The user approved React + pywebview, preserving the Python implementation, with a local delivery followed by a GitHub update.

## Boundaries

- Extract the existing browser Scheduler unchanged into `ui/scheduler.py`; retain its import from `ui/workbench.py` for compatibility.
- Add a pywebview entry point and a narrow JavaScript bridge. Reuse the single browser thread, queues, SQLite store and settings persistence.
- Build a local React/TypeScript interface covering courses, sections, playback/reading, exercises, grades, discussions, knowledge search, wrong questions and all existing editable settings.
- The default desktop launcher uses the new interface. Preserve `--legacy-gui` and existing CLI commands.
- Keep the API key on the Python side. An empty password input preserves the saved key. Clearing it requires an explicit operation. The frontend only receives whether a key exists.
- Preserve existing examination guards and discussion confirmation tokens. Do not execute real platform writes or paid AI requests during verification.

## Bridge contract

`bootstrap()` returns `{ok: true, data: Snapshot}`; `poll()` returns `{ok: true, data: {events, snapshot}}`. Failures return `{ok: false, error: string}`. `command(action, params)` validates an explicit command and returns acknowledgement, then the browser thread executes it.

Snapshot fields: `ready`, `busy`, `login_pending`, `initialization_error`, `course_id` (number/null), `courses` (id/name), `sections` (id/title/kind/progress/done/chapter_id), `stats` (total/finished), `ai` (configured/enabled/model/base_url), and `settings` (values/fields/groups). Settings fields have key/kind/label/group; values contain no secret.

Events preserve scheduler `{level, payload}` semantics. Logs and backend errors redact the saved API key. `discuss_confirm` carries a structured token/text request and must be answered explicitly through `discuss_auto_confirm`.

Commands: login/courses/select_course/catalog/open_item/play/pause/resume/stop_play/cancel/auto_next/task_tab/parse/auto/auto_chain/stop/grades/discuss_preview/discuss_fill_next/discuss_auto/discuss_auto_confirm/discuss_confirm/discuss_discard/discuss_not_published/discuss_set_max/read/dump/wrong_list/search/ai_check/ai_test/save_settings. Existing parameter names remain unchanged. `save_settings` takes `values` and optional `clear_api_key`; `ai_test` tests the saved configuration. Logout is confirmed by the UI because existing behavior clears local course records.

## Visual direction

A practical study workspace: cool pale background (#F4F6FB), white panels, deep navy navigation (#172449), blue action color (#345FEA), slate text (#27344A), green completed state (#26816A). Use system Chinese sans-serif typography, generous spacing, an asymmetric workspace and restrained borders. Avoid decorative statistics or fabricated course data. Empty, loading, offline and error states explain the next action. Respect keyboard focus and reduced motion.

## Verification and publication

Test invalid commands and parameter boundaries, asynchronous dispatch/cancellation, secret preservation and redaction, local-data snapshots, confirmation flow, native-window startup and shutdown. Build and type-check the React bundle, exercise its controls with Playwright, inspect screenshots, and rerun existing Python checks. Publish a descendant of the verified GitHub main commit using the separate publication copy. Rescan files and PDFs for credentials/private data; never upload local profiles, configuration, dependencies, diagnostics or old local history.
