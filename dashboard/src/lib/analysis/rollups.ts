import type { Session, Window } from '@/lib/api/client';
import { windowDays } from './windows';

export const sessionTokens = (s: Session) =>
  s.total_fresh_input_tokens + s.total_output_tokens + s.total_cache_read_tokens + s.total_cache_write_tokens;

// candidate-endpoint: per-project rollup for a window.
export function byProject(sessions: Session[]) {
  const m = new Map<string, { project: string; sessions: number; tokens: number; cost: number }>();
  for (const s of sessions) {
    const r = m.get(s.project_path) ?? { project: s.project_path, sessions: 0, tokens: 0, cost: 0 };
    r.sessions++;
    r.tokens += sessionTokens(s);
    r.cost += s.total_cost_usd;
    m.set(s.project_path, r);
  }
  return [...m.values()].sort((a, b) => b.tokens - a.tokens);
}

export const perMTokens = (cost: number, tokens: number): number | null => (tokens > 0 ? cost / (tokens / 1e6) : null);

export function topNPlusOther<T>(rows: readonly T[], n: number, key: (t: T) => string, value: (t: T) => number) {
  const sorted = [...rows].sort((a, b) => value(b) - value(a));
  const head = sorted.slice(0, n).map((r) => ({ name: key(r), value: value(r) }));
  const rest = sorted.slice(n).reduce((a, r) => a + value(r), 0);
  return rest > 0 ? [...head, { name: 'Other', value: rest }] : head;
}

/** Sessions whose start date falls inside the window (local calendar days). */
export function inWindow(sessions: Session[], w: Window, today: Date): Session[] {
  const n = windowDays(w);
  if (n == null) return sessions;
  const start = new Date(today);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - (n - 1));
  return sessions.filter((s) => new Date(s.started_at) >= start);
}
