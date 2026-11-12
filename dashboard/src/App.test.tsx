import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createRouter, createMemoryHistory } from '@tanstack/react-router';
import { routeTree } from './routeTree.gen';
import { queryClient } from './app/queryClient';

it('renders the shell at /', async () => {
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ['/'] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  // Whole-app render under a parallel full run already took ~1.1 s on main, right at
  // findByText's 1 s default; this asserts that the shell renders, not how fast.
  expect(await screen.findByText('ARGUS', {}, { timeout: 5000 })).toBeInTheDocument();
});
