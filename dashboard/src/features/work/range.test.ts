import { selRange } from './range';

it('turns a selection of local days into UTC bounds', () => {
  const days = ['2026-09-20', '2026-09-21', '2026-09-22'];
  expect(selRange(days, null, 330)).toEqual({});
  expect(selRange(days, { a: 1, b: 2 }, 330)).toEqual({ from: '2026-09-20T18:30:00.000Z', to: '2026-09-22T18:30:00.000Z' });
});
