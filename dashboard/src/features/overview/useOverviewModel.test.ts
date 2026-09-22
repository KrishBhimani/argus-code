import type { Overview } from '@/lib/api/client';
import { overviewTiles } from './useOverviewModel';

const ov = (tokens: number, cost: number, sessions: number, prior: Overview['prior_window']): Overview => ({
  window: '7d',
  total_cost_usd: cost,
  total_tokens: tokens,
  session_count: sessions,
  agent_split: {},
  cost_by_day: {},
  cost_by_model: {},
  tokens_by_day: {},
  tokens_by_model: {},
  top_sessions: [],
  prior_window: prior,
});

it('computes deltas against the server-computed prior window', () => {
  // REGRESSION (H8): the prior value used to be summed from whole calendar
  // days of a wider response, which overlapped the rolling current window,
  // and "sessions" compared sessions-with-turns against sessions-started.
  const cur = ov(15, 1, 2, { tokens: 10, cost_usd: 2, sessions: 1 });
  const t = overviewTiles(cur, '24h', { total_calls: 10, total_errors: 1 }, { total_calls: 30, total_errors: 2 });
  expect(t.tokens).toEqual({ abs: 5, pct: 0.5, dir: 'up' });
  expect(t.cost?.dir).toBe('down');
  expect(t.sessions).toEqual({ abs: 1, pct: 1, dir: 'up' });
  expect(t.errorRate?.dir).toBe('up'); // 10% now vs 5% before (1/20)
});

it('returns null deltas for the all window', () => {
  const cur = ov(10, 1, 1, null);
  expect(overviewTiles(cur, 'all', undefined, undefined)).toEqual({ tokens: null, cost: null, sessions: null, errorRate: null });
});
