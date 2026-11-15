import { render, screen, fireEvent } from '@testing-library/react';
import ActivityTab from './ActivityTab';

const calls: { kind: string; branch?: string; scope: string }[] = [];
const ranges: unknown[] = [];
const outside = (sha: string, subject: string, at: string) => ({
  kind: 'commit', sha, subject, author_name: 'Me', authored_at: at, evidence: null, session_id: null, added: 9, deleted: 7, sort_ts: at,
});
vi.mock('./api', () => ({
  useWorkTimeline: (_r: number, range: unknown, f: { kind: string; branch?: string; scope: string }) => {
    calls.push(f);
    ranges.push(range);
    return { data: { days: [
      { day: '2026-09-25', items: [
        outside('8e94457aa', 'fix(ingest): prompt-only sub-agent (#48)', '2026-09-25T11:36:00Z'),
        outside('cdcd15baa', 'fix(ingest): de-duplicate fork copies (#47)', '2026-09-25T10:58:00Z'),
      ] },
      { day: '2026-09-21', items: [
        { kind: 'session', session_id: 'claude_code:A', title: 'Composio connectors', branch: 'feat/composio', first_ts: '2026-09-21T08:32:00Z', last_ts: '2026-09-21T11:10:00Z',
          active_ms: 6_840_000, active_estimated: false, cost: 71, turns: 38, model: 'claude-opus-5', tool_errors: 2, output_tokens: 120_000, sort_ts: '2026-09-21T08:32:00Z',
          commits: [{ sha: 'a41c9e2aa', evidence: 'exact', added: 64, deleted: 12, files: 2, subject: 'feat: retry auth', author_name: 'Me' }] },
        outside('9be1a70bb', 'Merge PR #212', '2026-09-21T12:00:00Z'),
      ] },
    ] }, isLoading: false, error: null };
  },
}));
vi.mock('@tanstack/react-router', () => ({ Link: (p: { children: React.ReactNode }) => <a>{p.children}</a> }));

it('heads each day with its totals', () => {
  render(<ActivityTab repo={1} />);
  expect(screen.getByText(/Sep 25/)).toBeInTheDocument();
  expect(screen.getByText('no new Claude sessions · 2 commits')).toBeInTheDocument();
  expect(screen.getByText('1 session · 120.0k tokens · $71.00 · 2 commits')).toBeInTheDocument();
});

it('folds commits made outside Claude sessions into one line per day', () => {
  render(<ActivityTab repo={1} />);
  expect(screen.queryByText('fix(ingest): de-duplicate fork copies (#47)')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /2 commits made outside Claude sessions/ }));
  expect(screen.getByText('fix(ingest): de-duplicate fork copies (#47)')).toBeInTheDocument();
});

it('shows a session with its commits and plain-word evidence', () => {
  render(<ActivityTab repo={1} />);
  expect(screen.getByText('Composio connectors')).toBeInTheDocument();
  expect(screen.getByText('feat: retry auth')).toBeInTheDocument();
  expect(screen.getByText('made here')).toBeInTheDocument();
  expect(screen.getByText(/2 tool errors/)).toBeInTheDocument();
});

it('filters change the query', () => {
  render(<ActivityTab repo={1} />);
  fireEvent.click(screen.getByRole('button', { name: 'Claude sessions only' }));
  expect(calls.at(-1)).toMatchObject({ kind: 'sessions' });
  fireEvent.click(screen.getByRole('button', { name: 'All authors' }));
  expect(calls.at(-1)).toMatchObject({ scope: 'all' });
  fireEvent.change(screen.getByLabelText('Branch'), { target: { value: 'feat/composio' } });
  expect(calls.at(-1)).toMatchObject({ branch: 'feat/composio' });
});

it('asks for the chosen period (0 = all time)', () => {
  render(<ActivityTab repo={1} days={0} />);
  expect(ranges.at(-1)).toEqual({ days: 0 });
});

it('scrolls to and highlights the focused session', () => {
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;
  render(<ActivityTab repo={1} focus="claude_code:A" />);
  expect(screen.getByText('Composio connectors').closest('article')).toHaveAttribute('data-focused', 'true');
  expect(scroll).toHaveBeenCalled();
});
