import { Link } from '@tanstack/react-router';
import { ago, tok, usd } from '@/lib/format/format';
import type { ThreadStateT, WorkThread } from './api';
import { STATE } from './fmt';

export function StateBadge({ state }: { state: ThreadStateT }) {
  const s = STATE[state];
  return <span className={`shrink-0 text-[10px] tracking-[0.08em] px-1.5 py-px rounded-sm border ${s.className}`}>{s.label}</span>;
}

/** One open thread on the home page: what it is, why it's open, and the two ways back in. */
export function ThreadCard({ t }: { t: WorkThread }) {
  const last = t.session_ids[t.session_ids.length - 1];
  return (
    <article className="h-full rounded-lg border border-line bg-bg-1 px-4 py-3 flex flex-col gap-2.5 min-w-0">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[11px] text-ink-1 truncate">{t.project_name}</span>
        <StateBadge state={t.state} />
      </div>
      <div className="flex flex-col gap-0.5 min-w-0">
        <b className="text-[14px] font-semibold leading-snug line-clamp-2" title={t.title}>{t.title}</b>
        {t.branch && <span className="font-mono text-[11px] text-ink-2 truncate" title={t.branch}>{t.branch}</span>}
      </div>
      <p className="m-0 text-[12px] text-ink-0 line-clamp-2" title={t.reason}>{t.reason}</p>
      {/* Pinned to the bottom so the buttons line up across a row, whatever the text above. */}
      <div data-footer className="mt-auto flex flex-col gap-2.5">
        <div className="flex justify-between font-mono text-[11px] text-ink-2 border-t border-line pt-2">
          <span>{tok(t.output_tokens)} tok · {usd(t.cost)}</span><span>{ago(t.last_ts)}</span>
        </div>
        <div className="flex gap-2 text-[12px]">
          <Link to="/projects/$repo" params={{ repo: String(t.project_id) }} search={{ tab: 'threads', focus: t.key }}
            className="rounded-md border border-line-2 px-2.5 py-1.5 text-ink-0">Resume</Link>
          <Link to="/sessions/$id" params={{ id: last }} search={{ tab: 'overview' }} className="px-2.5 py-1.5">Open last session</Link>
        </div>
      </div>
    </article>
  );
}
