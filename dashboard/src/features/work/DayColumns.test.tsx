import { fireEvent, render, screen } from '@testing-library/react';
import { DayColumns, normSel } from './DayColumns';

const days = ['2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04'];

it('normalizes a drag in either direction', () => {
  expect(normSel(3, 1)).toEqual({ a: 1, b: 3 });
});

it('selects a single day on click and a range on drag', () => {
  const onSel = vi.fn();
  render(<DayColumns label="Active hours" days={days} values={[1, 2, 0, 4]} format={String} sel={null} onSel={onSel} />);
  const cols = screen.getAllByTestId('daycol');
  fireEvent.pointerDown(cols[1]); fireEvent.pointerUp(cols[1]);
  expect(onSel).toHaveBeenLastCalledWith({ a: 1, b: 1 });
  fireEvent.pointerDown(cols[3]); fireEvent.pointerEnter(cols[0]); fireEvent.pointerUp(cols[0]);
  expect(onSel).toHaveBeenLastCalledWith({ a: 0, b: 3 });
});

it('marks selected days and exposes values on hover', () => {
  render(<DayColumns label="Commits" days={days} values={[1, 2, 0, 4]} format={(v) => `${v} commits`} sel={{ a: 1, b: 2 }} onSel={() => {}} />);
  const cols = screen.getAllByTestId('daycol');
  expect(cols[1]).toHaveAttribute('data-selected', 'true');
  expect(cols[0]).toHaveAttribute('data-selected', 'false');
  expect(screen.getByText('2026-09-04 · 4 commits')).toBeInTheDocument();
});
