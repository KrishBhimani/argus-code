import { render, screen } from '@testing-library/react';
import ProjectPage from './ProjectPage';

vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ repo: '19' }),          // an old link to the second folder
  useSearch: () => ({ tab: 'overview' }),
  Link: (p: { children: React.ReactNode }) => <a>{p.children}</a>,
}));
vi.mock('@/app/shell/TopBar', () => ({
  TopBar: ({ crumbs }: { crumbs: (string | { label: string })[] }) => <div>{crumbs.map((c) => (typeof c === 'string' ? c : c.label)).join(' / ')}</div>,
  Page: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('./OverviewTab', () => ({ default: () => null }));
vi.mock('./ActivityTab', () => ({ default: () => null }));
vi.mock('./api', () => ({
  useWorkProjects: () => ({ data: { projects: [{
    id: 18, display_name: 'argus-code', root: 'C:/documents/GEN AI/Argus-CLI/argus-cli', present: true, last_error: null,
    active_ms: 0, active_estimated: false, tokens: 0, cost: 0, commits: 0, daily_tokens: [], last_worked_at: null, latest: null, folder_ids: [18, 19],
    folders: [
      { id: 18, root: 'C:/documents/GEN AI/Argus-CLI/argus-cli', present: true },
      { id: 19, root: 'C:/documents/Space/argus-code', present: false },
    ],
  }] } }),
}));

it('any folder id opens the grouped project and lists its folders', () => {
  render(<ProjectPage />);
  expect(screen.getByText('Projects / argus-code')).toBeInTheDocument();
  expect(screen.getByText('2 folders')).toBeInTheDocument();
  expect(screen.getByText('Argus-CLI/argus-cli')).toHaveAttribute('title', 'C:/documents/GEN AI/Argus-CLI/argus-cli');
  expect(screen.getByText('Space/argus-code')).toHaveAttribute('title', 'C:/documents/Space/argus-code (folder no longer exists)');
});

it('has two views, Overview and Activity, and no placeholder tabs', () => {
  render(<ProjectPage />);
  expect(screen.getByText('Overview')).toBeInTheDocument();
  expect(screen.getByText('Activity')).toBeInTheDocument();
  expect(screen.queryByText('Resume')).not.toBeInTheDocument();
  expect(screen.queryByText('Report')).not.toBeInTheDocument();
});
