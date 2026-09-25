/** The last two segments of a folder path: enough to tell clones apart. */
export const shortPath = (root: string): string => root.replace(/\\/g, '/').split('/').filter(Boolean).slice(-2).join('/');

export const hours = (ms: number, estimated = false): string => {
  const h = ms / 3_600_000;
  const s = h >= 10 ? `${Math.round(h)}h` : h >= 1 ? `${+h.toFixed(1)}h` : `${Math.round(ms / 60_000)}m`;
  return estimated ? `≈${s}` : s;
};
