import { createFileRoute, lazyRouteComponent } from '@tanstack/react-router';

export const Route = createFileRoute('/commands')({
  validateSearch: (s: Record<string, unknown>): { session?: string } => (typeof s.session === 'string' && s.session ? { session: s.session } : {}),
  component: lazyRouteComponent(() => import('@/features/commands/CommandsPage')),
});
