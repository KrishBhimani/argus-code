import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAlerts, useUnseenAlerts } from './hooks';

vi.mock('./client', () => ({
  api: { alerts: async () => ({ alerts: [] }), unseenAlerts: async () => ({ alerts: [] }) },
}));

const wrap = (qc: QueryClient) => ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={qc}>{children}</QueryClientProvider>
);

// An open Alerts page must track alerts that re-open or escalate; if the full
// list never refetches it falls behind the unseen badge (issue #58).
it('polls the full alert list on the same cadence as the unseen list', async () => {
  const qc = new QueryClient();
  const a = renderHook(() => useAlerts(), { wrapper: wrap(qc) });
  const u = renderHook(() => useUnseenAlerts(), { wrapper: wrap(qc) });
  await waitFor(() => expect(a.result.current.isSuccess && u.result.current.isSuccess).toBe(true));
  const interval = (key: unknown[]) => (qc.getQueryCache().find({ queryKey: key, exact: true })?.observers[0]?.options as { refetchInterval?: unknown }).refetchInterval;
  expect(interval(['alerts', 'unseen'])).toBeGreaterThan(0);
  expect(interval(['alerts'])).toBe(interval(['alerts', 'unseen']));
});
