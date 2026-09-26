import { render, screen, fireEvent, within } from '@testing-library/react';
import OverviewTab from './OverviewTab';

type Whole = null | { first_ts: string; last_ts: string; output_tokens: number; cost: number };
const story = (o: object) => ({
  title: 't', branch: 'main', prs: [] as number[], sessions: 1, session_ids: ['s1'], active_ms: 7_200_000, active_estimated: false, cost: 64,
  first_ts: '2026-09-16T10:00:00Z', last_ts: '2026-09-16T12:00:00Z', commits: { total: 0, exact: 0, coauthored: 0, inferred: 0 },
  added: 0, deleted: 0, files: 0, skills: [] as string[], output_tokens: 290_757, whole: null as Whole,
  session_list: [] as object[], commit_list: [] as object[], ...o,
});
const fix = story({
  title: 'fix-severe-issues', prs: [26, 27, 49], session_ids: ['claude_code:a', 'claude_code:b'], sessions: 2,
  first_ts: '2026-09-22T18:11:00Z', last_ts: '2026-09-25T18:18:00Z', commits: { total: 2, exact: 1, coauthored: 0, inferred: 1 },
  added: 5024, deleted: 974, files: 12, output_tokens: 622_442, cost: 95.78,
  session_list: [
    { session_id: 'claude_code:a', title: 'fix-severe-issues', model: 'claude-opus-5-5', turns: 814, first_ts: '2026-09-22T18:11:00Z', last_ts: '2026-09-25T18:18:00Z', active_ms: 14_861_486, active_estimated: true },
    { session_id: 'claude_code:b', title: 'follow-up', model: 'claude-opus-5-5', turns: 12, first_ts: '2026-09-25T10:00:00Z', last_ts: '2026-09-25T11:00:00Z', active_ms: 600_000, active_estimated: false },
  ],
  commit_list: [
    { sha: '4d72650aa', subject: 'feat(detectors): implement cost_spike (#26)', evidence: 'exact' },
    { sha: 'b890b93bb', subject: 'feat(work): trial work.db', evidence: 'inferred' },
  ],
});
const docs = story({ title: 'Docs review', branch: 'development', session_ids: ['claude_code:d'], last_ts: '2026-09-10T10:00:00Z', first_ts: '2026-09-10T09:00:00Z' });
const base = {
  tiles: { active_ms: 23_581_165, active_estimated: true, cost: 268.17, commits: 51, cost_per_commit: 5.26 },
  prior: { active_ms: 53_591_910, active_estimated: true, cost: 529.74, commits: 67, cost_per_commit: 7.9 },
  daily: { days: ['2026-09-15', '2026-09-16'], active_ms: [3_600_000, 7_200_000], commits: [2, 4], output_tokens: [120_000, 450_000], cost: [12, 30] },
  stories: [docs, fix],
  breakdowns: { branches: [{ name: 'main', value: 214 }, { name: 'development', value: 55 }], skills: [], files: [] },
};
const loading = { on: false };
vi.mock('./api', () => ({
  // The second call is the focused (brushed) query; `loading.on` makes it still in flight.
  useWorkOverview: (_r: number, range: { from?: string }) =>
    loading.on && range.from ? { data: undefined, isFetching: true, isLoading: true, error: null } : { data: base, isFetching: false, isLoading: false, error: null },
}));
vi.mock('@tanstack/react-router', () => ({
  Link: (p: { children: React.ReactNode; search?: unknown; params?: unknown; to: string }) =>
    <a data-to={p.to} data-search={JSON.stringify(p.search)} data-params={JSON.stringify(p.params)}>{p.children}</a>,
}));

