import { render, screen } from '@testing-library/react';
import ProjectsPage from './ProjectsPage';
import { hours } from './fmt';

const nav = vi.fn();
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => nav, Link: (p: { children: React.ReactNode }) => <a>{p.children}</a> }));
vi.mock('./api', () => ({
  useWorkProjects: () => ({ data: { projects: [
    { id: 2, display_name: 'argus-code', root: '/a', present: true, last_error: null, active_ms_30d: 68_400_000, commits_30d: 63, daily_active_ms: [0, 1, 2], last_worked_at: new Date().toISOString(),
      folder_ids: [2, 5], folders: [{ id: 2, root: '/a', present: true }, { id: 5, root: '/old/a', present: true }] },
    { id: 3, display_name: 'portfolio', root: '/p', present: false, last_error: null, active_ms_30d: 0, commits_30d: 12, daily_active_ms: [0, 0, 0], last_worked_at: '2026-08-01T00:00:00Z',
      folder_ids: [3], folders: [{ id: 3, root: '/p', present: false }] },
  ] }, isLoading: false, error: null }),
}));

it('lists projects, marks gone repos, and opens a project', () => {
  render(<ProjectsPage />);
  expect(screen.getByText('argus-code')).toBeInTheDocument();
  expect(screen.getByText('19h')).toBeInTheDocument();
  expect(screen.getByText('repo gone · archived')).toBeInTheDocument();
  screen.getByText('argus-code').click();
  expect(nav).toHaveBeenCalledWith({ to: '/projects/$repo', params: { repo: '2' }, search: { tab: 'overview' } });
});

it('formats hours', () => {
  expect(hours(68_400_000)).toBe('19h');
  expect(hours(5_400_000)).toBe('1.5h');
  expect(hours(720_000)).toBe('12m');
  expect(hours(720_000, true)).toBe('≈12m');
});

it('marks a project that spans several folders, listing them on hover', () => {
  render(<ProjectsPage />);
  const badge = screen.getByText('2 folders');
  expect(badge).toHaveAttribute('title', '/a\n/old/a');
  expect(screen.queryByText('1 folders')).not.toBeInTheDocument();
});
