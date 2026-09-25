import { createFileRoute, lazyRouteComponent } from '@tanstack/react-router';
import type { WorkTab } from '@/features/work/ProjectPage';

const TABS: WorkTab[] = ['overview', 'resume', 'timeline', 'report'];

export const Route = createFileRoute('/projects/$repo')({
  validateSearch: (s: Record<string, unknown>) => ({ tab: TABS.includes(s.tab as WorkTab) ? (s.tab as WorkTab) : 'overview' }),
  component: lazyRouteComponent(() => import('@/features/work/ProjectPage')),
});
