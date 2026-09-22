import { z } from 'zod';
import * as S from './schemas';

export type Window = '24h' | '7d' | '30d' | 'all';
export const WINDOWS: Window[] = ['24h', '7d', '30d', 'all'];
/** The server names the first window "today". */
export const toServerWindow = (w: Window): string => (w === '24h' ? 'today' : w);
/**
 * Viewer's UTC offset in minutes east (IST = 330). Day-bucketed endpoints take it
 * so the server's day keys are the viewer's local dates — the same keys
 * `dayKeys()` builds. Read per request so a DST change mid-session is honoured.
 */
export const tzOffsetMin = (): number => -new Date().getTimezoneOffset();

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Schemas are enforced in dev and tests so a backend shape drift fails loudly; production
// trusts the same-origin server and skips the parse (a 350 KB timeline costs real main-thread time).
const VALIDATE = import.meta.env.DEV || import.meta.env.MODE === 'test';
const decode = <T,>(schema: z.ZodType<T, z.ZodTypeDef, unknown>, json: unknown): T => (VALIDATE ? schema.parse(json) : (json as T));

async function get<T>(path: string, schema: z.ZodType<T, z.ZodTypeDef, unknown>): Promise<T> {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!r.ok) throw new ApiError(r.status, `${path} → ${r.status}`);
  return decode(schema, await r.json());
}
async function post<T>(path: string, schema: z.ZodType<T, z.ZodTypeDef, unknown>): Promise<T> {
  const r = await fetch(path, { method: 'POST', headers: { Accept: 'application/json' } });
  if (!r.ok) throw new ApiError(r.status, `${path} → ${r.status}`);
  return decode(schema, await r.json());
}
const enc = encodeURIComponent;

export const api = {
  overview: (w: Window) => get(`/api/overview?window=${toServerWindow(w)}&tz=${tzOffsetMin()}`, S.Overview),
  sessions: (limit = 100_000) => get(`/api/sessions?limit=${limit}`, S.SessionList),
  session: (id: string) => get(`/api/sessions/${enc(id)}`, S.SessionDetail),
  timeline: (id: string) => get(`/api/sessions/${enc(id)}/timeline`, S.Timeline),
  toolOutput: (id: string, toolUseId: string) => get(`/api/sessions/${enc(id)}/tool-output/${enc(toolUseId)}`, S.ToolOutput),
  subagents: (id: string) => get(`/api/sessions/${enc(id)}/subagents`, S.SubagentList),
  sessionTranscriptSearch: (id: string, q: string, limit = 100) =>
    get(`/api/sessions/${enc(id)}/transcript?q=${enc(q)}&limit=${limit}`, S.TranscriptSearch),
  toolsOverview: (w: Window) => get(`/api/tools/overview?window=${toServerWindow(w)}`, S.ToolsOverview),
  trends: (granularity: 'day' | 'week' | 'month', groupBy: 'model' | 'agent') =>
    get(`/api/trends?granularity=${granularity}&groupBy=${groupBy}&tz=${tzOffsetMin()}`, S.TrendsResponse),
  alerts: (limit = 50) => get(`/api/alerts?limit=${limit}`, S.AlertList),
  unseenAlerts: () => get('/api/alerts/unseen', S.AlertList),
  markAlertSeen: (id: number) => post(`/api/alerts/${id}/seen`, z.unknown()),
  search: (p: { q?: string; limit?: number; project?: string; includeSlash?: boolean; roles?: string[] }) => {
    const sp = new URLSearchParams();
    if (p.q) sp.set('q', p.q);
    if (p.limit) sp.set('limit', String(p.limit));
    if (p.project) sp.set('project', p.project);
    if (p.includeSlash) sp.set('include_slash', '1');
    if (p.roles?.length) sp.set('roles', p.roles.join(','));
    return get(`/api/search?${sp}`, S.SearchResponse);
  },
  promptProjects: () => get('/api/prompts/projects', S.Projects),
  searchIndexStatus: () => get('/api/search-index/status', S.SearchIndexStatus),
  searchIndexEnable: () => post('/api/search-index/enable', z.unknown()),
  searchIndexDisable: () => post('/api/search-index/disable', z.unknown()),
  searchIndexClear: () => post('/api/search-index/clear', z.unknown()),
  ingestStatus: () => get('/api/ingest/status', S.IngestStatus),
  pricing: () => get('/api/pricing', S.Pricing),
  parseErrors: () => get('/api/parse-errors', S.ParseErrors),
};

export type {
  Session, Overview, TimelineTurn, ToolsOverview, TrendsResponse, Alert, SubagentSummary, SearchResponse, IngestStatus,
} from './schemas';
