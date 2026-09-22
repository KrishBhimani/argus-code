import { windowDays, dayKeys, sliceWindow, widerWindowFor } from './windows';

const today = new Date('2026-08-21T12:00:00');

it('windowDays', () => {
  expect(windowDays('24h')).toBe(1);
  expect(windowDays('7d')).toBe(7);
  expect(windowDays('all')).toBeNull();
});

it('dayKeys ends at the given day', () => expect(dayKeys(today, 3)).toEqual(['2026-08-19', '2026-08-20', '2026-08-21']));

it('sliceWindow sums the given day keys; widerWindowFor steps up', () => {
  const byDay = { '2026-08-21': 5, '2026-08-20': 5, '2026-08-19': 1 };
  expect(sliceWindow(byDay, dayKeys(today, 2))).toBe(10);
  expect(widerWindowFor('7d')).toBe('30d');
});
