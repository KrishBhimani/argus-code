import { useEffect, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { Chip } from '@/components/ui/Chip';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { num, usd } from '@/lib/format/format';
import { useWorkTimeline, type Scope } from './api';
import { hours } from './fmt';
import { periodLabel } from './PeriodChips';

const BADGE: Record<string, string> = {
  exact: 'bg-s3/20 text-[#5fd3a6]', coauthored: 'bg-s1/20 text-[#8ab8ff]', inferred: 'bg-bg-3 text-ink-1',
};
const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

function CommitRow({ c, nested }: { c: { sha: string; subject: string; evidence: string | null; added: number; deleted: number; session_id?: string | null }; nested: boolean }) {
  return (
    <div className={`flex items-center gap-2 text-[12px] py-0.5 ${nested ? 'pl-4' : ''}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-s3 shrink-0" />
      <span className="font-mono text-ink-2">{c.sha.slice(0, 7)}</span>
      <span className="truncate">{c.subject}</span>
      {c.evidence ? <span className={`text-[10px] px-1.5 rounded-sm ${BADGE[c.evidence]}`}>{c.evidence === 'coauthored' ? 'co-authored' : c.evidence}</span>
        : !nested && <span className="text-[10px] text-ink-2">no session</span>}
      <span className="text-ink-2 font-mono text-[11px]">+{num(c.added)} −{num(c.deleted)}</span>
    </div>
  );
}

export default function TimelineTab({ repo, days = 30, focus }: { repo: number; days?: number; focus?: string }) {
  const focused = new Set(focus ? focus.split(',') : []);
  const list = useRef<HTMLDivElement>(null);
  const [kind, setKind] = useState<'all' | 'sessions' | 'commits'>('all');
  const [scope, setScope] = useState<Scope>('mine');
  const [branch, setBranch] = useState<string | undefined>(undefined);
  const q = useWorkTimeline(repo, { days }, { kind, scope, branch });
  const branches = [...new Set((q.data?.days ?? []).flatMap((d) => d.items.flatMap((i) => (i.kind === 'session' && i.branch ? [i.branch] : []))))].sort();
  // Arriving from a story's "Open in Timeline": bring its session into view once loaded.
  useEffect(() => {
    list.current?.querySelector('[data-focused="true"]')?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }, [q.data, focus]);
  if (q.error) return <ErrorPanel error={q.error} />;
  return (
    <div ref={list} className="flex flex-col gap-3">
      <div className="flex gap-2">
        {(['all', 'sessions', 'commits'] as const).map((k) => (
          <Chip key={k} active={kind === k} onClick={() => setKind(k)}>{k === 'all' ? 'All' : k === 'sessions' ? 'Sessions' : 'Commits'}</Chip>
        ))}
        <span className="w-px bg-line mx-1" />
        <Chip active={scope === 'mine'} onClick={() => setScope('mine')}>Mine + agent</Chip>
        <Chip active={scope === 'all'} onClick={() => setScope('all')}>All authors</Chip>
        <span className="w-px bg-line mx-1" />
        {branch ? <Chip active onClick={() => setBranch(undefined)}>branch: {branch} ✕</Chip>
          : branches.map((b) => <Chip key={b} onClick={() => setBranch(b)}>{b}</Chip>)}
      </div>
      {q.data && q.data.days.length === 0 && <EmptyState title={`Nothing in ${periodLabel(days)}`} />}
      {q.data?.days.map((d) => (
        <section key={d.day}>
          <div className="text-[10px] tracking-[0.08em] text-ink-2 mb-1">{new Date(`${d.day}T12:00:00`).toDateString().toUpperCase()}</div>
          <div className="border-l border-line-2 ml-1.5 pl-4 flex flex-col gap-2">
            {d.items.map((it) => it.kind === 'session' ? (
              <article key={it.session_id} data-session-id={it.session_id} data-focused={focused.has(it.session_id)}
                className={`relative ${focused.has(it.session_id) ? 'rounded-md bg-accent/10 ring-1 ring-accent/60 -mx-2 px-2 py-1' : ''}`}>
                <span className="absolute -left-[21px] top-1.5 w-2.5 h-2.5 rounded-full bg-s1" />
                <div className="flex items-center gap-2">
                  <b className="text-[13px]">{it.title ?? 'Untitled session'}</b>
                  {it.branch && <span className="font-mono text-[10px] text-ink-2">{it.branch}</span>}
                </div>
                <div className="text-[11px] text-ink-2">
                  {time(it.first_ts)}–{time(it.last_ts)} · {hours(it.active_ms, it.active_estimated)} active · {usd(it.cost)} · {it.model ?? '—'} · {num(it.turns)} turns
                  {it.tool_errors > 0 && ` · ${it.tool_errors} tool errors`} ·{' '}
                  <Link to="/sessions/$id" params={{ id: it.session_id }} search={{ tab: 'overview' }}>open session →</Link>
                </div>
                {it.commits.map((c) => <CommitRow key={c.sha} c={c} nested />)}
              </article>
            ) : <CommitRow key={it.sha} c={it} nested={false} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
