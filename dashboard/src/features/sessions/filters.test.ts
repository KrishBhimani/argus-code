import { applyFilters } from './filters';
import { mk } from '@/lib/analysis/testutil';

const today = new Date('2026-08-21T12:00:00');
const s = [
  mk({ id: '1', project_path: 'c:/a/argus', primary_model: 'claude-opus-4-8', started_at: '2026-08-20T01:00:00' }),
  mk({ id: '2', project_path: 'c:/b/other', primary_model: 'claude-sonnet-4-5', started_at: '2026-07-01T01:00:00' }),
];

it('filters by text, model, window and errors', () => {
  expect(applyFilters(s, { q: 'argus', project: null, model: null, window: 'all', hasErrors: false }, {}, today).map((x) => x.id)).toEqual(['1']);
  expect(applyFilters(s, { q: '', project: null, model: 'claude-sonnet-4-5', window: 'all', hasErrors: false }, {}, today).map((x) => x.id)).toEqual(['2']);
  expect(applyFilters(s, { q: '', project: null, model: null, window: '7d', hasErrors: false }, {}, today).map((x) => x.id)).toEqual(['1']);
  expect(applyFilters(s, { q: '', project: null, model: null, window: 'all', hasErrors: true }, { '2': 3 }, today).map((x) => x.id)).toEqual(['2']);
});

it('filters by agent when set and ignores it when absent', () => {
  const both = [mk({ id: 'claude_code:a' }), mk({ id: 'codex:b', agent: 'codex' })];
  const base = { q: '', project: null, model: null, window: 'all' as const, hasErrors: false };
  expect(applyFilters(both, { ...base, agent: 'codex' }, {}, today).map((x) => x.id)).toEqual(['codex:b']);
  expect(applyFilters(both, base, {}, today)).toHaveLength(2);
});
