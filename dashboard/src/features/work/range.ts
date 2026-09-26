import type { Sel } from './DayColumns';

/** Local day keys (viewer tz, minutes east) → UTC ISO [from, to) for the selected days. */
export function selRange(days: string[], sel: Sel, tz: number): { from?: string; to?: string } {
  if (!sel) return {};
  const utc = (day: string) => new Date(Date.parse(`${day}T00:00:00Z`) - tz * 60_000);
  const to = utc(days[sel.b]);
  to.setUTCDate(to.getUTCDate() + 1);
  return { from: utc(days[sel.a]).toISOString(), to: to.toISOString() };
}
