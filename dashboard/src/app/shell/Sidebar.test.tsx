import { render, screen } from '@testing-library/react';
import { Sidebar } from './Sidebar';

vi.mock('@tanstack/react-router', () => ({
  Link: (p: { to: string; children: React.ReactNode; 'data-active'?: boolean }) => (
    <a href={p.to} data-active={p['data-active']}>{p.children}</a>
  ),
  useRouterState: () => '/sessions',
}));
vi.mock('@/lib/api/hooks', () => ({
  useUnseenAlerts: () => ({ data: [{ id: 1 }] }),
  useIngestStatus: () => ({ data: { foregroundComplete: true, pending: 0, processed: 1, total: 1, sessionCount: 153 } }),
  usePricing: () => ({ data: { version: '2026-07-31' } }),
}));
const work = { enabled: false };
vi.mock('@/features/work/api', () => ({ useWorkStatus: () => work }));

it('groups navigation by job and shows the unseen-alert badge', () => {
  render(<Sidebar />);
  for (const g of ['MONITOR', 'ANALYZE', 'SEARCH', 'GOVERN']) expect(screen.getByText(g)).toBeInTheDocument();
  expect(screen.getByText('Alerts').parentElement).toHaveTextContent('1');
  expect(screen.getByText('tracking 153 sessions')).toBeInTheDocument();
});

it('shows the WORK group only when the trial is on', () => {
  const { rerender } = render(<Sidebar />);
  expect(screen.queryByText('WORK')).not.toBeInTheDocument();
  work.enabled = true;
  rerender(<Sidebar />);
  expect(screen.getByText('WORK')).toBeInTheDocument();
  expect(screen.getByText('Projects')).toBeInTheDocument();
  work.enabled = false;
});
