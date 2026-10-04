> **Historical document.** This is the original planning PRD, kept for the
> design rationale. It predates several decisions that went the other way and
> is **not** an accurate description of what shipped. Notably: distribution is
> PyPI (`pip install argus-code`), not npm; Codex support shipped later (0.7.0)
> with a different design from the one sketched here — its rollout schema,
> usage source and sub-agent layout are documented in
> `python/argus/adapters/AGENTS.md`. For what Argus actually does today, read
> [README.md](./README.md) and [ARCHITECTURE.md](./ARCHITECTURE.md).

# Argus — Product Requirements Document (v0.2, MVP)

> v0.2 supersedes v0.1 (working name "AgentLens"). Schemas verified against on-disk logs. Tech stack and data model locked. Most v0.1 open questions are resolved; remaining ones are flagged in §10.

---

## 1. Overview

Developers increasingly run multiple coding agents side-by-side — Claude Code, OpenAI Codex, Cursor, Aider, Cline, and others. Each agent burns through tokens, picks models on its own, spins up sub-agents, and produces sessions that can range from a 30-second one-shot to a multi-hour autonomous run.

**Today, almost nobody analyzes what actually happened afterward.** How much did that refactor cost? Which model did Claude Code escalate to mid-session? Was Codex cheaper for the same task? Are tokens trending up week-over-week? The data exists — it's sitting in `~/.claude/` and `~/.codex/` — but no one looks at it because there's no good lens.

**Argus** is a local-first dashboard that reads those existing log files, normalizes them across agents, and shows the user cost, token, model, and session-level analytics with trends and cross-agent comparison. It's a personal tool for solo developers and power users who run agents heavily and want visibility into what they're spending and how they're using these tools.

The name comes from Argus Panoptes, the hundred-eyed watchman of Greek mythology — apt for a tool whose job is to watch every session across every agent, all the time.

---

## 2. Goals & Non-Goals

### Goals
- Single dashboard view of agent usage across Claude Code and Codex CLI.
- Cost, token consumption, model selection, and session-level detail.
- Trends over time and cross-agent comparison.
- Passive capture from existing log files — zero workflow change.
- Privacy-first: metadata only, no prompts or code stored, ever.
- Adapter pattern so additional agents can be added later without rewriting the core.

### Non-Goals (for MVP)
- No team / multi-user features.
- No quality / output evaluation. Cost and usage only.
- No cloud sync in v1 (architected for it, but ship local-only first).
- No prompt or response storage — explicitly out of scope.
- No real-time alerts or budget caps.
- No agent control (starting, stopping, configuring agents from the dashboard).

---

## 3. Target User

**Persona: The Power User Solo Developer**

- Uses Claude Code and Codex daily, often both in parallel.
- Probably also dabbling in Cursor / Aider / Cline.
- Pays out of pocket or through a personal company card.
- Has a vague sense their monthly bill is "high" but no granular view.
- Comfortable with `npm install -g`, localhost dashboards, editing config files.
- Cares about privacy — code is sensitive, prompts often contain proprietary logic.

**Not the target (yet):** team leads, engineering managers, finance, enterprise admins. Valid future personas, but require SSO/multi-user/RBAC features that bloat the MVP.

---

## 4. Key User Stories

1. *"Show me how much I spent on coding agents this week, broken down by tool."*
2. *"Which Claude Code session yesterday burned 800k tokens? What model did it use?"*
3. *"Am I spending more on Claude Code or Codex over the last 30 days?"*
4. *"Show me the cost trend — is my usage going up or down month over month?"*
5. *"Which model gets selected most often by Claude Code? How much of my spend is Opus vs Sonnet vs Haiku?"*
6. *"How long do my sessions typically run, and is there a cost-per-minute pattern?"*

---

## 5. MVP Scope

### 5.1 Supported Agents (v1)
- **Claude Code** — `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl` plus sub-agent files in `<sid>/subagents/`. Schema verified on disk (see §8.1).
- **OpenAI Codex CLI** — `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`. Schema verified on disk (see §8.2).
- Adapter pattern allows additional agents (Cursor, Aider, Cline) to be added post-MVP without changing the core.

