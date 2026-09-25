import { render, screen, fireEvent } from '@testing-library/react';
import OverviewTab from './OverviewTab';

const story = (title: string, branch: string, skills: string[] = [], total = 4) => ({
  title, branch, prs: [212], sessions: 2, session_ids: [title], active_ms: 7_200_000, cost: 64, first_ts: '2026-09-16T10:00:00Z', last_ts: '2026-09-16T12:00:00Z',
  commits: total ? { total, exact: 3, coauthored: 0, inferred: 1 } : { total: 0, exact: 0, coauthored: 0, inferred: 0 },
  added: 96, deleted: 41, files: 5, skills, output_tokens: 290_757,
});
const base = {
  tiles: { active_ms: 147_600_000, cost: 1240, commits: 86, cost_per_commit: 14.4 }, prior: { active_ms: 1, cost: 1050, commits: 60, cost_per_commit: 17.5 },
  daily: { days: ['2026-09-15', '2026-09-16'], active_ms: [3_600_000, 7_200_000], commits: [2, 4], output_tokens: [120_000, 450_000], cost: [12, 30] },
  stories: [story('Fix blocking calls', 'fix/blocking', ['superpowers:brainstorming']), story('Docs review', 'development', [], 0)],
  breakdowns: { branches: [{ name: 'fix/blocking', value: 64 }], skills: [{ name: 'superpowers:brainstorming', value: 0.3 }], files: [{ name: 'api/x.py', value: 3 }] },
};
const loading = { on: false };
vi.mock('./api', () => ({
  // The second call is the focused (brushed) query; `loading.on` makes it still in flight.
  useWorkOverview: (_r: number, range: { from?: string }) =>
    loading.on && range.from ? { data: undefined, isFetching: true, isLoading: true, error: null } : { data: base, isFetching: false, isLoading: false, error: null },
}));
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn(), Link: (p: { children: React.ReactNode }) => <a>{p.children}</a> }));

it('shows tiles, both day charts, stories and the rail', () => {
  render(<OverviewTab repo={1} />);
  expect(screen.getByText('41h')).toBeInTheDocument();
  expect(screen.getByLabelText('Output tokens by day')).toBeInTheDocument();
  expect(screen.getByLabelText('Commits by day')).toBeInTheDocument();
  expect(screen.getByText('Fix blocking calls')).toBeInTheDocument();
  expect(screen.getByText('4 commits')).toBeInTheDocument();
  expect(screen.getByText('(3 exact · 1 inferred)')).toBeInTheDocument();
  expect(screen.getByText('no commits: research')).toBeInTheDocument();
});

it('clicking a branch filters the stories; the chip clears it', () => {
  render(<OverviewTab repo={1} />);
  fireEvent.click(screen.getByRole('button', { name: 'fix/blocking' }));
  expect(screen.queryByText('Docs review')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /branch: fix\/blocking/ }));
  expect(screen.getByText('Docs review')).toBeInTheDocument();
});

it('marks estimated active time with ≈ (spec §5)', () => {
  const saved = base.tiles;
  base.tiles = { ...base.tiles, active_estimated: true } as typeof base.tiles;
  render(<OverviewTab repo={1} />);
  expect(screen.getByText('≈41h')).toBeInTheDocument();
  base.tiles = saved;
});

it('labels both day charts in plain words with their peak, and names the period', () => {
  render(<OverviewTab repo={1} days={90} />);
  expect(screen.getByText("Claude's output (tokens)")).toBeInTheDocument();
  expect(screen.getByText('peak 450.0k')).toBeInTheDocument();
  expect(screen.getByText('Commits landed')).toBeInTheDocument();
  expect(screen.getByText('peak 4')).toBeInTheDocument();
  expect(screen.getByText(/WORK · LAST 90 DAYS/)).toBeInTheDocument();
});

it('shows no vs-prior deltas for all time (there is no earlier period)', () => {
  const tiles = (days?: number) => {
    const { container, unmount } = render(<OverviewTab repo={1} days={days} />);
    const text = container.querySelector('.grid-cols-4')!.textContent!;
    unmount();
    return text;
  };
  expect(tiles(30)).toMatch(/%/);
  expect(tiles(0)).not.toMatch(/%/);
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

it('switches the top chart between output tokens (default), cost and working time', () => {
  render(<OverviewTab repo={1} />);
  expect(screen.getByRole('tab', { name: 'Tokens' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByLabelText('Output tokens by day')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'Cost' }));
  expect(screen.getByText('Cost ($)')).toBeInTheDocument();
  expect(screen.getByLabelText('Cost by day')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'Time' }));
  expect(screen.getByText("Claude's working time")).toBeInTheDocument();
  expect(screen.getByText('peak 2h')).toBeInTheDocument();
});

it('shows the output tokens of each story', () => {
  render(<OverviewTab repo={1} />);
  expect(screen.getAllByText(/290\.8k tokens/).length).toBeGreaterThan(0);
});