it('starts with where you left off: the latest piece of work', () => {
  render(<OverviewTab repo={1} />);
  const card = screen.getByText('WHERE YOU LEFT OFF').parentElement!.parentElement!;
  expect(within(card).getByText('fix-severe-issues')).toBeInTheDocument();
  expect(within(card).getByText(/2 commits · 3 PRs, latest #49/)).toBeInTheDocument();
  expect(JSON.parse(within(card).getByText('Open last session').getAttribute('data-params')!)).toEqual({ id: 'claude_code:b' });
  expect(JSON.parse(within(card).getByText('See it in Activity').getAttribute('data-search')!)).toEqual({ tab: 'activity', focus: 'claude_code:a,claude_code:b' });
});

it('shows four plain numbers with the change against the previous period', () => {
  render(<OverviewTab repo={1} />);
  expect(screen.getByText('Claude output')).toBeInTheDocument();
  expect(screen.getByText('570.0k')).toBeInTheDocument();
  expect(screen.getByText('↓ 49% vs previous 30 days')).toBeInTheDocument();   // cost
  expect(screen.getByText('↓ 24% vs previous 30 days')).toBeInTheDocument();   // commits
  expect(screen.getByText('≈6.6h')).toBeInTheDocument();
});

it('all time shows no changes (there is no earlier period)', () => {
  render(<OverviewTab repo={1} days={0} />);
  expect(screen.queryByText(/vs previous/)).not.toBeInTheDocument();
});

it('switches the chart between output tokens (default), cost and time', () => {
  render(<OverviewTab repo={1} />);
  expect(screen.getByRole('tab', { name: 'Tokens' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByLabelText('Output tokens by day')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'Cost' }));
  expect(screen.getByLabelText('Cost by day')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'Time' }));
  expect(screen.getByLabelText('Active hours by day')).toBeInTheDocument();
  expect(screen.getByLabelText('Commits by day')).toBeInTheDocument();
});

it('lists the work newest first; a row expands to its sessions and commits in plain words', () => {
  render(<OverviewTab repo={1} />);
  expect(screen.getByText('2 pieces of work')).toBeInTheDocument();
  const row = screen.getByRole('button', { name: /fix-severe-issues/ });
  expect(row).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByText('feat(work): trial work.db')).not.toBeInTheDocument();
  fireEvent.click(row);
  expect(row).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('feat(work): trial work.db')).toBeInTheDocument();
  expect(screen.getByText('made here')).toBeInTheDocument();
  expect(screen.getByText('likely')).toBeInTheDocument();
  expect(screen.getByText(/814 turns/)).toBeInTheDocument();
  expect(screen.getByText('PRs · #26 #27 #49')).toBeInTheDocument();
});

it('branch chips filter the work', () => {
  render(<OverviewTab repo={1} />);
  fireEvent.click(screen.getByRole('button', { name: 'main · $214' }));
  expect(screen.queryByRole('button', { name: /Docs review/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'All branches' }));
  expect(screen.getByRole('button', { name: /Docs review/ })).toBeInTheDocument();
});

it('an expanded slice of a longer session says so, with the whole totals', () => {
  const saved = base.stories;
  base.stories = [{ ...fix, whole: { first_ts: '2026-09-03T15:24:45Z', last_ts: '2026-09-17T23:32:06Z', output_tokens: 947_908, cost: 430.67 } }];
  render(<OverviewTab repo={1} />);
  fireEvent.click(screen.getByRole('button', { name: /fix-severe-issues/ }));
  expect(screen.getByText(/part of a longer session/)).toHaveTextContent('947.9k tokens');
  expect(screen.getByText(/part of a longer session/)).toHaveTextContent('$431');
  base.stories = saved;
});

it('while a brushed range loads, it says so instead of "No work in this range"', () => {
  loading.on = true;
  render(<OverviewTab repo={1} />);
  const cols = screen.getAllByTestId('daycol');
  fireEvent.pointerDown(cols[0]); fireEvent.pointerUp(cols[0]);
  expect(screen.queryByText('No work in this range')).not.toBeInTheDocument();
  expect(screen.getByText(/Loading/)).toBeInTheDocument();
  loading.on = false;
});
