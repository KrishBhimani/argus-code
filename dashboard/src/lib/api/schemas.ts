import { z } from 'zod';

export const Session = z.object({
  id: z.string(),
  agent: z.string(),
  agent_version: z.string().nullable(),
  project_path: z.string(),
  started_at: z.string(),
  ended_at: z.string().nullable(),
  duration_sec: z.number().nullable(),
  total_fresh_input_tokens: z.number(),
  total_output_tokens: z.number(),
  total_cache_read_tokens: z.number(),
  total_cache_write_tokens: z.number(),
  total_cost_usd: z.number(),
  primary_model: z.string(),
  turn_count: z.number(),
  pricing_table_version: z.string(),
  computed_at: z.string(),
  agent_reported_cost_usd: z.number().nullable(),
  metadata: z.record(z.unknown()).default({}),
});
export type Session = z.infer<typeof Session>;
export const SessionList = z.object({ sessions: z.array(Session) });

export const Turn = z.object({
  id: z.string(),
  session_id: z.string(),
  sequence: z.number(),
  timestamp: z.string(),
  model: z.string(),
  fresh_input_tokens: z.number(),
  output_tokens: z.number(),
  cache_read_tokens: z.number(),
  cache_write_tokens: z.number(),
  tool_calls_count: z.number(),
  cost_usd: z.number(),
  metadata: z.record(z.unknown()).default({}),
});
export const SessionDetail = z.object({ session: Session, turns: z.array(Turn) });

export const TimelineToolCall = z.object({
  tool_name: z.string(),
  tool_use_id: z.string(),
  is_error: z.union([z.literal(0), z.literal(1)]),
  input_size: z.number(),
  subagent_type: z.string().nullable(),
  error_text: z.string().nullable(),
});
export const TimelineTurn = z.object({
  sequence: z.number(),
  timestamp: z.string(),
  model: z.string(),
  fresh_input_tokens: z.number(),
  cache_read_tokens: z.number(),
  cache_write_tokens: z.number(),
  output_tokens: z.number(),
  cost_usd: z.number(),
  tool_calls: z.array(TimelineToolCall),
});
export type TimelineTurn = z.infer<typeof TimelineTurn>;
export const Timeline = z.object({ search_enabled: z.boolean(), turns: z.array(TimelineTurn) });
export const ToolOutput = z.object({ search_enabled: z.boolean(), found: z.boolean(), text: z.string().nullable() });

export const TopSession = z.object({
  id: z.string(),
  project_path: z.string(),
  primary_model: z.string(),
  started_at: z.string(),
  days_active: z.number(),
  window_tokens: z.number(),
  window_cost_usd: z.number(),
});
export const Overview = z.object({
  window: z.string(),
  total_cost_usd: z.number(),
  total_tokens: z.number(),
  session_count: z.number(),
  agent_split: z.record(z.object({ cost: z.number(), sessions: z.number(), tokens: z.number() })),
  cost_by_day: z.record(z.number()),
  cost_by_model: z.record(z.number()),
  tokens_by_day: z.record(z.number()),
  tokens_by_model: z.record(z.number()),
  top_sessions: z.array(TopSession),
  /** Same metrics over the equal-length range right before the window; null for "all". */
  prior_window: z.object({ tokens: z.number(), cost_usd: z.number(), sessions: z.number() }).nullable(),
});
export type Overview = z.infer<typeof Overview>;

export const ToolsOverview = z.object({
  window: z.string(),
  total_calls: z.number(),
  total_errors: z.number(),
  tool_leaderboard: z.array(z.object({ name: z.string(), calls: z.number(), errors: z.number(), error_rate: z.number() })),
  mcp_servers: z.array(z.object({ server: z.string(), calls: z.number(), errors: z.number(), tools_used: z.number() })),
  subagents: z.array(z.object({ type: z.string(), calls: z.number(), errors: z.number() })),
});
export type ToolsOverview = z.infer<typeof ToolsOverview>;

export const TrendsResponse = z.object({
  granularity: z.string(),
  groupBy: z.string(),
  points: z.array(
    z.object({
      bucket: z.string(),
      groups: z.record(z.object({ cost: z.number(), tokens: z.number(), sessions: z.number() })),
    }),
  ),
});
export type TrendsResponse = z.infer<typeof TrendsResponse>;

export const Alert = z
  .object({
    id: z.number(),
    detector: z.string(),
    severity: z.enum(['info', 'warning', 'critical']),
    title: z.string(),
    message: z.string(),
    metadata: z.record(z.unknown()).default({}),
    created_at: z.string().optional(),
    first_seen_at: z.string().optional(),
    last_seen_at: z.string().optional(),
    seen_at: z.string().nullable().optional(),
  })
  .passthrough();
export type Alert = z.infer<typeof Alert>;
export const AlertList = z.object({ alerts: z.array(Alert) });

export const SubagentSummary = z
  .object({
    id: z.string(),
    model: z.string(),
    status: z.enum(['ok', 'error']),
    turns: z.number(),
    errors: z.number(),
    tokens: z.object({ fresh_input: z.number(), output: z.number(), cache_read: z.number(), cache_write: z.number() }),
    tools: z.array(z.object({ name: z.string(), count: z.number(), errors: z.number() })),
    tool_calls: z.number().optional(),
    total_tokens: z.number().optional(),
    cost_usd: z.number().optional(),
    duration_sec: z.number().nullable().optional(),
    task_given: z.string().nullable().optional(),
  })
  .passthrough();
export type SubagentSummary = z.infer<typeof SubagentSummary>;
export const SubagentList = z.object({ subagents: z.array(SubagentSummary) });

export const SearchResponse = z.object({
  total: z.number(),
  prompt_total: z.number(),
  transcript_total: z.number(),
  results: z.array(
    z.object({
      kind: z.enum(['prompt', 'transcript']),
      role: z.string(),
      timestamp_ms: z.number(),
      project_path: z.string().nullable(),
      text: z.string(),
      snippet: z.string(),
      pasted_chars: z.number(),
      session_id: z.string().nullable(),
    }),
  ),
});
export type SearchResponse = z.infer<typeof SearchResponse>;

export const IngestStatus = z.object({
  foregroundComplete: z.boolean(),
  pending: z.number(),
  processed: z.number(),
  total: z.number(),
  sessionCount: z.number().optional(),
  daemon: z.boolean().optional(),
});
export type IngestStatus = z.infer<typeof IngestStatus>;
export const Pricing = z.object({ version: z.string() }).passthrough();
export const ParseErrors = z.object({
  errors: z.array(z.object({ file: z.string(), reason: z.string(), raw_line_truncated: z.string() })),
});
export const SearchIndexStatus = z.object({
  enabled: z.boolean(),
  segment_count: z.number(),
  indexed_sessions: z.number(),
  backfill: z.object({
    in_progress: z.boolean(),
    processed: z.number(),
    total: z.number(),
    started_at_ms: z.number().nullable(),
    finished_at_ms: z.number().nullable(),
  }),
});
export const Projects = z.object({ projects: z.array(z.string()) });
export const TranscriptSearch = z.object({
  total: z.number(),
  segments: z.array(z.object({ uid: z.string(), timestamp: z.string(), role: z.string(), text: z.string(), snippet: z.string() })),
  search_indexing_enabled: z.boolean().optional(),
});
