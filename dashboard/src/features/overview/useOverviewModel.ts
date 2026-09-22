import { useMemo } from 'react';
import type { Overview, Session, Window } from '@/lib/api/client';
import { useOverview, useSessions, useToolsOverview } from '@/lib/api/hooks';
import { widerWindowFor, windowDays } from '@/lib/analysis/windows';
import { delta, type Delta } from '@/lib/analysis/deltas';
import { inWindow } from '@/lib/analysis/rollups';

type Tools = { total_calls: number; total_errors: number } | undefined;
export type Tiles = { tokens: Delta | null; cost: Delta | null; sessions: Delta | null; errorRate: Delta | null };
const NONE: Tiles = { tokens: null, cost: null, sessions: null, errorRate: null };

/**
 * Deltas vs the previous equal-length window. The prior totals come from the
 * server (`prior_window`: `[now - 2n, now - n)`, same definitions as the
 * current window), so nothing is counted in both windows.
 */
export function overviewTiles(cur: Overview, w: Window, toolsCur: Tools, toolsWider: Tools): Tiles {
  if (windowDays(w) == null || !cur.prior_window) return NONE;
  const prior = cur.prior_window;
  let errorRate: Delta | null = null;
  if (toolsCur && toolsWider) {
    const curRate = toolsCur.total_calls ? toolsCur.total_errors / toolsCur.total_calls : 0;
    const pc = toolsWider.total_calls - toolsCur.total_calls;
    const pe = toolsWider.total_errors - toolsCur.total_errors;
    // Prior rate = (wider − current): a longer span than the current window, but a ratio, so comparable.
    const prevRate = pc > 0 ? pe / pc : null;
    errorRate =
      prevRate == null
        ? null
        : { abs: curRate - prevRate, pct: prevRate ? (curRate - prevRate) / prevRate : null, dir: curRate > prevRate ? 'up' : curRate < prevRate ? 'down' : 'flat' };
  }
  return { tokens: delta(cur.total_tokens, prior.tokens), cost: delta(cur.total_cost_usd, prior.cost_usd), sessions: delta(cur.session_count, prior.sessions), errorRate };
}

export function useOverviewModel(w: Window, today = new Date()) {
  const cur = useOverview(w);
  const tools = useToolsOverview(w);
  const toolsWider = useToolsOverview(widerWindowFor(w));
  const sessions = useSessions();
  const tiles = useMemo(
    () => (cur.data ? overviewTiles(cur.data, w, tools.data, toolsWider.data) : NONE),
    [cur.data, tools.data, toolsWider.data, w],
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const sessionsInWindow = useMemo(() => (sessions.data ? inWindow(sessions.data, w, today) : []), [sessions.data, w]);
  return {
    cur: cur.data,
    tiles,
    tools: tools.data,
    sessionsInWindow,
    allSessions: sessions.data ?? ([] as Session[]),
    isLoading: cur.isLoading,
    error: cur.error ?? tools.error ?? sessions.error,
  };
}
