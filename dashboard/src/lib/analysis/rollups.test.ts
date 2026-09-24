import { byProject, perMTokens, topNPlusOther, inWindow, sessionTokens } from './rollups';
import { mk } from './testutil';

it('byProject aggregates and sorts by tokens', () => {
  const r = byProject([
    mk({ project_path: 'a', total_output_tokens: 5, total_cost_usd: 1 }),
    mk({ project_path: 'b', total_output_tokens: 9 }),
    mk({ project_path: 'a', total_output_tokens: 1 }),
  ]);
  expect(r[0]).toEqual({ project: 'b', sessions: 1, tokens: 9, cost: 0 });
  expect(r[1]).toEqual({ project: 'a', sessions: 2, tokens: 6, cost: 1 });
});

it('perMTokens', () => {
  expect(perMTokens(5, 2_000_000)).toBe(2.5);
  expect(perMTokens(5, 0)).toBeNull();
});

it('topNPlusOther folds the tail', () => {
  const rows = [['a', 10], ['b', 5], ['c', 2], ['d', 1]] as const;
  expect(topNPlusOther(rows, 2, (r) => r[0], (r) => r[1])).toEqual([
    { name: 'a', value: 10 },
    { name: 'b', value: 5 },
    { name: 'Other', value: 3 },
  ]);
});

it('inWindow keeps sessions started within the window', () => {
  const today = new Date('2026-08-21T12:00:00');
  const s = [mk({ started_at: '2026-08-20T01:00:00' }), mk({ started_at: '2026-08-01T01:00:00' })];
  expect(inWindow(s, '7d', today).length).toBe(1);
  expect(inWindow(s, 'all', today).length).toBe(2);
});

it('sessionTokens sums four fields', () =>
  expect(sessionTokens(mk({ total_fresh_input_tokens: 1, total_output_tokens: 2, total_cache_read_tokens: 3, total_cache_write_tokens: 4 }))).toBe(10));
