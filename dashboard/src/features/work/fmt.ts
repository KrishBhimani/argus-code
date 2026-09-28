import type { ThreadStateT } from './api';

/** The last two segments of a folder path: enough to tell clones apart. */
export const shortPath = (root: string): string => root.replace(/\\/g, '/').split('/').filter(Boolean).slice(-2).join('/');

export const hours = (ms: number, estimated = false): string => {
  const h = ms / 3_600_000;
  const s = h >= 10 ? `${Math.round(h)}h` : h >= 1 ? `${+h.toFixed(1)}h` : `${Math.round(ms / 60_000)}m`;
  return estimated ? `≈${s}` : s;
};

/** How a commit is tied to the work, in plain words (the API's exact / coauthored / inferred). */
export const EVIDENCE: Record<'exact' | 'coauthored' | 'inferred', { label: string; className: string }> = {
  exact: { label: 'made here', className: 'bg-s3/20 text-[#5fd3a6]' },
  coauthored: { label: 'Claude co-author', className: 'bg-s1/20 text-[#8ab8ff]' },
  inferred: { label: 'likely', className: 'bg-bg-3 text-ink-1' },
};
export const EVIDENCE_KEY = 'made here: the session ran the commit · Claude co-author: the commit names Claude · likely: committed within 30 min of a Claude turn';

/** A thread's state as a badge: text always, colour second (blue/amber vs green/grey, not red/green). */
export const STATE: Record<ThreadStateT, { label: string; className: string }> = {
  uncommitted: { label: 'UNCOMMITTED', className: 'text-warn border-warn/50' },
  not_pushed: { label: 'NOT PUSHED', className: 'text-warn border-warn/50' },
  pr_open: { label: 'PR OPEN', className: 'text-[#8ab8ff] border-s1/50' },
  in_progress: { label: 'IN PROGRESS', className: 'text-[#8ab8ff] border-s1/50' },
  stalled: { label: 'STALLED', className: 'text-accent-ink border-accent/50' },
  shipped: { label: 'SHIPPED', className: 'text-[#5fd3a6] border-s3/50' },
  dropped: { label: 'DROPPED', className: 'text-ink-2 border-line-2' },
  unknown: { label: 'UNKNOWN', className: 'text-ink-1 border-dashed border-line-2' },
};
export const OPEN_STATES: ReadonlySet<ThreadStateT> = new Set(['uncommitted', 'not_pushed', 'pr_open', 'stalled', 'in_progress']);
