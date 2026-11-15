import { createFileRoute, lazyRouteComponent } from '@tanstack/react-router';
import type { WorkTab } from '@/features/work/ProjectPage';

const TABS: WorkTab[] = ['threads', 'activity'];
// Links from before the redesigns: Timeline became Activity, Overview became Threads.
const LEGACY: Record<string, WorkTab> = { timeline: 'activity', overview: 'threads' };

export const Route = createFileRoute('/projects/$repo')({
  validateSearch: (s: Record<string, unknown>): { tab: WorkTab; focus?: string } => ({
    tab: TABS.includes(s.tab as WorkTab) ? (s.tab as WorkTab) : LEGACY[s.tab as string] ?? 'threads',
    // Threads: a thread key; Activity: session ids (comma-separated). Scrolled to and highlighted.
    ...(typeof s.focus === 'string' && s.focus ? { focus: s.focus } : {}),
  }),
  component: lazyRouteComponent(() => import('@/features/work/ProjectPage')),
});
