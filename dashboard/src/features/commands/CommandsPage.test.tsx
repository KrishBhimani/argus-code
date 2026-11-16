import { fireEvent, render, screen, within } from '@testing-library/react';
import CommandsPage from './CommandsPage';
import type { CommandList } from '@/lib/api/schemas';

const row = (o: Partial<CommandList['items'][number]>): CommandList['items'][number] => ({
  id: 'claude_code:a:t1', session_id: 'claude_code:a', timestamp: '2026-11-10T10:00:00Z', agent: 'claude_code', shell: 'bash',
  cwd: 'C:/work/alpha', project_path: 'C:/work/alpha', command: 'git status', truncated: false, background: false, failed: false,
  severity: null, risks: [], secrets: [], ...o,
});
const XSS = '<img src=x onerror="window.__pwned = 1">';
const items = [
  row({ id: 'claude_code:a:t1', command: 'git status' }),
  row({ id: 'claude_code:a:t2', command: 'rm -rf /', severity: 'high', risks: [{ rule: 'delete-recursive', severity: 'high' }], failed: true }),
  row({ id: 'codex:c:t3', session_id: 'codex:c', agent: 'codex', shell: 'shell', command: 'export TOKEN=[redacted:assignment]', secrets: ['assignment'] }),
  row({ id: 'claude_code:a:t4', command: XSS, truncated: true, background: true }),
];
const asked: Record<string, unknown>[] = [];
vi.mock('@/app/shell/TopBar', () => ({
  TopBar: (p: { children?: React.ReactNode }) => <div>{p.children}</div>,
  Page: (p: { children?: React.ReactNode }) => <div>{p.children}</div>,
}));
vi.mock('@tanstack/react-router', () => ({
  Link: (p: { children?: React.ReactNode; params?: { id?: string } }) => <a href={`/sessions/${p.params?.id}`}>{p.children}</a>,
  useSearch: () => ({}),
}));
vi.mock('@/lib/api/hooks', () => ({
  useCommands: (p: Record<string, unknown>) => {
    asked.push(p);
    return { data: { items, total: 4, counts: { total: 4, high: 1, medium: 0, low: 0, secrets: 1 } }, isFetching: false, error: null };
  },
}));
beforeEach(() => { asked.length = 0; });

it('lists commands with what was flagged and why', () => {
  render(<CommandsPage />);
  const risky = within(screen.getByText('rm -rf /').closest('[data-row]') as HTMLElement);
  expect(risky.getByText('HIGH')).toBeInTheDocument();
  expect(risky.getByText('delete-recursive')).toBeInTheDocument();
  expect(risky.getByText('failed')).toBeInTheDocument();
  const secret = within(screen.getByText('export TOKEN=[redacted:assignment]').closest('[data-row]') as HTMLElement);
  expect(secret.getByText('secret masked')).toBeInTheDocument();
  expect(secret.getByText(/Codex · shell/)).toBeInTheDocument();
  expect(screen.getByText('4 commands · 1 high · 0 medium · 0 low · 1 with a secret')).toBeInTheDocument();
});

it('shows a command as text, never as markup', () => {
  render(<CommandsPage />);
  expect(screen.getByText(XSS)).toBeInTheDocument();           // the literal characters
  expect(document.querySelector('img')).toBeNull();            // no element was made from them
  expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  const r = within(screen.getByText(XSS).closest('[data-row]') as HTMLElement);
  expect(r.getByText('truncated')).toBeInTheDocument();
  expect(r.getByText('background')).toBeInTheDocument();
});

it('asks the server for what the filters say', () => {
  render(<CommandsPage />);
  expect(asked.at(-1)).toMatchObject({ window: '30d', q: '', severity: undefined, secrets: false });
  fireEvent.click(screen.getByRole('button', { name: 'High risk' }));
  expect(asked.at(-1)).toMatchObject({ severity: 'high' });
  fireEvent.click(screen.getByRole('button', { name: 'With a secret' }));
  expect(asked.at(-1)).toMatchObject({ severity: 'high', secrets: true });
  fireEvent.click(screen.getByRole('button', { name: 'All time' }));
  expect(asked.at(-1)).toMatchObject({ window: 'all' });
});

it('links each command to its session and says what a flag means', () => {
  render(<CommandsPage />);
  expect(screen.getAllByRole('link', { name: /Open session/ })[0]).toHaveAttribute('href', '/sessions/claude_code:a');
  expect(screen.getByText(/A flag means "worth a look"/)).toBeInTheDocument();
});
