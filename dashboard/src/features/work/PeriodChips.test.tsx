import { render, screen, fireEvent } from '@testing-library/react';
import { PeriodChips, periodLabel } from './PeriodChips';

it('offers 30 days to all time and reports the chosen span in days (0 = all)', () => {
  const onChange = vi.fn();
  render(<PeriodChips days={30} onChange={onChange} />);
  expect(screen.getByRole('button', { name: '30 days' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'All time' }));
  expect(onChange).toHaveBeenCalledWith(0);
  expect(periodLabel(0)).toBe('all time');
  expect(periodLabel(90)).toBe('last 90 days');
});
