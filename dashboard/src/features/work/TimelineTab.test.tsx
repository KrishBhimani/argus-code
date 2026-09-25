import { render, screen, fireEvent } from '@testing-library/react';
import TimelineTab from './TimelineTab';

const calls: unknown[] = [];
vi.mock('./api', () => ({
  useWorkTimeline: (_r: number, _range: unknown, f: unknown) => {
    calls.push(f);
    return { data: { days: [{ day: '2026-09-21', items: [
      { kind: 'session', session_id: 'claude_code:A', title: 'Composio connectors', branch: 'feat/composio', first_ts: '2026-09-21T08:32:00Z', last_ts: '2026-09-21T11:10:00Z',
        active_ms: 6_840_000, cost: 71, turns: 38, model: 'claude-opus-5', tool_errors: 2, sort_ts: '2026-09-21T08:32:00Z',
        commits: [{ sha: 'a41c9e2aa', evidence: 'exact', added: 64, deleted: 12, files: 2, subject: 'feat: retry auth', author_name: 'Me' }] },
      { kind: 'commit', sha: '9be1a70bb', subject: 'Merge PR #212', author_name: 'Me', authored_at: '2026-09-21T12:00:00Z', evidence: null, session_id: null, added: 0, deleted: 0, sort_ts: '2026-09-21T12:00:00Z' },
    ] }] }, isLoading: false, error: null };
  },
}));
vi.mock('@tanstack/react-router', () => ({ Link: (p: { children: React.ReactNode }) => <a>{p.children}</a> }));

it('nests commits under their session with an evidence badge', () => {
  render(<TimelineTab repo={1} />);
  expect(screen.getByText('Composio connectors')).toBeInTheDocument();
  expect(screen.getByText('feat: retry auth')).toBeInTheDocument();
  expect(screen.getByText('exact')).toBeInTheDocument();
  expect(screen.getByText('Merge PR #212')).toBeInTheDocument();
  expect(screen.getByText('no session')).toBeInTheDocument();
});

it('filter chips change the query', () => {
  render(<TimelineTab repo={1} />);
  fireEvent.click(screen.getByRole('button', { name: 'Commits' }));
  expect(calls.at(-1)).toMatchObject({ kind: 'commits' });
  fireEvent.click(screen.getByRole('button', { name: 'All authors' }));
  expect(calls.at(-1)).toMatchObject({ scope: 'all' });
  fireEvent.click(screen.getByRole('button', { name: 'feat/composio' }));
  expect(calls.at(-1)).toMatchObject({ branch: 'feat/composio' });
});
