import { useMemo, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { Chip } from '@/components/ui/Chip';
import { Seg } from '@/components/ui/Seg';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { ChartWithTable } from '@/components/charts/ChartTable';
import { ago, num, tok, usd } from '@/lib/format/format';
import { tzOffsetMin } from '@/lib/api/client';
import { useWorkOverview, type Scope, type StoryT } from './api';
import { DayColumns, type Sel } from './DayColumns';
import { EVIDENCE, EVIDENCE_KEY, hours } from './fmt';
import { selRange } from './range';
import { periodLabel } from './PeriodChips';

/** What the day chart measures. Tokens by default: the archive has them for every
 *  session, while time needs the transcript (or is estimated from turn gaps). */
type Metric = 'tokens' | 'cost' | 'time';
const METRIC_TABS: { value: Metric; label: string }[] = [
  { value: 'tokens', label: 'Tokens' }, { value: 'cost', label: 'Cost' }, { value: 'time', label: 'Time' },
];
const COLS = 'grid grid-cols-[16px_minmax(0,1fr)_110px_70px_80px_80px_70px_120px] gap-x-3 items-center';
const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
const dayTime = (iso: string) => new Date(iso).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const span = (a: string, b: string) => (day(a) === day(b) ? day(a) : `${day(a)} – ${day(b)}`);
const plural = (n: number, word: string) => `${num(n)} ${word}${n === 1 ? '' : 's'}`;

function ChartLabel({ name, peak }: { name: string; peak: string }) {
  return (
    <div className="flex items-baseline justify-between text-[10px] text-ink-2">
      <span className="tracking-[0.08em] uppercase">{name}</span>
      <span>{peak}</span>
    </div>
  );
}

/** The newest piece of work in the period, with the two ways back into it. */
function LeftOff({ s, repo }: { s: StoryT; repo: number }) {
  const lastSession = s.session_ids[s.session_ids.length - 1];
  const facts = [
    s.branch && `on ${s.branch}`, `last active ${ago(s.last_ts)}`, plural(s.commits.total, 'commit'),
    s.prs.length > 0 && `${plural(s.prs.length, 'PR')}, latest #${Math.max(...s.prs)}`,
  ].filter(Boolean).join(' · ');
  return (
    <div className="rounded-lg border border-accent/30 bg-accent/5 px-5 py-4 flex items-center justify-between gap-6">
      <div className="flex flex-col gap-1 min-w-0">
        <span className="text-[10px] tracking-[0.12em] text-accent-ink">WHERE YOU LEFT OFF</span>
        <b className="text-[16px] truncate">{s.title}</b>
        <span className="text-[12px] text-ink-1">{facts}</span>
      </div>
      <div className="flex gap-2 shrink-0 text-[13px]">
        <Link to="/projects/$repo" params={{ repo: String(repo) }} search={{ tab: 'activity', focus: s.session_ids.join(',') }}
          className="rounded-md border border-line-2 px-3 py-2 text-ink-0">See it in Activity</Link>
        <Link to="/sessions/$id" params={{ id: lastSession }} search={{ tab: 'overview' }}
          className="rounded-md bg-accent px-3 py-2 font-semibold text-bg-0">Open last session</Link>
      </div>
    </div>
  );
}

/** One piece of work: a row that opens in place to its sessions, commits and PRs. */
function PieceRow({ s, repo }: { s: StoryT; repo: number }) {
  const [open, setOpen] = useState(false);
  const toActivity = { tab: 'activity' as const, focus: s.session_ids.join(',') };
  return (
    <div className={`border-b border-line ${open ? 'bg-bg-2' : ''}`}>
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)}
        className={`${COLS} w-full text-left px-4 py-3 hover:bg-bg-2 cursor-pointer`}>
        <span aria-hidden className="text-ink-2 text-[10px]">{open ? '▾' : '▸'}</span>
        <span className="flex items-center gap-2 min-w-0">
          <span className={`truncate text-[13px] ${open ? 'font-semibold' : ''}`}>{s.title}</span>
          {s.branch && <span className="font-mono text-[10px] px-1.5 rounded-full border border-line-2 text-ink-1 shrink-0">{s.branch}</span>}
        </span>
        <span className="text-[12px] text-ink-1">{span(s.first_ts, s.last_ts)}</span>
        <span className="text-right font-mono text-[12px]">{s.sessions}</span>
        <span className="text-right font-mono text-[12px]">{tok(s.output_tokens)}</span>
        <span className="text-right font-mono text-[12px]">{usd(s.cost)}</span>
        <span className={`text-right font-mono text-[12px] ${s.commits.total ? '' : 'text-ink-2'}`}>{s.commits.total ? num(s.commits.total) : 'none'}</span>
        <span className="text-right font-mono text-[12px]">
          {s.commits.total > 0 && <><span className="text-[#5fd3a6]">+{num(s.added)}</span> <span className="text-[#e5707a]">−{num(s.deleted)}</span></>}
        </span>
      </button>
      {open && (
        <div className="pl-11 pr-4 pb-4 flex flex-col gap-3 text-[12px]">
          {s.whole && (
            <p className="text-ink-2">
              part of a longer session ({day(s.whole.first_ts)} – {day(s.whole.last_ts)}): {tok(s.whole.output_tokens)} tokens · {usd(s.whole.cost)}; this range shows {tok(s.output_tokens)} tokens · {usd(s.cost)}
            </p>
          )}
          {s.session_list.map((x) => (
            <div key={x.session_id} className="flex items-center justify-between gap-4 rounded-md border border-line bg-bg-1 px-3 py-2">
              <span className="text-ink-1 min-w-0 truncate">
                <b className="text-ink-0 font-medium">{x.title ?? 'Untitled session'}</b> · {x.model ?? '—'} · {num(x.turns)} turns · {dayTime(x.first_ts)} → {dayTime(x.last_ts)} · {hours(x.active_ms, x.active_estimated)} of Claude time
              </span>
              <Link to="/sessions/$id" params={{ id: x.session_id }} search={{ tab: 'overview' }} className="shrink-0">Open session</Link>
            </div>
          ))}
          {s.commit_list.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-ink-1"><b className="text-ink-0 font-medium">Commits</b> · {num(s.commit_list.length)}{s.commit_list.length > 5 ? ' · showing 5' : ''}</span>
              {s.commit_list.slice(0, 5).map((c) => (
                <div key={c.sha} className="grid grid-cols-[64px_minmax(0,1fr)_auto] gap-x-3 items-center">
                  <span className="font-mono text-ink-2">{c.sha.slice(0, 7)}</span>
                  <span className="truncate">{c.subject}</span>
                  <span className={`text-[10px] px-1.5 rounded-full ${EVIDENCE[c.evidence].className}`}>{EVIDENCE[c.evidence].label}</span>
                </div>
              ))}
              {s.commit_list.length > 5 && (
                <Link to="/projects/$repo" params={{ repo: String(repo) }} search={toActivity}>All {s.commit_list.length} commits in Activity</Link>
              )}
            </div>
          )}
          {s.prs.length > 0 && <p className="text-ink-1">PRs · {s.prs.map((p) => `#${p}`).join(' ')}</p>}
        </div>
      )}
    </div>
  );
}

