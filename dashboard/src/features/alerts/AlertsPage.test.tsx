import { render, screen } from '@testing-library/react';
import AlertsPage from './AlertsPage';

const base = { dedup_key: 'k', metadata: {}, first_seen_at: '2026-10-20T10:00:00Z', last_seen_at: '2026-10-26T10:00:00Z' };

// Detectors re-open and escalate alerts in place (same id). The full list is
// what the page renders, so "unseen" must come from it, not from a second
// query that refreshes on a different schedule (issue #58).
const all = [
  { ...base, id: 601, detector: 'cost_spike', severity: 'critical', title: 'cost 16.5x', message: 'm', seen_at: null },
  { ...base, id: 9, detector: 'tool_error_rate_spike', severity: 'warning', title: 'Grep error rate', message: 'm', seen_at: null },
  { ...base, id: 61, detector: 'tool_error_rate_spike', severity: 'warning', title: 'Write error rate', message: 'm', seen_at: '2026-10-21T12:30:24Z' },
];

vi.mock('@/app/shell/TopBar', () => ({
  TopBar: (p: { children?: React.ReactNode }) => <div>{p.children}</div>,
  Page: (p: { children?: React.ReactNode }) => <div>{p.children}</div>,
}));
vi.mock('@tanstack/react-router', () => ({
  Link: (p: { children?: React.ReactNode; to: string; search?: Record<string, string> }) =>
    <a href={`${p.to}?${new URLSearchParams(p.search)}`}>{p.children}</a>,
}));
vi.mock('@/lib/api/hooks', () => ({
  useAlerts: () => ({ data: all }),
  // Out of step with the list on purpose: a separately cached query that
  // only knows about #601.
  useUnseenAlerts: () => ({ data: [{ id: 601 }] }),
  useMarkAlertSeen: () => ({ mutate: vi.fn() }),
}));

it('shows every unseen alert from the rendered list, even when the unseen query disagrees', () => {
  render(<AlertsPage />);
  expect(screen.getByText('Unseen · 2')).toBeInTheDocument();
  expect(screen.getByText('All · 3')).toBeInTheDocument();
  expect(screen.getByText('cost 16.5x')).toBeInTheDocument();
  expect(screen.getByText('Grep error rate')).toBeInTheDocument();
  expect(screen.queryByText('Write error rate')).not.toBeInTheDocument();
});

it('links a command-ledger alert to that session’s commands, and no other alert', () => {
  all.push(
    { ...base, id: 700, detector: 'risky_command', severity: 'warning', title: 'alpha: 1 high-risk command in one session', message: 'm', seen_at: null,
      metadata: { session_id: 'claude_code:abc/agent-1', project: 'alpha' } } as (typeof all)[number],
    { ...base, id: 701, detector: 'secret_in_command', severity: 'warning', title: 'alpha: a secret appeared in 1 command', message: 'm', seen_at: null,
      metadata: { session_id: 42 } } as unknown as (typeof all)[number],
  );
  render(<AlertsPage />);
  const links = screen.getAllByRole('link', { name: /View commands/ });
  expect(links).toHaveLength(1);                                    // 701 has no usable session id; the others are not ledger alerts
  expect(links[0]).toHaveAttribute('href', '/commands?session=claude_code%3Aabc%2Fagent-1');
});
