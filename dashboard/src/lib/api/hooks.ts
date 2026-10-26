import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, tzOffsetMin, type Window } from './client';

// Day-bucketed queries key on the UTC offset too: data bucketed before a DST
// change must not be served for the new offset.
export const useOverview = (w: Window) => useQuery({ queryKey: ['overview', w, tzOffsetMin()], queryFn: () => api.overview(w) });
export const useSessions = () => useQuery({ queryKey: ['sessions'], queryFn: () => api.sessions(), select: (d) => d.sessions });
export const useSession = (id: string) => useQuery({ queryKey: ['session', id], queryFn: () => api.session(id) });
export const useTimeline = (id: string) => useQuery({ queryKey: ['timeline', id], queryFn: () => api.timeline(id) });
export const useSubagents = (id: string) =>
  useQuery({ queryKey: ['subagents', id], queryFn: () => api.subagents(id), select: (d) => d.subagents });
export const useToolsOverview = (w: Window) => useQuery({ queryKey: ['tools', w], queryFn: () => api.toolsOverview(w) });
export const useTrends = (g: 'day' | 'week' | 'month', by: 'model' | 'agent') =>
  useQuery({ queryKey: ['trends', g, by, tzOffsetMin()], queryFn: () => api.trends(g, by) });
// Both alert queries poll together: detectors re-open and escalate alerts in
// place (same id), so a list that never refetches drifts from the badge.
const ALERTS_POLL_MS = 10_000;
export const useAlerts = () =>
  useQuery({ queryKey: ['alerts'], queryFn: () => api.alerts(200), select: (d) => d.alerts, refetchInterval: ALERTS_POLL_MS });
export const useUnseenAlerts = () =>
  useQuery({ queryKey: ['alerts', 'unseen'], queryFn: () => api.unseenAlerts(), select: (d) => d.alerts, refetchInterval: ALERTS_POLL_MS });
export const useMarkAlertSeen = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.markAlertSeen, onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }) });
};
// 4 s while ingesting (progress is visible), 10 s once idle.
export const useIngestStatus = () =>
  useQuery({ queryKey: ['ingest'], queryFn: api.ingestStatus, refetchInterval: (q) => (q.state.data && q.state.data.foregroundComplete && q.state.data.pending === 0 ? 10_000 : 4000) });
export const usePricing = () => useQuery({ queryKey: ['pricing'], queryFn: api.pricing });
export const useSearch = (p: Parameters<typeof api.search>[0], enabled = true) =>
  useQuery({ queryKey: ['search', p], queryFn: () => api.search(p), enabled });
export const usePromptProjects = () => useQuery({ queryKey: ['projects'], queryFn: api.promptProjects, select: (d) => d.projects });
export const useSearchIndexStatus = () =>
  useQuery({ queryKey: ['searchIndex'], queryFn: api.searchIndexStatus, refetchInterval: 4000 });
export const useParseErrors = () => useQuery({ queryKey: ['parseErrors'], queryFn: api.parseErrors, select: (d) => d.errors });
