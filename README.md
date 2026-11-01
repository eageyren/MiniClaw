# MiniClaw

MiniClaw is a voice-first family companion assistant. This repository is being implemented in the staged order described in `../开发前准备/`.

## Current stage: S13 — production readiness and family pilot preparation

S1 provides a FastAPI API, PostgreSQL persistence, Alembic migrations, LangGraph conversation flow with a separate PostgreSQL checkpoint schema, and server-sent event (SSE) streaming in the text chat flow. S2 added the large push-to-talk interface. Audio is recorded in the browser, uploaded once, converted in memory by server-side ffmpeg to 16 kHz mono WAV, transcribed, processed by the conversation graph, and synthesized to speech. Original and converted audio are not persisted. Voice metrics record API receive to STT return, STT return to LLM first token, completed LLM answer to playable TTS, playback duration, and browser-measured recording-end to playback-end; daily P50/P95 are available at `/api/voice/metrics/daily?day=YYYY-MM-DD`.

S3 adds the fixed daughter and granddaughter roles. Their configuration is stored per family in PostgreSQL, can be hot-reloaded through `POST /api/roles/import` using the example `api/packages/roles.yaml`, and can be reviewed at `GET /api/roles`. The pure prompt assembler is `api/app/role_config.py`; its layers are base persona, role configuration, shared facts, tool availability, and safety. Role switches are audited. Tool allowlists remain empty in S3. Direct questions about whether the assistant is a real family member are handled by a backend response guard.

S4 counts prompt tokens with `tiktoken`, keeps a configurable recent-turn window, and incrementally summarizes older turns when the context budget is exceeded. Summaries are stored in `conversation_summaries`; full message history remains in PostgreSQL. A failed summary call logs a warning and drops the oldest context first. Completed assistant messages record input/output token estimates, whether compression ran, and the context token count. Configure `CONTEXT_TOKEN_BUDGET`, `CONTEXT_KEEP_TURNS`, and `CONTEXT_SUMMARY_MAX_TOKENS`. LangGraph checkpoint state is reduced to the latest turn and inactive checkpoint threads are deleted after `CHECKPOINT_TTL_DAYS` (default 14); business messages and rolling summaries remain.

In development, open `/debug` for the selected conversation's prompt, summary, retained original messages, tool result counts, and token breakdown. The backing endpoint returns 404 unless `APP_ENV=development`.

The voice-first interface is the elder's main route. `/debug/chat` is a development-only text chat page for S1 acceptance and is linked from the home footer only when the web build uses `NEXT_PUBLIC_APP_ENV=development`. In development, it obtains a short-lived fixed-identity session automatically, so the S1 text acceptance flow does not require device pairing. The development Compose ports bind to loopback; the API hides this session endpoint outside `APP_ENV=development`.

S5 adds a central, read-only registry in `api/app/tools.py`: current date/time with timezone, scheduled reminders, and explicitly authorized shared family facts. Every tool validates JSON Schema parameters, has a timeout, returns its query time and execution duration, and clips oversized results. Read-only calls in one model round run concurrently. The agent allows at most three tool rounds and four executions per turn; three consecutive failures disable further tools and lead to a plain response. Tool call arguments/results are recorded with the assistant message for development review. No tool can write to the database.

S6 adds the `propose_reminder` confirmation-required tool. The agent requires the user to explicitly say the item, date, clock time, timezone, and repeat rule; absent details are asked about instead of guessed. A pending action is recited and requires one of two buttons. Each confirmation step expires after 30 seconds and defaults to rejection. Medication, money, or other-person actions require two sequential button approvals. Decisions and timeout outcomes are audited. At S6, approvals were written only to `staging_reminders`.

S7 converts newly approved candidates into durable `reminders`, with an idempotency key, optimistic version, source conversation/message, canonical RRULE, and next trigger timestamp. `reminder_service.py` owns state transitions and deterministic recurrence expansion in the reminder's IANA timezone. A separate Compose `scheduler` process claims due rows with `FOR UPDATE SKIP LOCKED`, writes a unique trigger occurrence and a separate pending delivery record, then advances recurrence or marks one-shot reminders triggered. Late occurrences are recorded as catch-up triggers. Invalid recurrence expansion marks the reminder `needs_reconfirmation`. Read APIs expose today's reminders, this week's reminders, and unacknowledged deliveries; an acknowledgement endpoint records elder confirmation. The agent can complete, reschedule, pause, resume, or cancel a reminder only through the S6 confirmation node; cancel and reschedule require two approvals, and every change checks the version read from the reminder tool. S11 adds push delivery and escalation on top of the durable in-app record.

