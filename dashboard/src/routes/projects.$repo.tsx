import { createFileRoute, lazyRouteComponent } from '@tanstack/react-router';
import type { WorkTab } from '@/features/work/ProjectPage';

const TABS: WorkTab[] = ['overview', 'activity'];
// Links from before the redesign: Timeline became Activity.
const LEGACY: Record<string, WorkTab> = { timeline: 'activity' };

export const Route = createFileRoute('/projects/$repo')({
  validateSearch: (s: Record<string, unknown>): { tab: WorkTab; focus?: string } => ({
    tab: TABS.includes(s.tab as WorkTab) ? (s.tab as WorkTab) : LEGACY[s.tab as string] ?? 'overview',
    // Timeline: session ids (comma-separated) to scroll to and highlight
    ...(typeof s.focus === 'string' && s.focus ? { focus: s.focus } : {}),
  }),
  component: lazyRouteComponent(() => import('@/features/work/ProjectPage')),
});
