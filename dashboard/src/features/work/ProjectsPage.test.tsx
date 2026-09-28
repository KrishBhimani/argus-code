import { render, screen, fireEvent, within } from '@testing-library/react';
import ProjectsPage from './ProjectsPage';
import { hours } from './fmt';

const nav = vi.fn();
const asked: number[] = [];
const state = { open: [] as object[] };
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => nav,
  Link: (p: { children: React.ReactNode; to: string; search?: unknown; params?: unknown }) =>
    <a data-to={p.to} data-search={JSON.stringify(p.search)} data-params={JSON.stringify(p.params)}>{p.children}</a>,
}));
vi.mock('@/app/shell/TopBar', () => ({
  TopBar: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Page: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
const P = (o: object) => ({
  id: 1, display_name: 'p', root: '/p', present: true, last_error: null, active_ms: 0, active_estimated: false, tokens: 0, cost: 0,
  commits: 0, daily_tokens: [0, 0], last_worked_at: null, latest: null, folder_ids: [1], folders: [{ id: 1, root: '/p', present: true }],
  outcomes: { shipped: 0, open: 0, dropped: 0 }, cost_per_shipped: null, claude_share: null, ...o,
});
const T = (o: object) => ({
  key: 'claude_code:k', project_id: 2, project_name: 'argus-code', title: 'Work/Projects redesign', branch: 'feat/work-threads-ui',
  state: 'uncommitted', reason: 'folder has 12 changed files', first_ts: '2026-09-29T08:00:00Z', last_ts: new Date().toISOString(),
  cost: 41, output_tokens: 1_500_000, commits: 0, prs: [], session_ids: ['claude_code:k', 'claude_code:last'], remote_as_of: null, ...o,
});
vi.mock('./api', () => ({
  useWorkProjects: (days: number) => {
    asked.push(days);
    return { data: { projects: [
      P({ id: 2, display_name: 'argus-code', tokens: 1_413_735, cost: 279, commits: 51, last_worked_at: new Date().toISOString(),
        outcomes: { shipped: 9, open: 1, dropped: 2 }, cost_per_shipped: 31, claude_share: 0.71 }),
      P({ id: 3, display_name: 'portfolio', present: false, last_worked_at: '2026-07-31T00:00:00Z' }),
    ] }, isLoading: false, error: null };
  },
  useWorkThreads: () => ({ data: { open: state.open, counts: { shipped: 6, open: state.open.length, dropped: 2 }, remote_as_of: null }, error: null }),
}));

beforeEach(() => { state.open = [T({})]; nav.mockClear(); });

it('leads with open threads and how to resume them', () => {
  render(<ProjectsPage />);
  expect(screen.getByText('1 thread open across 1 project')).toBeInTheDocument();
  const card = within(screen.getByRole('article'));
  expect(card.getByText('UNCOMMITTED')).toBeInTheDocument();
  expect(card.getByText('folder has 12 changed files')).toBeInTheDocument();
  expect(JSON.parse(card.getByText('Resume').getAttribute('data-search')!)).toEqual({ tab: 'threads', focus: 'claude_code:k' });
  expect(JSON.parse(card.getByText('Open last session').getAttribute('data-params')!)).toEqual({ id: 'claude_code:last' });
});

it('says so when nothing is open', () => {
  state.open = [];
  render(<ProjectsPage />);
  expect(screen.getAllByText('Nothing open').length).toBeGreaterThan(0);
  expect(screen.getByText('Everything has shipped or gone quiet.')).toBeInTheDocument();
});

it('shows eight threads, then the rest on demand', () => {
  state.open = Array.from({ length: 10 }, (_, i) => T({ key: `k${i}`, title: `thread ${i}` }));
  render(<ProjectsPage />);
  expect(screen.getAllByRole('article')).toHaveLength(8);
  fireEvent.click(screen.getByRole('button', { name: 'Show all 10' }));
  expect(screen.getAllByRole('article')).toHaveLength(10);
});

it('shows each project as outcomes, not raw output', () => {
  render(<ProjectsPage />);
  const card = within(screen.getByRole('button', { name: /argus-code/ }));
  expect(card.getByText('9 shipped · 1 open · 2 dropped')).toBeInTheDocument();
  expect(card.getByText('$31.00')).toBeInTheDocument();
  expect(card.getByText('71%')).toBeInTheDocument();
});

it('folds quiet projects and opens a project on its Threads tab', () => {
  render(<ProjectsPage />);
  expect(screen.queryByText('portfolio')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /1 quiet project/ }));
  expect(screen.getByText('portfolio')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /argus-code/ }));
  expect(nav).toHaveBeenCalledWith({ to: '/projects/$repo', params: { repo: '2' }, search: { tab: 'threads' } });
});

it('the period chips change the period', () => {
  render(<ProjectsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'All time' }));
  expect(asked.at(-1)).toBe(0);
});

it('formats hours', () => {
  expect(hours(68_400_000)).toBe('19h');
  expect(hours(720_000, true)).toBe('≈12m');
});
