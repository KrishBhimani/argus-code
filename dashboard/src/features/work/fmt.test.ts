import { expect, it } from 'vitest';
import { evidenceBadge, stateBadge } from './fmt';

it('names the known commit evidence and thread states', () => {
  expect(evidenceBadge('files').label).toBe('edited here');
  expect(stateBadge('explored').label).toBe('EXPLORED');
});

// Regression: a backend newer than the built dashboard sent evidence "files" and state
// "explored"; the lookup returned undefined and the whole page crashed on `.className`.
it('shows a value it does not know as a plain badge instead of crashing', () => {
  expect(evidenceBadge('from_the_future')).toEqual({ label: 'from the future', className: expect.any(String) });
  expect(stateBadge('from_the_future')).toEqual({ label: 'FROM THE FUTURE', className: expect.any(String) });
});
