export const hours = (ms: number, estimated = false): string => {
  const h = ms / 3_600_000;
  const s = h >= 10 ? `${Math.round(h)}h` : h >= 1 ? `${+h.toFixed(1)}h` : `${Math.round(ms / 60_000)}m`;
  return estimated ? `≈${s}` : s;
};
