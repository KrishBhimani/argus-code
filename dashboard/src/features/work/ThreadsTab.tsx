import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from '@tanstack/react-router';
import { Chip } from '@/components/ui/Chip';
import { Seg } from '@/components/ui/Seg';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { ChartWithTable } from '@/components/charts/ChartTable';
import { ago, num, tok, usd } from '@/lib/format/format';
import { tzOffsetMin } from '@/lib/api/client';
import { useWorkOverview, type Scope, type StoryT } from './api';
import { DayColumns, type Sel } from './DayColumns';
import { EVIDENCE, EVIDENCE_KEY, OPEN_STATES, hours } from './fmt';
import { StateBadge } from './ThreadCard';
import { selRange } from './range';
import { periodLabel } from './PeriodChips';

type Metric = 'tokens' | 'cost' | 'time';
const METRIC_TABS: { value: Metric; label: string }[] = [
  { value: 'tokens', label: 'Tokens' }, { value: 'cost', label: 'Cost' }, { value: 'time', label: 'Time' },
];
const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
const dayTime = (iso: string) => new Date(iso).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const newest = (xs: StoryT[]) => [...xs].sort((a, b) => b.last_ts.localeCompare(a.last_ts));

/** A thread opened in place: its sessions, commits and PRs. */
function Details({ s }: { s: StoryT }) {
  return (
    <div className="px-3 pb-3 flex flex-col gap-2.5 text-[12px]">
      {s.whole && (
        <p className="m-0 text-ink-2">
          part of a longer session ({day(s.whole.first_ts)} – {day(s.whole.last_ts)}): {tok(s.whole.output_tokens)} tokens · {usd(s.whole.cost)}
        </p>
      )}
      {s.session_list.map((x) => (
        <div key={x.session_id} className="flex items-center justify-between gap-3 rounded-md border border-line bg-bg-1 px-2.5 py-1.5">
          <span className="text-ink-1 min-w-0 truncate">
            <b className="text-ink-0 font-medium">{x.title ?? 'Untitled session'}</b> · {x.model ?? '—'} · {num(x.turns)} turns · {dayTime(x.first_ts)} · {hours(x.active_ms, x.active_estimated)}
          </span>
          <Link to="/sessions/$id" params={{ id: x.session_id }} search={{ tab: 'overview' }} className="shrink-0">Open session</Link>
        </div>
      ))}
      {s.commit_list.length > 0 && (
        <div className="flex flex-col gap-1">
          {s.commit_list.slice(0, 8).map((c) => (
            <div key={c.sha} className="grid grid-cols-[64px_minmax(0,1fr)_auto] gap-x-3 items-center">
              <span className="font-mono text-ink-2">{c.sha.slice(0, 7)}</span>
              <span className="truncate">{c.subject}</span>
              <span className={`text-[10px] px-1.5 rounded-full ${EVIDENCE[c.evidence].className}`}>{EVIDENCE[c.evidence].label}</span>
            </div>
          ))}
          {s.commit_list.length > 8 && <span className="text-ink-2">and {s.commit_list.length - 8} more in Activity</span>}
        </div>
      )}
      {s.prs.length > 0 && <p className="m-0 text-ink-1">PRs · {s.prs.map((p) => `#${p}`).join(' ')}</p>}
    </div>
  );
}

function ThreadRow({ s, focused }: { s: StoryT; focused: boolean }) {
  const [open, setOpen] = useState(focused);
  return (
    <div data-focused={focused} className={`rounded-md border ${focused ? 'border-accent/60 bg-accent/10' : 'border-line bg-bg-2'}`}>
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="w-full text-left px-3 py-2.5 flex flex-col gap-1">
        <span className="flex items-start justify-between gap-2">
          <b className="text-[13px] font-medium leading-snug">{s.title}</b>
          <StateBadge state={s.state} />
        </span>
        {s.branch && <span className="font-mono text-[10px] text-ink-2">{s.branch}</span>}
        <span className="text-[11px] text-ink-1">{s.reason} · {usd(s.cost)} · {ago(s.last_ts)}</span>
      </button>
      {open && <Details s={s} />}
    </div>
  );
}

function Column({ name, note, children }: { name: string; note: string; children: ReactNode }) {
  return (
    <section aria-label={name} className="rounded-lg border border-line bg-bg-1 flex flex-col min-w-0">
      <header className="px-3.5 py-2.5 border-b border-line flex items-baseline justify-between">
        <span className="text-[11px] tracking-[0.1em] uppercase">{name}</span>
        <span className="text-[11px] text-ink-2">{note}</span>
      </header>
      <div className="p-2.5 flex flex-col gap-2">{children}</div>
    </section>
  );
}

