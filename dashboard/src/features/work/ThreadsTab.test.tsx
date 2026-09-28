import { render, screen, fireEvent, within } from '@testing-library/react';
import ThreadsTab from './ThreadsTab';

const story = (o: object) => ({
  key: 'k', title: 't', branch: 'main', prs: [] as number[], sessions: 1, session_ids: ['k'], active_ms: 0, active_estimated: false, cost: 10,
  first_ts: '2026-09-16T10:00:00Z', last_ts: '2026-09-16T12:00:00Z', commits: { total: 0, exact: 0, coauthored: 0, inferred: 0 },
  added: 0, deleted: 0, files: 0, skills: [] as string[], output_tokens: 1000, whole: null, remote_as_of: null,
  state: 'shipped', reason: '1 commit on main', session_list: [] as object[], commit_list: [] as object[], ...o,
});
const base = {
  tiles: { active_ms: 0, active_estimated: false, cost: 120, commits: 3, cost_per_commit: 40 },
  prior: { active_ms: 0, active_estimated: false, cost: 60, commits: 1, cost_per_commit: 60 },
  daily: { days: ['2026-09-15', '2026-09-16'], active_ms: [0, 0], commits: [1, 2], output_tokens: [10, 20], cost: [1, 2] },
  stories: [
    story({ key: 'claude_code:open', title: 'Project sharing redesign', state: 'pr_open', reason: 'PR #212 open', first_ts: '2026-09-28T09:00:00Z', last_ts: '2026-09-28T10:00:00Z',
      session_ids: ['claude_code:open'],
      session_list: [{ session_id: 'claude_code:open', title: 'Project sharing redesign', model: 'claude-opus-5-5', turns: 51,
        first_ts: '2026-09-28T09:00:00Z', last_ts: '2026-09-28T10:00:00Z', active_ms: 600_000, active_estimated: false }] }),
    story({ key: 'claude_code:ship1', title: 'grokbot integration' }),
    story({ key: 'claude_code:ship2', title: 'composio async fix' }),
    story({ key: 'claude_code:drop', title: 'fastapi import error', state: 'dropped', reason: 'no commit · $0.44 spent', cost: 0.44 }),
  ],
  breakdowns: { branches: [], skills: [], files: [] },
};
// A range query would return stories clipped to the range, with states re-derived from the clipped
// commits: the tab must not use them for thread states.
const clipped = { ...base, stories: base.stories.map((s) => ({ ...s, state: 'dropped', reason: 'no commit' })) };
vi.mock('./api', () => ({
  useWorkOverview: (_r: number, range: { from?: string }) => ({ data: range.from ? clipped : base, error: null }),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: (p: { children: React.ReactNode; to: string; params?: unknown }) => <a data-to={p.to} data-params={JSON.stringify(p.params)}>{p.children}</a>,
}));

const column = (name: string) => within(screen.getByRole('region', { name }));

it('sorts threads into Open, Shipped and Dropped, each with its reason', () => {
  render(<ThreadsTab repo={1} />);
  expect(column('Open').getByText('Project sharing redesign')).toBeInTheDocument();
  expect(column('Open').getByText('PR OPEN')).toBeInTheDocument();
  expect(column('Open').getByText(/PR #212 open/)).toBeInTheDocument();
  expect(column('Shipped').getAllByText('SHIPPED')).toHaveLength(2);
});

it('keeps Dropped folded to one line until asked', () => {
  render(<ThreadsTab repo={1} />);
  expect(screen.queryByText('fastapi import error')).not.toBeInTheDocument();
  fireEvent.click(column('Dropped').getByRole('button', { name: /1 dropped/ }));
  expect(screen.getByText('fastapi import error')).toBeInTheDocument();
});

it('leads with outcomes: shipped, open, dropped, spend per shipped', () => {
  render(<ThreadsTab repo={1} />);
  const strip = within(screen.getByRole('group', { name: 'Outcomes' }));
  expect(strip.getByText('Per shipped').nextSibling).toHaveTextContent('$60.00');   // $120 ÷ 2 shipped
  expect(strip.getByText('Shipped').nextSibling).toHaveTextContent('2');
});

it('opens the focused thread and lists its sessions', () => {
  render(<ThreadsTab repo={1} focus="claude_code:open" />);
  const row = screen.getAllByText('Project sharing redesign', { selector: 'b' })[0].closest('[data-focused]')!;   // [1] is its session, shown open
  expect(row).toHaveAttribute('data-focused', 'true');
  expect(JSON.parse(within(row as HTMLElement).getByText('Open session').getAttribute('data-params')!)).toEqual({ id: 'claude_code:open' });
});

it('keeps the daily chart one click away', () => {
  render(<ThreadsTab repo={1} />);
  expect(screen.queryByRole('img', { name: 'Output tokens by day' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Daily output/ }));
  expect(screen.getByRole('img', { name: 'Output tokens by day' })).toBeInTheDocument();
});

it('brushing narrows the threads without changing their states', () => {
  render(<ThreadsTab repo={1} />);
  fireEvent.click(screen.getByRole('button', { name: /Daily output/ }));
  const cols = screen.getAllByTestId('daycol');
  fireEvent.pointerDown(cols[1]); fireEvent.pointerUp(cols[1]);            // 2026-09-16 only
  expect(column('Shipped').getByText('grokbot integration')).toBeInTheDocument();
  expect(column('Shipped').getAllByText('SHIPPED')).toHaveLength(2);
  expect(screen.queryByText('Project sharing redesign')).not.toBeInTheDocument();   // last active Sep 28
});
