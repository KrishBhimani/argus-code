import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GithubPanel } from './GithubPanel';

const state = { enabled: false, checked_at: null as string | null, last_error: null as string | null, gh_available: true };
const posted: string[] = [];
vi.mock('./api', () => ({
  useWorkGithub: () => ({ data: state }),
  workApi: {
    githubEnable: () => { posted.push('enable'); return Promise.resolve(state); },
    githubDisable: () => { posted.push('disable'); return Promise.resolve(state); },
    githubRefresh: () => { posted.push('refresh'); return Promise.resolve(state); },
  },
}));
const wrap = () => render(<QueryClientProvider client={new QueryClient()}><GithubPanel /></QueryClientProvider>);

beforeEach(() => { posted.length = 0; Object.assign(state, { enabled: false, checked_at: null, last_error: null, gh_available: true }); });

it('is off by default and says what it sends', async () => {
  wrap();
  expect(screen.getByText(/Only PR numbers and repo names are sent/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Turn on' }));
  await waitFor(() => expect(posted).toEqual(['enable']));
});

it('when on: check now, turn off, last checked and the last error', async () => {
  Object.assign(state, { enabled: true, checked_at: new Date().toISOString(), last_error: 'quirq-ai/x: not found' });
  wrap();
  fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
  await waitFor(() => expect(posted).toEqual(['refresh']));
  fireEvent.click(screen.getByRole('button', { name: 'Turn off' }));
  await waitFor(() => expect(posted).toEqual(['refresh', 'disable']));
  expect(screen.getByText(/last checked/)).toBeInTheDocument();
  expect(screen.getByText('quirq-ai/x: not found')).toBeInTheDocument();
});

it('says when gh is missing', () => {
  state.gh_available = false;
  wrap();
  expect(screen.getByText(/gh CLI not found/)).toBeInTheDocument();
});