S8 adds one `memories` table with `role_private` and explicitly authorized `family_shared` scopes; operational reminders stay in their own table. Writes require an explicit “记住” request and are limited to simple preference and preferred-name statements. Health, medication, financial, and complaint content is excluded. Every saved record includes its family/member, scope, role, source conversation/message, confidence, timestamps, expiry, and status. Only the active role's private memories plus authorized shared memories are injected, ordered newest first, with an 800-token cap and source text. Memory is treated as untrusted user data. `/memory` lists source excerpts and supports soft deletion; role-wide deletion and shared-scope revocation are also available. Deleted and expired records are filtered immediately and physically cleaned after retention. “别记” receives a fixed “好，不记。” response.

S9 adds a separate `/family` layout for role settings, shared family facts, read-only reminders, authorized shared memories, data export/deletion, and change history. Role edits use optimistic versions and apply from the database on the elder's next turn. Every role/fact/memory/authorization change stores actor, before/after values, timestamp, and a short elder-facing summary; the elder page displays the latest summary. The family memory endpoints return only authorized `family_shared` rows, and export omits private conversations and role-private memories.

S10 adds bearer-token identity, a six-digit 10-minute elder pairing code, family OTP login/pairing, short-lived access tokens with silent refresh, per-family role/tool permissions, family-link revocation, device unpairing, and the elder-facing `/access` authorization page. Protected APIs derive family and member identity from the bearer token and use tenant-scoped queries; inaccessible identifiers return 404. Configure `SMS_GATEWAY_URL` and `SMS_GATEWAY_TOKEN` for a JSON SMS webhook in production. Development returns the OTP in the response. The Compose bootstrap still initializes the first family/member from `FAMILY_ID` and `MEMBER_ID`; additional family onboarding/provisioning is an operator task in this iteration.

S11 adds an installable PWA shell, service worker Web Push subscriptions kept separate for the elder device and family devices, a durable delivery idempotency key, bounded retries (one, three, and seven minutes), and an independent scheduler that delivers notifications. When an in-app reminder is still unacknowledged at five minutes, the open elder page repeats its spoken reminder and the worker queues a second push. At fifteen minutes, the worker marks it escalated and sends push only to family devices whose owners explicitly opted in; the family dashboard lists unacknowledged reminders and delivery/device alerts. Delivery status changes carry timestamps. Development offers a simulated trigger button and API. Generate VAPID keys and configure `PUSH_VAPID_PUBLIC_KEY`, `PUSH_VAPID_PRIVATE_KEY`, and `PUSH_VAPID_SUBJECT`; without keys, in-page speech still works while the family dashboard reports that push is not configured.

Role import body is JSON shaped as `{"yaml_text":"<contents of roles.yaml>"}`. S3 intentionally has no role editor screen; it imports only the two fixed role keys. Every role switch plays and stores a clear self-introduction before the elder starts chatting.

`FAMILY_ID` and `MEMBER_ID` identify the initial family created during database bootstrap; normal requests no longer trust browser-supplied identity. `STT_PROVIDER` and `TTS_PROVIDER` currently accept `openai_compatible`; model and voice are independently configured with `STT_MODEL`, `TTS_MODEL`, and `TTS_VOICE`. Select the actual provider/model only after S0 tests against the target family's dialect, latency, cost, and data policy. Voice requires a configured compatible API key. `OPENAI_BASE_URL` is shared by chat, STT, and TTS.

### Start

