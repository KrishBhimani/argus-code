import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { tzOffsetMin } from '@/lib/api/client';

const VALIDATE = import.meta.env.DEV || import.meta.env.MODE === 'test';

export const WorkStatus = z.object({ enabled: z.boolean(), last_scan_at: z.string().nullable(), repos: z.number(), errors: z.record(z.string()) });
const Project = z.object({
  id: z.number(), display_name: z.string(), root: z.string(), present: z.boolean(), last_error: z.string().nullable(),
  active_ms_30d: z.number(), active_estimated: z.boolean(), commits_30d: z.number(), daily_active_ms: z.array(z.number()), last_worked_at: z.string().nullable(),
  // A project is every folder holding one repo (clones, worktrees); any folder's id opens it.
  folder_ids: z.array(z.number()), folders: z.array(z.object({ id: z.number(), root: z.string(), present: z.boolean() })),
});
export const WorkProjects = z.object({ projects: z.array(Project) });
const Tiles = z.object({ active_ms: z.number(), active_estimated: z.boolean(), cost: z.number(), commits: z.number(), cost_per_commit: z.number().nullable() });
const Named = z.object({ name: z.string(), value: z.number() });
export const Story = z.object({
  title: z.string(), branch: z.string().nullable(), prs: z.array(z.number()), sessions: z.number(), session_ids: z.array(z.string()),
  active_ms: z.number(), active_estimated: z.boolean(), cost: z.number(), first_ts: z.string(), last_ts: z.string(),
  commits: z.object({ total: z.number(), exact: z.number(), coauthored: z.number(), inferred: z.number() }),
  added: z.number(), deleted: z.number(), files: z.number(), skills: z.array(z.string()), output_tokens: z.number(),
  // Set when the range shows only part of a session: that session's totals end to end.
  whole: z.object({ first_ts: z.string(), last_ts: z.string(), output_tokens: z.number(), cost: z.number() }).nullable(),
});
export const WorkOverview = z.object({
  tiles: Tiles, prior: Tiles,
  daily: z.object({
    days: z.array(z.string()), active_ms: z.array(z.number()), commits: z.array(z.number()),
    output_tokens: z.array(z.number()), cost: z.array(z.number()),
  }),
  stories: z.array(Story),
  breakdowns: z.object({ branches: z.array(Named), skills: z.array(Named), files: z.array(Named) }),
});
const Evidence = z.enum(['exact', 'coauthored', 'inferred']);
const NestedCommit = z.object({ sha: z.string(), evidence: Evidence, added: z.number(), deleted: z.number(), files: z.number(), subject: z.string(), author_name: z.string() });
const SessionItem = z.object({
  kind: z.literal('session'), session_id: z.string(), title: z.string().nullable(), branch: z.string().nullable(), first_ts: z.string(), last_ts: z.string(),
  active_ms: z.number(), active_estimated: z.boolean(), cost: z.number(), turns: z.number(), model: z.string().nullable(), tool_errors: z.number(), commits: z.array(NestedCommit), sort_ts: z.string(),
});
const CommitItem = z.object({
  kind: z.literal('commit'), sha: z.string(), subject: z.string(), author_name: z.string(), authored_at: z.string(),
  evidence: Evidence.nullable(), session_id: z.string().nullable(), added: z.number(), deleted: z.number(), sort_ts: z.string(),
});
export const WorkTimeline = z.object({ days: z.array(z.object({ day: z.string(), items: z.array(z.discriminatedUnion('kind', [SessionItem, CommitItem])) })) });

export type WorkProject = z.infer<typeof Project>;
export type WorkOverviewT = z.infer<typeof WorkOverview>;
export type StoryT = z.infer<typeof Story>;
export type WorkTimelineT = z.infer<typeof WorkTimeline>;
export type Scope = 'mine' | 'all';
export type Range = { from?: string; to?: string; days?: number };

async function get<T>(path: string, schema: z.ZodType<T, z.ZodTypeDef, unknown>): Promise<T> {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  const json = await r.json();
  return VALIDATE ? schema.parse(json) : (json as T);
}
const qs = (p: Record<string, string | number | undefined>) =>
  new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString();

export const workApi = {
  status: () => get('/api/work/status', WorkStatus),
  projects: () => get(`/api/work/projects?${qs({ tz: tzOffsetMin() })}`, WorkProjects),
  overview: (repo: number, r: Range, scope: Scope) => get(`/api/work/projects/${repo}/overview?${qs({ ...r, scope, tz: tzOffsetMin() })}`, WorkOverview),
  timeline: (repo: number, r: Range, f: { kind: string; branch?: string; scope: Scope }) =>
    get(`/api/work/projects/${repo}/timeline?${qs({ ...r, ...f, tz: tzOffsetMin() })}`, WorkTimeline),
};

/** Trial switch: the routes exist only with `argus start --work`, so a 404 means "off". */
export const useWorkStatus = () => {
  const q = useQuery({ queryKey: ['work', 'status'], queryFn: workApi.status, retry: false, staleTime: 60_000 });
  return { enabled: q.data?.enabled === true, data: q.data };
};
export const useWorkProjects = () => useQuery({ queryKey: ['work', 'projects', tzOffsetMin()], queryFn: workApi.projects });
export const useWorkOverview = (repo: number, r: Range, scope: Scope) =>
  useQuery({ queryKey: ['work', 'overview', repo, r.from, r.to, r.days, scope, tzOffsetMin()], queryFn: () => workApi.overview(repo, r, scope) });
export const useWorkTimeline = (repo: number, r: Range, f: { kind: string; branch?: string; scope: Scope }) =>
  useQuery({ queryKey: ['work', 'timeline', repo, r.from, r.to, r.days, f.kind, f.branch, f.scope, tzOffsetMin()], queryFn: () => workApi.timeline(repo, r, f) });