### 5.2 Data Capture
- **Method:** read local log/session files written by each agent.
- **Mode:** `chokidar` watcher with per-file byte-offset tracking — only the new tail of each JSONL is parsed on change. Manual "Refresh" button triggers a full re-scan.
- **What's captured per session:**
  - Session id (derived from agent + native session uuid), start time, end time, duration
  - Agent name & version (`cli_version` for Codex; `version` field per-line for Claude Code)
  - Project / working directory path (derived from the `cwd` field inside the JSONL — **not** from the encoded directory name, which is lossy)
  - Per-turn: model used, fresh input tokens, output tokens, cache read/write tokens, tool-call count, timestamp
  - Aggregate session totals (computed, not read — agents don't write totals)
  - Per-agent metadata blob (Codex: `reasoning_output_tokens`, `sandbox_policy`, `effort`, `model_context_window`; Claude Code: `service_tier`, `inference_geo`, `attribution_agent`)
- **What's NOT captured:** prompts, responses, code diffs, file contents, file paths beyond top-level project root. **Metadata only.**

### 5.3 Cost Calculation

**Strategy: recompute from tokens, always.** Claude Code removed `costUSD` from its JSONL around v1.0.9 (June 2025), and Codex CLI never wrote cost. Recomputation is the only viable path that covers both agents and stays valid as users move between API direct, Bedrock, Vertex, and Max/Pro plans.

- Ships with a bundled static pricing JSON covering current Anthropic and OpenAI models (input, output, cache-write, cache-read rates).
- `argus pricing refresh` command pulls LiteLLM's `model_prices_and_context_window.json`, shows a diff against the bundled table, and applies on confirm.
- Per-turn cost: `(fresh_input × input_rate) + (output × output_rate) + (cache_write × cache_write_rate) + (cache_read × cache_read_rate)`, summed per session.
- Modifiers handled in the pricing layer: Anthropic `inference_geo=us` (1.1x), Bedrock/Vertex regional (+10%), Opus 4.6 fast-mode (6x), GPT-5.5 long-context multiplier above 272K input, batch API (50% off both sides).
- Each computed cost is stored alongside `pricing_table_version` and `computed_at` so the user can tell whether a number reflects current rates.
- If a legacy `costUSD` is found on a Claude Code turn, it's persisted as `agent_reported_cost_usd` for cross-check but never displayed as primary.

### 5.4 First-Run Experience

A user who installs Argus may have months of accumulated session logs. Argus must be useful in seconds, not minutes.

- **Two-phase ingest:**
  1. **Foreground (last 30 days):** scanned and ingested before the dashboard opens. Typical case is small enough to complete in a couple of seconds. Dashboard launches with the user's recent activity already populated.
  2. **Background (older sessions):** queued and ingested while the dashboard is live. A small "Backfilling history… 12/47 sessions" status indicator in the dashboard chrome polls a `/api/ingest/status` endpoint. "Trends → all-time" and historical aggregates fill out as backfill completes.
- A priority queue inside the collector lets new live events (active sessions writing to disk) preempt the backfill so live data is never delayed by historical scans.

### 5.5 Dashboard Views (MVP)

**Overview / Home**
- One large hero number: total cost (default 7d, switchable to today / 30d / all-time). This is the headline — visual hierarchy reflects that the persona's pain is "what did this week cost me."
- Total tokens, session count for the same window.
- Agent split (stacked bar: Claude Code vs Codex).
- Cost-over-time line chart (daily for 30d, weekly for all-time).
- Calendar heatmap of cost-per-day for the last ~3 months — power users intuit usage from spatial patterns better than from line charts.
- Pricing freshness footer: "Cost computed using pricing table v2026-05-02 — `argus pricing refresh` to update."

**Sessions List**
- Sortable, filterable table: date, agent, project, duration, primary model, total tokens, cost.
- Click a row → session detail.

**Session Detail**
- Per-turn timeline: model used, in/out tokens, cache reads, tool-call count, timestamp.
- Session totals, including a "sub-agent rollup" sub-row when Claude Code Task-tool sub-agents contributed.
- Per-agent metadata (Codex `reasoning_output_tokens`, `sandbox_policy`, `effort`; Claude Code `service_tier`, `inference_geo`).
- *No prompt or response content shown.*

**Trends**
- Line charts: cost / tokens / session count over time.
- Granularity toggle (daily, weekly, monthly).
- Group by agent or model.

**Compare**
- Side-by-side cards or table: Claude Code vs Codex on selected window — cost, tokens, sessions, avg cost/session, model mix.
- Visible "comparison limits" disclaimer about cache semantics (see §7) so users don't over-interpret a token-count delta.

### 5.6 Privacy & Security
- Metadata only — enforced at the collector layer (regex/strict-typed schemas reject any field not on the allowlist).
- All data stored locally (SQLite at `~/.argus/argus.db`) by default.
- Cloud sync hooks designed-in but disabled in v1.
- No telemetry sent home unless user opts in.
- Data export (JSON / CSV) and full-wipe option in settings.

---

## 6. Technical Architecture

### 6.1 Components

```
┌────────────────────────────────────────────────────────────────┐
│                            Argus                               │
│                                                                │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │   Adapters   │ → │   Collector  │ → │   Local Store    │    │
│  │ (per-agent)  │   │ (normalizer) │   │  (SQLite, WAL)   │    │
│  └──────────────┘   └──────────────┘   └─────────┬────────┘    │
│         ↑                                        │             │
│  watches ~/.claude, ~/.codex                     ↓             │
│                                          ┌───────────────┐     │
│                                          │   Dashboard   │     │
│                                          │ (Astro static)│     │
│                                          └───────────────┘     │
└────────────────────────────────────────────────────────────────┘
```

**Adapters** — one per agent. Each knows where logs live, the schema (and its versions), and how to map raw events to the normalized turn schema. New agent = new adapter file.

**Collector** — chokidar watcher with per-file byte offsets, parses incremental tail of each JSONL, normalizes via the adapter, dedupes (Claude Code requires `message.id` dedup — see §8.1), and writes to SQLite. Includes a priority queue so live events preempt historical backfill.

**Local Store** — SQLite via `better-sqlite3`, WAL mode, single file at `~/.argus/argus.db`.

**Dashboard** — Astro static build, served by a tiny `node:http` (or `hono`) server in the same process as the watcher. Charts via ECharts (tree-shaken: LineChart, BarChart, HeatmapChart, CanvasRenderer).

### 6.2 Tech Stack (Locked)

- **Language**: TypeScript (Node 24 LTS).
- **DB**: `better-sqlite3` with WAL mode. Fall back to Node 24's built-in `node:sqlite` only if `better-sqlite3`'s native binding fails on Windows-ARM.
- **File watcher**: `chokidar@4` plus per-file byte-offset tail (~30 LOC, no wrapper library).
- **UI**: Astro (`output: 'static'`). Framework-agnostic islands; React or Svelte per chart as convenient.
- **Charts**: ECharts, tree-shaken imports only.
- **Distribution**: `npm install -g argus-cli`. The package contains the compiled TypeScript, the bundled Astro `dist/`, and declares `argus` as the `bin` entrypoint. Optional Bun `--compile` single-binary artifacts can be added as a secondary download channel post-v1.

### 6.3 Data Flow
1. User installs (`npm install -g argus-cli`) and runs `argus start`.
2. Collector spins up, registers chokidar watchers on `~/.claude/projects/` and `~/.codex/sessions/`.
3. Two-phase ingest kicks off (foreground: last 30d, background: older — see §5.4).
4. As files change, the watcher reads only the new bytes since the last offset, parses each new JSONL line, normalizes via the adapter, dedupes, writes to SQLite.
5. Dashboard polls `/api/*` endpoints and re-renders.
6. Manual "Refresh" button triggers a full re-scan from byte-offset 0 (catches anything missed if the watcher was offline).

---

## 7. Data Model (Normalized Schema)

Two core tables for MVP. Cache fields follow the cross-provider normalization rules in §7.1.

**`sessions`**
| Field | Type | Notes |
|---|---|---|
| id | TEXT (PK) | `<agent>:<native_session_uuid>` |
| agent | TEXT | `claude_code`, `codex` |
| agent_version | TEXT | latest seen in session (Claude Code: `version` field; Codex: `cli_version`) |
| project_path | TEXT | derived from `cwd` field inside JSONL, never from encoded dir name |
| started_at | TIMESTAMP | first turn's timestamp |
| ended_at | TIMESTAMP | last turn's timestamp; nullable for live sessions |
| duration_sec | INT | computed |
| total_fresh_input_tokens | INT | summed from turns |
| total_output_tokens | INT | summed from turns (includes Codex `reasoning_output_tokens`) |
| total_cache_read_tokens | INT | summed |
| total_cache_write_tokens | INT | summed; always 0 for Codex |
| total_cost_usd | REAL | computed via pricing table |
| primary_model | TEXT | most-used model in session, canonical id |
| turn_count | INT | de-duped by `message.id` for Claude Code |
| pricing_table_version | TEXT | which pricing snapshot was used to compute cost |
| computed_at | TIMESTAMP | when cost was computed |
| agent_reported_cost_usd | REAL | nullable; populated from legacy `costUSD` if present |
| metadata | JSON | per-agent extras (see §8) |

**`turns`** (per-message granularity)
| Field | Type | Notes |
|---|---|---|
| id | TEXT (PK) | adapter-generated, stable across re-scans |
| session_id | TEXT (FK) | |
| sequence | INT | order within session |
| timestamp | TIMESTAMP | |
| model | TEXT | canonical model id (after canonicalization — see §8.1) |
| model_raw | TEXT | original string as written by the agent |
| fresh_input_tokens | INT | |
| output_tokens | INT | |
| cache_read_tokens | INT | |
| cache_write_tokens | INT | always 0 for Codex |
| cache_write_5m_tokens | INT | Anthropic-only, NULL for Codex |
| cache_write_1h_tokens | INT | Anthropic-only, NULL for Codex |
| tool_calls_count | INT | |
| cost_usd | REAL | computed |
| metadata | JSON | per-turn extras |

A `pricing` table (or pricing JSON loaded into memory) maps canonical model id + modifier flags → rates.

### 7.1 Cross-Provider Cache Field Definitions

- **`fresh_input_tokens`** — input tokens billed at full rate.
  - Anthropic: `usage.input_tokens` (already excludes cached tokens).
  - OpenAI: `usage.prompt_tokens − usage.prompt_tokens_details.cached_tokens`.
- **`cache_read_tokens`** — input tokens served from cache at a discount.
  - Anthropic: `usage.cache_read_input_tokens`.
  - OpenAI: `usage.prompt_tokens_details.cached_tokens`.
- **`cache_write_tokens`** — input tokens written to cache, premium-billed.
  - Anthropic: `usage.cache_creation_input_tokens` (= `ephemeral_5m_input_tokens` + `ephemeral_1h_input_tokens`).
  - OpenAI: **always 0**. Cache writes are automatic and unbilled; no API field.
- **Anthropic-only**: `cache_write_5m_tokens`, `cache_write_1h_tokens` for TTL-tier fidelity.

**UI disclaimer (Compare view):** "OpenAI caches prompts automatically and does not bill or report cache writes, so OpenAI sessions show 0 cache writes. Cache hits are also not directly comparable: OpenAI's automatic cache makes hits common and incidental, while Anthropic's hits reflect intentional `cache_control` placement."

---

## 8. Adapter Specifications

### 8.1 Claude Code Adapter

**Location:** `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`. Sub-agent (Task tool) files at `<sid>/subagents/agent-<hash>.jsonl`. Per-project catalog at `<encoded-cwd>/sessions-index.json` is a useful cheap source of session metadata for the historical backfill.

**Session id:** the JSONL filename stem (also appears in every line's `sessionId` field).

**Fields used (per assistant line):**
- `message.model` → canonicalize and store both raw and canonical forms.
- `message.usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}` plus `cache_creation.{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}` when present.
- `message.id` — used **for dedup**. Claude Code writes one JSONL line per content block, all sharing the same `message.id` and the same `usage`. Naive summing multi-counts tokens. Adapter MUST dedupe by `message.id` (or `requestId`) before summing.
- `timestamp`, `cwd`, `version`, `gitBranch`, `service_tier`, `inference_geo`.
- `attribution_agent` and `agentId` (sub-agent context, when present).
- Tool-call counts: count `content[]` items where `type === "tool_use"` per de-duped turn.

**Sub-agent rollup:** sub-agent JSONLs are scanned alongside the parent and their token totals are summed into the parent session. They appear as a labeled sub-section in the session detail view, not as separate sessions.

**Resume semantics:** a resumed session appends to the same JSONL. Argus treats one `<sid>.jsonl` (plus sub-agent files) as one session. A long file from many resumes shows up as a long session. Acceptable trade-off; matches the user's mental model.

**Model-string canonicalization:** Claude Code writes models in two forms — dated (`claude-opus-4-5-20251101`) and short alias (`claude-opus-4-7`). The pricing table is keyed on canonical ids; a mapping table normalizes both forms into the same key, accounting for `service_tier` and `inference_geo` modifiers.

**Schema-drift guard:** the adapter declares a supported `version` range; sessions written by a newer Claude Code emit a warning ("Claude Code v2.1.X is newer than what Argus understands; some fields may be ignored") rather than failing. Older sessions (pre-v2.1.x) may be missing `iterations[]`, `slug`, `attribution_agent` — adapter treats these as optional.

**Gotchas to handle:**
- `tool_results` exceeding inline limit are spilled to `<sid>/tool-results/<hash>.txt`. The JSONL `tool_result.content` may reference rather than embed. Argus doesn't read these files (no prompt/response capture).
- Encoded-cwd is lossy (`:`, `\`, `/`, spaces, `.` all replaced with `-`). Always derive `project_path` from the JSONL `cwd` field.
- Some old sessions have only `<sid>/subagents/` with no top-level `<sid>.jsonl`. Adapter treats orphan sub-agent files as their own (sub-agent-only) session.

### 8.2 Codex CLI Adapter

**Location:** `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-timestamp>-<uuid>.jsonl`.

**Two schema generations** — adapter must handle both:
- **Legacy (≤v0.x, pre-Sep 2025):** bare events, no envelope. First line `{id, timestamp, instructions}`, then raw `{type:"message",...}` items. Limited token data; older sessions may have no token counts at all.
- **Current (≥v0.45-alpha, "rollout v2"):** every line wrapped as `{timestamp, type, payload}`. This is the default target.

**Session id:** uuid in filename; also appears as `session_meta.id`.

**Fields used:**
- `session_meta`: `id`, `timestamp` (started_at), `cwd` (project_path), `cli_version`, `originator`, `git`.
- `turn_context` (one per turn): `model`, `effort`, `summary`, `cwd` (latest), `sandbox_policy`, `approval_policy`.
- `event_msg` with `payload.type === "token_count"` (cumulative): `info.total_token_usage.{input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens, total_tokens}` and `info.last_token_usage` (last call only). **Per-turn deltas** computed by subtracting consecutive `total_token_usage` snapshots.
- `response_item` with `payload.type === "function_call"`: counted for `tool_calls_count`.

**Reasoning tokens:** Codex's `reasoning_output_tokens` is folded into `output_tokens` in the normalized schema (output billing line covers both). The raw value is preserved in the per-turn `metadata` JSON for users who want to see why a gpt-5.3-codex session looks output-heavy.

**Cache fields:** `cached_input_tokens` → `cache_read_tokens`. `cache_write_tokens` is always 0 for Codex (see §7.1).

**Session boundary:** one rollout file = one session. No resume linking — a new invocation produces a new file. `ended_at` is derived from the last line's timestamp; no explicit end marker exists.

**Codex-only metadata** stored in `metadata` JSON: `reasoning_output_tokens` (per turn), `sandbox_policy`, `approval_policy`, `effort`, `cli_version`, `originator`/`source` (CLI vs VSCode extension), `model_context_window`.

**Schema-drift guard:** `event_msg.payload.type` is treated as an open enum. Unknown types are logged to the parse-errors counter (see §6.1) but do not fail ingest.

---

## 9. Out of Scope (v2 and beyond)

- Cloud sync and multi-device aggregation.
- Team / org features (shared dashboards, RBAC, SSO).
- Additional agent adapters (Cursor, Aider, Cline, Roo, GitHub Copilot, ChatGPT desktop).
- Budget alerts and spending caps.
- Quality scoring / output evaluation.
- Prompt and response capture (would require a separate, explicit opt-in mode in a later major version).
- Per-project / per-repo cost attribution beyond top-level path.
- Tagging sessions ("feature work", "debugging", "exploration").
- Export integrations (Notion, Linear, GitHub Issues).
- Public benchmarks / community-shared anonymized stats.
- Codex `reasoning_output_tokens` as a separate column rather than folded into output. Revisit if users ask.
- Single-binary distribution (Bun `--compile`) — possible secondary channel later.

---

## 10. Open Questions & Risks

### Resolved since v0.1
- ~~Codex log location and schema~~ — verified on disk; see §8.2.
- ~~Naming~~ — Argus, package `argus-cli`, command `argus`.
- ~~Cost source-of-truth~~ — recompute always, store provenance, persist any legacy `agent_reported_cost_usd` for cross-check.
- ~~Cache token semantics~~ — normalized; see §7.1.
- ~~Session boundaries~~ — one Claude Code JSONL (+ sub-agents) = one session; one Codex rollout = one session.
- ~~Distribution channel~~ — `npm install -g argus-cli`.

### Still open
1. **npm name availability** — `argus-cli` needs an `npm view` confirmation before publish. Fallbacks: `@<scope>/argus`, `argus-agents`, `argus-watch`.
2. **Pricing modifiers UX** — the model-pricing JSON has to encode `inference_geo`, Bedrock/Vertex, fast-mode, batch-API, and GPT-5.5 long-context multipliers. How the user's session is *actually* billed (Max/Pro plan, Bedrock, etc.) isn't in the logs. v1 punt: assume API-direct rates and surface a "what if" toggle in settings ("show as: API direct / Bedrock / Vertex"). Not a blocker for v1 ship.
3. **Old Claude Code sessions** — pre-v2.1.x sessions may lack `iterations[]`, `slug`, `attribution_agent`. Backfill of these will be partial. Acceptable for v1; warn in UI when a session is from an old version.
4. **Sub-agent file orphaning** — some old subagent dirs exist with no parent `<sid>.jsonl`. Surfaced as sub-agent-only sessions in v1; revisit grouping later.

### Risks
- **Schema drift.** Both agents' formats change without notice. Mitigation: per-adapter version-range support, schema-drift warnings in the UI, parse-errors counter in settings.
- **Cross-agent comparison fairness.** Different agents do different things per token (reasoning models, sub-agents, tool overhead). Mitigation: surface the *kinds* of tokens (fresh, cache-read, cache-write, reasoning-folded), document comparison limits in the UI on the Compare view.
- **Privacy creep.** Easy to accumulate richer data over time. Mitigation: keep "metadata only" as a hard architectural rule for v1 — enforced at the collector with strict allowlists; any prompt/response capture lives behind a separate, explicit opt-in mode in a later version.
- **Pricing-table staleness.** Bundled JSON ages; users may see wrong numbers if they don't refresh. Mitigation: pricing freshness footer on every cost view, `argus pricing refresh` command, and a "pricing table is N days old" nudge in the dashboard chrome after 30 days.
- **Claude Code `message.id` dedup bug.** The single most likely place for a numeric-correctness bug. Mitigation: dedup is unit-tested with golden JSONL fixtures from real sessions before any UI numbers are trusted.

---

## 11. Success Criteria for MVP

A user installs Argus (`npm install -g argus-cli`), runs `argus start`, and within 60 seconds (including first-run ingest) can answer:
- *"How much did I spend on coding agents in the last 7 days?"* → hero number on Overview.
- *"Which agent did I use more?"* → agent split chart on Overview.
- *"What was my biggest session this week?"* → Sessions list, sort by cost.

If those three questions are answerable with one click each, MVP works.

---

## 12. Build Order (for solo execution)

1. **Claude Code adapter + SQLite store + normalized schema.** Includes `message.id` dedup, sub-agent rollup, model-string canonicalization. Unit-tested against golden JSONL fixtures.
2. **Codex CLI adapter.** Both legacy and current envelope schemas; reasoning-tokens folded into output; `cache_write_tokens = 0`. Unit-tested against fixtures.
3. **Pricing table + cost computation.** Bundled JSON, canonical model lookup, modifier handling. `pricing_table_version` and `computed_at` written alongside every cost.
4. **Local dashboard skeleton.** Astro static + tiny http server in same process. Overview (with hero number) + Sessions List + Session Detail.
5. **Trends + Compare views.** ECharts: line, bar, stacked-bar, calendar heatmap. Compare-view disclaimer.
6. **File watcher with byte-offset tailing + live updates.** Chokidar + per-file offsets; partial-line handling on the tail.
7. **Two-phase first-run ingest + backfill status API.** Priority queue so live events preempt backfill; `/api/ingest/status` endpoint; UI indicator.
8. **Settings, export, wipe, `argus pricing refresh`, pricing-config UI.**
9. **Packaging / distribution.** `npm publish argus-cli`. `bin/argus` entrypoint. Bundled Astro `dist/` inside the tarball.

Steps 1–4 alone produce a usable dual-agent tool — ship that to yourself first, iterate from there.

---

*End of PRD v0.2.*