export default function OverviewTab({ repo, scope = 'mine', days: period = 30 }: { repo: number; scope?: Scope; days?: number }) {
  const [sel, setSel] = useState<Sel>(null);
  const [branch, setBranch] = useState<string | null>(null);
  const [metric, setMetric] = useState<Metric>('tokens');
  const base = useWorkOverview(repo, { days: period }, scope);
  const days = base.data?.daily.days ?? [];
  const range = useMemo(() => selRange(days, sel, tzOffsetMin()), [days, sel]);
  const focused = useWorkOverview(repo, range, scope);
  const detail = sel ? focused.data : base.data;
  const loading = sel !== null && !focused.data && !focused.error;
  if (base.error) return <ErrorPanel error={base.error} />;
  if (!base.data) return null;
  const { tiles, prior, daily } = base.data;
  const newest = (xs: StoryT[]) => [...xs].sort((a, b) => b.last_ts.localeCompare(a.last_ts));
  const latest = newest(base.data.stories)[0];
  const pieces = newest(detail?.stories ?? []).filter((s) => !branch || s.branch === branch);
  const label = sel ? `${daily.days[sel.a]} – ${daily.days[sel.b]}` : periodLabel(period);
  // All time has no earlier period to compare with.
  const change = (cur: number, prev: number) => {
    if (period === 0 || !prev) return null;
    const pct = (cur - prev) / prev;
    return `${pct > 0 ? '↑' : pct < 0 ? '↓' : '='} ${Math.round(Math.abs(pct) * 100)}% vs previous ${period} days`;
  };
  const stats = [
    { label: 'Claude output', value: tok(daily.output_tokens.reduce((a, b) => a + b, 0)), note: 'tokens written' },
    { label: 'Cost', value: usd(tiles.cost), note: change(tiles.cost, prior.cost) },
    { label: 'Commits landed', value: num(tiles.commits), note: change(tiles.commits, prior.commits) ?? (scope === 'mine' ? 'yours + Claude' : 'all authors') },
    { label: 'Claude time', value: hours(tiles.active_ms, tiles.active_estimated), note: change(tiles.active_ms, prior.active_ms) ?? 'working, not wall clock' },
  ];
  const m = {
    tokens: { name: "Claude's output (tokens)", aria: 'Output tokens by day', values: daily.output_tokens, format: (v: number) => tok(v) },
    cost: { name: 'Cost ($)', aria: 'Cost by day', values: daily.cost, format: (v: number) => usd(v) },
    time: { name: "Claude's working time", aria: 'Active hours by day', values: daily.active_ms, format: (v: number) => hours(v) },
  }[metric];
  const table = {
    columns: ['Day', 'Output tokens', 'Cost', 'Active', 'Commits'],
    rows: daily.days.map((d, i) => [d, tok(daily.output_tokens[i]), usd(daily.cost[i]), hours(daily.active_ms[i]), daily.commits[i]]),
  };
  return (
    <div className="flex flex-col gap-4">
      {latest && <LeftOff s={latest} repo={repo} />}

      <div className="grid grid-cols-4 rounded-lg border border-line bg-bg-1">
        {stats.map((s, i) => (
          <div key={s.label} className={`px-5 py-3 flex flex-col gap-0.5 ${i ? 'border-l border-line' : ''}`}>
            <span className="text-[12px] text-ink-1">{s.label}</span>
            <span className="font-mono text-[20px]">{s.value}</span>
            <span className="text-[11px] text-ink-2">{s.note ?? ''}</span>
          </div>
        ))}
      </div>

      <section className="rounded-lg border border-line bg-bg-1 px-5 py-4 flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex flex-col">
            <b className="text-[13px]">What Claude produced, and what landed, per day</b>
            <span className="text-[11px] text-ink-2">Drag across days to narrow the work list below</span>
          </div>
          <div className="flex items-center gap-2">
            {sel && <Chip active onClick={() => setSel(null)}>{label} ✕</Chip>}
            <Seg options={METRIC_TABS} value={metric} onChange={setMetric} />
          </div>
        </div>
        <ChartWithTable table={table} chart={
          <div className="flex flex-col gap-2">
            <ChartLabel name={m.name} peak={`peak ${m.format(Math.max(0, ...m.values))}`} />
            <DayColumns key={metric} label={m.aria} days={daily.days} values={m.values} format={m.format} sel={sel} onSel={setSel} />
            <ChartLabel name="Commits landed" peak={`${num(daily.commits.reduce((a, b) => a + b, 0))} in ${periodLabel(period).replace('last ', '')}`} />
            <DayColumns label="Commits by day" days={daily.days} values={daily.commits} format={(v) => `${v} commits`} sel={sel} onSel={setSel} height={32} color="#4fc59a" />
          </div>
        } />
      </section>

      <section className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <h2 className="m-0 text-[14px] font-semibold">{plural(pieces.length, 'piece')} of work{sel ? ` · ${label}` : ''}</h2>
          <div className="flex gap-1.5 flex-wrap">
            <Chip active={branch === null} onClick={() => setBranch(null)}>All branches</Chip>
            {base.data.breakdowns.branches.map((b) => (
              <Chip key={b.name} active={branch === b.name} onClick={() => setBranch(b.name)}>{`${b.name} · ${usd(b.value)}`}</Chip>
            ))}
          </div>
        </div>
        <div className="rounded-lg border border-line bg-bg-1 overflow-hidden">
          <div className={`${COLS} px-4 py-2 border-b border-line text-[10px] tracking-[0.08em] text-ink-2`}>
            <span /><span>WORK</span><span>WHEN</span><span className="text-right">SESSIONS</span><span className="text-right">TOKENS</span>
            <span className="text-right">COST</span><span className="text-right">COMMITS</span><span className="text-right">LINES</span>
          </div>
          {loading ? <div className="text-[12px] text-ink-2 py-6 text-center">Loading {label}…</div>
            : pieces.length ? pieces.map((s) => <PieceRow key={s.session_ids.join()} s={s} repo={repo} />)
              : <EmptyState title="No work in this range" />}
        </div>
        <p className="text-[11px] text-ink-2">How a commit is tied to the work: {EVIDENCE_KEY}</p>
      </section>
    </div>
  );
}
