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