1. Copy `.env.example` to `.env` and set `OPENAI_API_KEY` (optional for fallback mode).
2. Run `docker compose up --build` from this directory.
3. Open [http://localhost:3000](http://localhost:3000). API health is at [http://localhost:8000/health](http://localhost:8000/health).

The API applies the Alembic migration on startup. PostgreSQL data is stored in the `postgres_data` volume, so conversation history survives page reloads and API/container restarts. To stop services while retaining data, use `docker compose down`; `docker compose down -v` deletes the database volume.

For voice, allow microphone access in the browser. `localhost` is a secure context for local development. Production microphone access requires HTTPS with a valid certificate and domain; do not expose the S1 fixed development identity publicly. The server image includes ffmpeg for in-memory audio conversion. The browser records webm/opus; conversion to 16 kHz mono WAV happens on the API server.

### Current product boundary

Current boundaries: no speech interruption, real-time speech, always-on microphone, voice cloning, or offline recognition. Web Push requires HTTPS and valid VAPID keys; browser/OS delivery cannot be guaranteed and still requires target-device field verification. Additional family onboarding/provisioning is not exposed in the UI. S0 dialect and latency measurements remain outstanding, and the configured OpenAI-compatible provider must be verified against the chosen vendor.

S12 adds request IDs across HTTP, STT, Agent, tools, TTS, and reminder delivery, with trace timelines at `/debug/trace` (elder) and `/api/family/traces/{request_id}` (family). Production stores trace rows in PostgreSQL; development also emits structured console logs. `/family/usage` shows family-facing weekly activity, reminder outcomes, and monthly estimated cost. Configure the `COST_*` unit rates and `MONTHLY_COST_LIMIT_USD` to match the selected provider. Evaluation fixtures live in `evals/`; `python evals/run_eval.py --predictions evals/predictions.json` writes a Markdown report and compares its scores with the previous run. Reminder predictions, role review results, and executed authorization evidence are supplied as offline input; missing cases score as failures. The runner does not claim unexecuted API security scenarios passed.

The family usage page can export weekly activity and reminder outcomes as CSV. The family-only API also returns per-session rounds/retries/cancellations, daily funnel counts, daily cost inputs, and the current month's estimate; the family page shows the family-facing totals without model-call details.

S13 production files are separate from development: `docker-compose.prod.yml`, `Caddyfile`, `web/Dockerfile.prod`, and `ops/`. Point `APP_DOMAIN` DNS at the host and open inbound ports 80/443. Set a unique `POSTGRES_PASSWORD` of at least 24 URL-safe characters, `OPENAI_API_KEY`, non-development bootstrap `FAMILY_ID`/`MEMBER_ID`, a real `FAMILY_PHONE`, `SMS_GATEWAY_URL`/`SMS_GATEWAY_TOKEN` for family OTP, and both VAPID keys in `.env`; then run `sh ops/deploy-prod.sh`. The script rejects sample values, validates configuration, builds images, runs Alembic migration preflight before replacing the current services, starts the stack, and waits up to five minutes for API health. Caddy obtains and renews HTTPS certificates. Check status with `sh ops/status-prod.sh`; API health is exposed at `https://$APP_DOMAIN/health`.

The production backup service writes a PostgreSQL custom-format dump every 24 hours to `backups/`, records successes and failures in `backups/backup.log`, and removes dumps older than 14 days. Keep `backups/` out of source control and copy it to separately protected storage as an additional disaster-recovery measure. Before replacing production data, use `sh ops/drill-restore.sh backups/miniclaw-<timestamp>.dump` to restore into an isolated drill database and inspect the recovered records. `sh ops/restore-backup.sh <dump>` is the explicitly confirmed production restore path. Do not call a recovery drill complete until that command has actually succeeded against a real dump.

If navigation fails offline after the PWA has loaded once, the service worker serves a cached, static `offline.html` with a family call link, a preset local browser voice message, and retry action. Caddy also serves this fallback on upstream failure; a database health response shows “正在整理，稍等一下”. `FAMILY_PHONE` is embedded into this static page at production build time. The comfort message and browser speech synthesis do not require the API or a model; audible playback still depends on the device's local Chinese speech voice.

### S13 pilot gates — do not start a family trial until every hard gate is measured

Record results in a dated pilot sheet. A code change alone is not evidence of a gate passing.

| Gate | Required evidence | Threshold |
|---|---|---|
| Voice startup | 5 target elders × 3 sessions, first-attempt success | ≥ 80% |
| Reminder confirmation independence | Same sessions, no coaching | ≥ 70% |
| Repeats | Voice session records | ≤ 1.5 per session |
| Voice latency | Release → first spoken byte, timestamp distribution | P50 ≤ 2.5 s; P95 ≤ 4 s |
| Role distinction | Blind matching of 6 replies | ≥ 75%; consistency ≥ 4/5 |
| Identity and memory safety | 20 adversarial identity prompts; 20 cross-role private facts; delete-and-recall drill | 0 impersonation/false contact, 0 leaks, 0 recalls after deletion |
| Reminder reliability | 100 injected reminders across restart and concurrent scheduling | ≥ 99% on time; 0 duplicates; 0 lost |
| Access control | Cross-family read/write, revoked access, forged token scenarios | 100% blocked |
| Failure feedback | Walk every §6.5 outage case on target devices | 100% understandable recovery; 0 blank screens |
| Four-week retention | Weekly use ≥ 3 times | ≥ 60% of participants |

The product baseline also calls for a voluntary target-family dialect and provider spike (S0); no dialect accuracy or production latency claim is made until that has been measured. Current repository work does not include field data, a real-device push confirmation, a successful backup restore drill, or completed evaluation predictions. S13 code delivery therefore prepares for a pilot but does not certify that a family is ready to start one.

For local deterministic checks, install `api/requirements-dev.txt` and run `cd api && python -m pytest`. Database-backed isolation, restart/concurrency, real push delivery, and production restore still require dedicated service fixtures and are not covered by the unit checks.

See [FAMILY_GUIDE.md](FAMILY_GUIDE.md) for the one-page family explanation.
