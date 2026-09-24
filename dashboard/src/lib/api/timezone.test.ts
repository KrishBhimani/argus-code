import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest';
import overview from './__fixtures__/overview.json';
import trends from './__fixtures__/trends.json';
import { api, tzOffsetMin } from './client';
import { dayKeys } from '@/lib/analysis/windows';

// REGRESSION (H8): the server bucketed days in UTC while the dashboard built
// local date keys. Pinned to the maintainer's zone (IST, UTC+5:30) at 02:10
// local — between local midnight and the UTC rollover, when "today" never
// matched and the chart plotted 0 next to a 91.6M Tokens tile.
// No @types/node in this project: reach Node's env through globalThis.
const env = (globalThis as unknown as { process: { env: Record<string, string | undefined> } }).process.env;
const prevTZ = env.TZ;
beforeAll(() => {
  env.TZ = 'Asia/Kolkata';
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-09-22T20:40:00Z')); // 02:10 on the 23rd in IST
});
afterAll(() => {
  vi.useRealTimers();
  env.TZ = prevTZ;
});
afterEach(() => vi.unstubAllGlobals());

const stubFetch = (body: unknown) => {
  const f = vi.fn(async (_url: string) => new Response(JSON.stringify(body), { status: 200 }));
  vi.stubGlobal('fetch', f);
  return f;
};

it('reports the viewer offset in minutes east of UTC', () => {
  expect(tzOffsetMin()).toBe(330);
});

it('asks the server to bucket days in the viewer timezone', async () => {
  const f = stubFetch(overview);
  await api.overview('7d');
  expect(String(f.mock.calls[0][0])).toContain('tz=330');
  const g = stubFetch(trends);
  await api.trends('day', 'model');
  expect(String(g.mock.calls[0][0])).toContain('tz=330');
});

it("local 'today' is the key the server uses for a turn at 20:40 UTC", () => {
  // The server (tz=330) buckets 2026-09-22T20:40Z under 2026-09-23 — the same
  // key the chart looks up for today.
  expect(dayKeys(new Date(), 1)).toEqual(['2026-09-23']);
});
