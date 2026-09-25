import { useEffect, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { num, tok, usd } from '@/lib/format/format';
import { useWorkTimeline, type Scope, type WorkTimelineT } from './api';
import { EVIDENCE, hours } from './fmt';
import { periodLabel } from './PeriodChips';

type Item = WorkTimelineT['days'][number]['items'][number];
type SessionItem = Extract<Item, { kind: 'session' }>;
type CommitItem = Extract<Item, { kind: 'commit' }>;

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const dayName = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
const plural = (n: number, word: string) => `${num(n)} ${word}${n === 1 ? '' : 's'}`;

function Toggle<T extends string>({ options, value, onChange, label }: {
  options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex border border-line-2 rounded-md overflow-hidden text-[12px]">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}
          className={`px-2.5 py-[5px] ${o.value === value ? 'bg-bg-3 text-ink-0' : 'text-ink-1 hover:text-ink-0'}`}>{o.label}</button>
      ))}
    </div>
  );
}

function SessionCard({ s, focused }: { s: SessionItem; focused: boolean }) {
  const [all, setAll] = useState(false);
  const shown = all ? s.commits : s.commits.slice(0, 6);
  return (
    <article data-session-id={s.session_id} data-focused={focused}
      className={`rounded-lg border px-4 py-3 flex flex-col gap-2 ${focused ? 'border-accent/60 bg-accent/10' : 'border-s1/30 bg-s1/5'}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-0.5 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] tracking-[0.12em] text-[#8ab8ff]">CLAUDE SESSION</span>
            <b className="text-[13px] truncate">{s.title ?? 'Untitled session'}</b>
            {s.branch && <span className="font-mono text-[10px] px-1.5 rounded-full border border-line-2 text-ink-1">{s.branch}</span>}
          </div>
          <span className="text-[11px] text-ink-1">
            {time(s.first_ts)} → {time(s.last_ts)} · {s.model ?? '—'} · {num(s.turns)} turns · {tok(s.output_tokens)} tokens · {usd(s.cost)} · {hours(s.active_ms, s.active_estimated)}
            {s.tool_errors > 0 && ` · ${s.tool_errors} tool errors`}
          </span>
        </div>
        <Link to="/sessions/$id" params={{ id: s.session_id }} search={{ tab: 'overview' }} className="text-[12px] shrink-0">Open session</Link>
      </div>
      {s.commits.length > 0 && (
        <div className="flex flex-col gap-1 border-t border-line pt-2 text-[12px]">
          {shown.map((c) => (
            <div key={c.sha} className="grid grid-cols-[64px_minmax(0,1fr)_auto_auto] gap-x-3 items-center">
              <span className="font-mono text-ink-2">{c.sha.slice(0, 7)}</span>
              <span className="truncate">{c.subject}</span>
              <span className="font-mono text-[11px]"><span className="text-[#5fd3a6]">+{num(c.added)}</span> <span className="text-[#e5707a]">−{num(c.deleted)}</span></span>
              <span className={`text-[10px] px-1.5 rounded-full ${EVIDENCE[c.evidence].className}`}>{EVIDENCE[c.evidence].label}</span>
            </div>
          ))}
          {s.commits.length > 6 && (
            <button type="button" onClick={() => setAll((v) => !v)} className="self-start text-[12px] text-accent-ink">
              {all ? 'Show fewer' : `Show ${s.commits.length - 6} more commits`}
            </button>
          )}
        </div>
      )}
    </article>
  );
}

/** Commits no Claude session is tied to (merges, manual work): one line per day, open on demand. */
function Outside({ commits }: { commits: CommitItem[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-line bg-bg-1">
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-4 py-2.5 text-[12px] text-ink-1 hover:text-ink-0 flex items-center gap-2">
        <span aria-hidden className="text-[10px]">{open ? '▾' : '▸'}</span>
        <span><b className="text-ink-0 font-medium">{plural(commits.length, 'commit')}</b> made outside Claude sessions (merges, manual commits)</span>
      </button>
      {open && (
        <div className="px-4 pb-3 pl-9 flex flex-col gap-1 text-[12px]">
          {commits.map((c) => (
            <div key={c.sha} className="grid grid-cols-[44px_64px_minmax(0,1fr)_auto] gap-x-3 items-center">
              <span className="font-mono text-ink-2">{time(c.authored_at)}</span>
              <span className="font-mono text-ink-2">{c.sha.slice(0, 7)}</span>
              <span className="truncate">{c.subject}</span>
              <span className="font-mono text-[11px]"><span className="text-[#5fd3a6]">+{num(c.added)}</span> <span className="text-[#e5707a]">−{num(c.deleted)}</span></span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** What happened in a project, day by day: Claude sessions with their commits, everything else folded. */
export default function ActivityTab({ repo, days = 30, focus }: { repo: number; days?: number; focus?: string }) {
  const [kind, setKind] = useState<'all' | 'sessions'>('all');
  const [scope, setScope] = useState<Scope>('mine');
  const [branch, setBranch] = useState<string>('');
  const q = useWorkTimeline(repo, { days }, { kind, scope, branch: branch || undefined });
  const focused = new Set(focus ? focus.split(',') : []);
  const list = useRef<HTMLDivElement>(null);
  // Arriving from a piece of work: bring its session into view once loaded.
  useEffect(() => {
    list.current?.querySelector('[data-focused="true"]')?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }, [q.data, focus]);
  // Branches seen in what is shown, plus the chosen one so the select can always show it.
  const branches = [...new Set([
    ...(q.data?.days ?? []).flatMap((d) => d.items.flatMap((i) => (i.kind === 'session' && i.branch ? [i.branch] : []))),
    ...(branch ? [branch] : []),
  ])].sort();
  if (q.error) return <ErrorPanel error={q.error} />;
  return (
    <div ref={list} className="flex flex-col gap-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Toggle label="Show" value={kind} onChange={setKind}
          options={[{ value: 'all', label: 'Everything' }, { value: 'sessions', label: 'Claude sessions only' }]} />
        <Toggle label="Authors" value={scope} onChange={setScope}
          options={[{ value: 'mine', label: 'You + Claude' }, { value: 'all', label: 'All authors' }]} />
        <label className="flex items-center gap-2 text-[12px] text-ink-1">Branch
          <select value={branch} onChange={(e) => setBranch(e.target.value)}
            className="bg-bg-1 text-ink-0 border border-line-2 rounded-md px-2 py-1">
            <option value="">All branches</option>
            {branches.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
      </div>
      {q.data && q.data.days.length === 0 && <EmptyState title={`Nothing in ${periodLabel(days)}`} />}
      {q.data?.days.map((d) => {
        const sessions = d.items.filter((i): i is SessionItem => i.kind === 'session');
        const outside = d.items.filter((i): i is CommitItem => i.kind === 'commit');
        const commits = outside.length + sessions.reduce((n, s) => n + s.commits.length, 0);
        const totals = sessions.length
          ? `${plural(sessions.length, 'session')} · ${tok(sessions.reduce((n, s) => n + s.output_tokens, 0))} tokens · ${usd(sessions.reduce((n, s) => n + s.cost, 0))} · ${plural(commits, 'commit')}`
          : `no new Claude sessions · ${plural(commits, 'commit')}`;
        return (
          <section key={d.day} className="flex flex-col gap-2">
            <div className="flex items-baseline gap-3 border-b border-line pb-1.5">
              <h3 className="m-0 text-[13px] font-semibold">{dayName(d.day)}</h3>
              <span className="text-[12px] text-ink-2">{totals}</span>
            </div>
            {sessions.map((s) => <SessionCard key={s.session_id} s={s} focused={focused.has(s.session_id)} />)}
            {outside.length > 0 && <Outside commits={outside} />}
          </section>
        );
      })}
    </div>
  );
}