/** A project's threads by outcome, with the daily output one click away. */
export default function ThreadsTab({ repo, scope = 'mine', days: period = 30, focus }: { repo: number; scope?: Scope; days?: number; focus?: string }) {
  const [sel, setSel] = useState<Sel>(null);
  const [metric, setMetric] = useState<Metric>('tokens');
  const [chart, setChart] = useState(false);
  const [showDropped, setShowDropped] = useState(false);
  const base = useWorkOverview(repo, { days: period }, scope);
  const dayList = base.data?.daily.days;
  const range = useMemo(() => selRange(dayList ?? [], sel, tzOffsetMin()), [dayList, sel]);
  const list = useRef<HTMLDivElement>(null);
  // Arriving from Resume: bring the thread into view once loaded.
  useEffect(() => {
    list.current?.querySelector('[data-focused="true"]')?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }, [base.data, focus]);
  if (base.error) return <ErrorPanel error={base.error} />;
  if (!base.data) return null;
  const { tiles, prior, daily } = base.data;
  const all = base.data.stories;
  // A brush narrows to the threads active in those days. Their states stay the ones read over the
  // whole period: re-reading a range would clip a thread's commits and change its state.
  const shown = newest(range.from && range.to
    ? all.filter((s) => s.first_ts < range.to! && s.last_ts >= range.from!) : all);
  // Unknown sits with Open: it needs a look, and is never counted as shipped or dropped.
  const isOpen = (s: StoryT) => OPEN_STATES.has(s.state) || s.state === 'unknown';
  const open = shown.filter(isOpen);
  const shipped = shown.filter((s) => s.state === 'shipped');
  const dropped = shown.filter((s) => s.state === 'dropped');
  const shippedN = all.filter((s) => s.state === 'shipped').length;
  const droppedAll = all.filter((s) => s.state === 'dropped');
  const spent = (xs: StoryT[]) => usd(xs.reduce((n, s) => n + s.cost, 0));
  const change = (cur: number, prev: number) => (period === 0 || !prev ? null
    : `${cur >= prev ? '↑' : '↓'} ${Math.round(Math.abs((cur - prev) / prev) * 100)}% vs previous ${period} days`);
  const stats = [
    { label: 'Shipped', value: num(shippedN), note: periodLabel(period) },
    { label: 'Open', value: num(all.filter((s) => OPEN_STATES.has(s.state)).length), note: 'not on main yet' },
    { label: 'Dropped', value: num(droppedAll.length), note: `${spent(droppedAll)} spent` },
    { label: 'Spent', value: usd(tiles.cost), note: change(tiles.cost, prior.cost) },
    { label: 'Per shipped', value: shippedN ? usd(tiles.cost / shippedN) : '—', note: 'all spend ÷ shipped' },
  ];
  const m = {
    tokens: { aria: 'Output tokens by day', values: daily.output_tokens, format: (v: number) => tok(v) },
    cost: { aria: 'Cost by day', values: daily.cost, format: (v: number) => usd(v) },
    time: { aria: 'Active hours by day', values: daily.active_ms, format: (v: number) => hours(v) },
  }[metric];
  const table = {
    columns: ['Day', 'Output tokens', 'Cost', 'Active', 'Commits'],
    rows: daily.days.map((d, i) => [d, tok(daily.output_tokens[i]), usd(daily.cost[i]), hours(daily.active_ms[i]), daily.commits[i]]),
  };
  const rows = (xs: StoryT[]) => (xs.length ? xs.map((s) => <ThreadRow key={s.key} s={s} focused={s.key === focus} />)
    : <p className="m-0 text-[12px] text-ink-2 p-2">None</p>);
  return (
    <div ref={list} className="flex flex-col gap-4">
      <div role="group" aria-label="Outcomes" className="grid grid-cols-5 rounded-lg border border-line bg-bg-1">
        {stats.map((s, i) => (
          <div key={s.label} className={`px-5 py-3 flex flex-col gap-0.5 ${i ? 'border-l border-line' : ''}`}>
            <span className="text-[12px] text-ink-1">{s.label}</span>
            <span className="font-mono text-[20px]">{s.value}</span>
            <span className="text-[11px] text-ink-2">{s.note ?? ''}</span>
          </div>
        ))}
      </div>

      {sel && <div><Chip active onClick={() => setSel(null)}>{`${daily.days[sel.a]} – ${daily.days[sel.b]} ✕`}</Chip></div>}
      <div className="grid grid-cols-3 gap-3 items-start">
        <Column name="Open" note={`${open.length}`}>{rows(open)}</Column>
        <Column name="Shipped" note={`${shipped.length}`}>{rows(shipped)}</Column>
        <Column name="Dropped" note={spent(dropped)}>
          <button type="button" aria-expanded={showDropped} onClick={() => setShowDropped((v) => !v)}
            className="text-left text-[12px] text-ink-1 hover:text-ink-0 px-1">
            {showDropped ? 'Hide' : 'Show'} {dropped.length} dropped · {spent(dropped)} spent
          </button>
          {showDropped && rows(dropped)}
        </Column>
      </div>
      <p className="m-0 text-[11px] text-ink-2">How a commit is tied to the work: {EVIDENCE_KEY}</p>

      <section className="rounded-lg border border-line bg-bg-1 px-5 py-3 flex flex-col gap-3">
        <button type="button" aria-expanded={chart} onClick={() => setChart((v) => !v)} className="text-left flex items-center gap-2">
          <span aria-hidden className="text-[10px] text-ink-2">{chart ? '▾' : '▸'}</span>
          <b className="text-[13px]">Daily output</b>
          <span className="text-[11px] text-ink-2">drag across days to narrow the threads above</span>
        </button>
        {chart && (
          <>
            <div className="flex justify-end"><Seg options={METRIC_TABS} value={metric} onChange={setMetric} /></div>
            <ChartWithTable table={table} chart={
              <div className="flex flex-col gap-2">
                <DayColumns key={metric} label={m.aria} days={daily.days} values={m.values} format={m.format} sel={sel} onSel={setSel} />
                <DayColumns label="Commits by day" days={daily.days} values={daily.commits} format={(v) => `${v} commits`} sel={sel} onSel={setSel} height={32} color="#4fc59a" />
              </div>
            } />
          </>
        )}
      </section>
    </div>
  );
}
