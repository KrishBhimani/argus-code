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

it('filters by agent and matches the agent label in text search', () => {
  const t = [mk({ id: 'c', agent: 'claude_code' }), mk({ id: 'x', agent: 'codex', primary_model: 'gpt-5.5' })];
  expect(applyFilters(t, { q: '', project: null, model: null, window: 'all', hasErrors: false, agent: 'codex' }, {}, today).map((x) => x.id)).toEqual(['x']);
  expect(applyFilters(t, { q: 'codex', project: null, model: null, window: 'all', hasErrors: false }, {}, today).map((x) => x.id)).toEqual(['x']);
});
