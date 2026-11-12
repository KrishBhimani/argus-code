import { render, screen } from '@testing-library/react';
import { ThreadCard } from './ThreadCard';

vi.mock('@tanstack/react-router', () => ({ Link: (p: { children: React.ReactNode }) => <a>{p.children}</a> }));
const t = {
  key: 'k', project_id: 1, project_name: 'xo-space', title: 'Project sharing page redesign', branch: 'claude/project-sharing-redesign-41ea0b',
  state: 'in_progress' as const, reason: '10 commits on claude/project-sharing-redesign-41ea0b', first_ts: '2026-09-28T09:00:00Z',
  last_ts: '2026-09-28T10:00:00Z', cost: 15.8, output_tokens: 202_500, commits: 10, prs: [], session_ids: ['k'], remote_as_of: null,
};

it('keeps the footer at the bottom and long text on at most two lines, full text on hover', () => {
  render(<ThreadCard t={t} />);
  const reason = screen.getByText(t.reason);
  expect(reason).toHaveClass('line-clamp-2');
  expect(reason).toHaveAttribute('title', t.reason);
  expect(screen.getByText(t.branch)).toHaveAttribute('title', t.branch);
  expect(screen.getByText('Resume').closest('[data-footer]')).toHaveClass('mt-auto');
});
