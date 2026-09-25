import { render, screen, fireEvent, within } from '@testing-library/react';
import ProjectsPage from './ProjectsPage';
import { hours } from './fmt';

const nav = vi.fn();
const asked: number[] = [];
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => nav, Link: (p: { children: React.ReactNode }) => <a>{p.children}</a> }));
vi.mock('@/app/shell/TopBar', () => ({
  TopBar: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Page: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
const P = (o: object) => ({
  id: 1, display_name: 'p', root: '/p', present: true, last_error: null, active_ms: 0, active_estimated: false, tokens: 0, cost: 0,
  commits: 0, daily_tokens: [0, 0], last_worked_at: null, latest: null, folder_ids: [1], folders: [{ id: 1, root: '/p', present: true }], ...o,
});
vi.mock('./api', () => ({
  useWorkProjects: (days: number) => {
    asked.push(days);
    return { data: { projects: [
      P({ id: 2, display_name: 'argus-code', tokens: 1_413_735, cost: 268.17, commits: 51, daily_tokens: [0, 5, 9], last_worked_at: new Date().toISOString(),
        latest: { title: 'fix-severe-issues', branch: 'main', session_id: 'claude_code:x' },
        folder_ids: [2, 5], folders: [{ id: 2, root: '/a', present: true }, { id: 5, root: '/old/a', present: true }] }),
      P({ id: 3, display_name: 'portfolio', present: false, last_worked_at: '2026-07-31T00:00:00Z', latest: { title: 'Old thing', branch: 'main', session_id: 'claude_code:y' } }),
    ] }, isLoading: false, error: null };
  },
}));

it('lists active projects with their latest work and totals', () => {
  render(<ProjectsPage />);
  expect(screen.getByText('Where you worked in the last 30 days')).toBeInTheDocument();
  expect(screen.getByText('argus-code')).toBeInTheDocument();
  const row = within(screen.getByRole('row', { name: /argus-code/ }));
  expect(row.getByText('fix-severe-issues')).toBeInTheDocument();
  expect(row.getByText('1.4M')).toBeInTheDocument();
  expect(row.getByText('$268')).toBeInTheDocument();
  expect(screen.getByText('2 folders')).toHaveAttribute('title', '/a\n/old/a');
});

it('folds quiet projects behind one line', () => {
  render(<ProjectsPage />);
  expect(screen.queryByText('portfolio')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /1 quiet project/ }));
  expect(screen.getByText('portfolio')).toBeInTheDocument();
  expect(screen.getByText('repo gone · archived')).toBeInTheDocument();
});

it('opens a project', () => {
  render(<ProjectsPage />);
  fireEvent.click(screen.getByText('argus-code'));
  expect(nav).toHaveBeenCalledWith({ to: '/projects/$repo', params: { repo: '2' }, search: { tab: 'overview' } });
});

it('the period chips change the period', () => {
  render(<ProjectsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'All time' }));
  expect(asked.at(-1)).toBe(0);
  expect(screen.getByText('Where you worked, all time')).toBeInTheDocument();
});

it('formats hours', () => {
  expect(hours(68_400_000)).toBe('19h');
  expect(hours(5_400_000)).toBe('1.5h');
  expect(hours(720_000)).toBe('12m');
  expect(hours(720_000, true)).toBe('≈12m');
});
