import { createFileRoute, lazyRouteComponent } from '@tanstack/react-router';

export const Route = createFileRoute('/projects/')({ component: lazyRouteComponent(() => import('@/features/work/ProjectsPage')) });
