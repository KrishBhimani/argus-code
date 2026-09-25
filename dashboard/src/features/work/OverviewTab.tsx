import { useMemo, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { Tile } from '@/components/ui/Tile';
import { Panel } from '@/components/ui/Panel';
import { Chip } from '@/components/ui/Chip';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { Bars } from '@/components/charts/Bars';
import { ChartWithTable } from '@/components/charts/ChartTable';
import { delta } from '@/lib/analysis/deltas';
import { Seg } from '@/components/ui/Seg';
import { num, pct, tok, usd } from '@/lib/format/format';
import { tzOffsetMin } from '@/lib/api/client';
import { useWorkOverview, type Scope, type StoryT } from './api';
import { DayColumns, type Sel } from './DayColumns';
import { hours } from './fmt';
import { selRange } from './range';
import { periodLabel } from './PeriodChips';

/** What the top day chart measures. Tokens by default: the archive has them for every
 *  session, while time needs the transcript (or is estimated from turn gaps). */
type Metric = 'tokens' | 'cost' | 'time';
const METRIC_TABS: { value: Metric; label: string }[] = [
  { value: 'tokens', label: 'Tokens' }, { value: 'cost', label: 'Cost' }, { value: 'time', label: 'Time' },
];

function ChartLabel({ name, peak }: { name: string; peak: string }) {
  return (
    <div className="flex items-baseline justify-between text-[10px] text-ink-2">
      <span className="tracking-[0.08em] uppercase">{name}</span>
      <span>peak {peak}</span>
    </div>
  );
}

function StoryCard({ s, repo }: { s: StoryT; repo: number }) {
  const ev = [s.commits.exact && `${s.commits.exact} exact`, s.commits.coauthored && `${s.commits.coauthored} co-authored`, s.commits.inferred && `${s.commits.inferred} inferred`].filter(Boolean).join(' · ');
  return (
    <article className="border-l-2 border-line-2 hover:border-accent pl-3 py-1">
      <div className="flex items-center gap-2 flex-wrap">
        <b className="text-[13px]">{s.title}</b>
        {s.branch && <span className="font-mono text-[10px] px-1.5 rounded-full border border-line-2 text-ink-1">{s.branch}</span>}
        {s.prs.map((p) => <span key={p} className="text-[10px] px-1.5 rounded-full border border-line-2 text-ink-1">PR #{p}</span>)}
      </div>
      <div className="text-[11px] text-ink-2">{s.sessions} session{s.sessions === 1 ? '' : 's'} · {tok(s.output_tokens)} tokens · {hours(s.active_ms, s.active_estimated)} active · {usd(s.cost)}</div>
      <div className="text-[11px] text-ink-1">
        {s.commits.total === 0 ? 'no commits: research' : <>
          <span>{num(s.commits.total)} commits</span> <span className="text-ink-2">({ev})</span> · +{num(s.added)} / −{num(s.deleted)} · {num(s.files)} files
        </>}
      </div>
      <Link to="/projects/$repo" params={{ repo: String(repo) }} search={{ tab: 'timeline' }} className="text-[11px]">Open in Timeline →</Link>
    </article>
  );
}

export default function OverviewTab({ repo, scope = 'mine', days: period = 30 }: { repo: number; scope?: Scope; days?: number }) {
  const [sel, setSel] = useState<Sel>(null);
  const [branch, setBranch] = useState<string | null>(null);
  const [skill, setSkill] = useState<string | null>(null);
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
  // All time has no earlier period to compare with.
  const vs = (cur: number, prev: number) => (period === 0 ? null : delta(cur, prev));
  const stories = (detail?.stories ?? []).filter((s) => (!branch || s.branch === branch) && (!skill || s.skills.includes(skill)));
  const table = {
    columns: ['Day', 'Output tokens', 'Cost', 'Active', 'Commits'],
    rows: daily.days.map((d, i) => [d, tok(daily.output_tokens[i]), usd(daily.cost[i]), hours(daily.active_ms[i]), daily.commits[i]]),
  };
  const m = {
    tokens: { name: "Claude's output (tokens)", aria: 'Output tokens by day', values: daily.output_tokens, format: (v: number) => tok(v) },
    cost: { name: 'Cost ($)', aria: 'Cost by day', values: daily.cost, format: (v: number) => usd(v) },
    time: { name: "Claude's working time", aria: 'Active hours by day', values: daily.active_ms, format: (v: number) => hours(v) },
  }[metric];
  const label = sel ? `${daily.days[sel.a]} – ${daily.days[sel.b]}` : periodLabel(period);
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-4 gap-3">
        <Tile label="Active time" value={hours(tiles.active_ms, tiles.active_estimated)} sub="Claude working, not wall clock" delta={vs(tiles.active_ms, prior.active_ms)} />
        <Tile label="Est. cost" value={usd(tiles.cost)} delta={vs(tiles.cost, prior.cost)} upIsBad />
        <Tile label="Commits" value={num(tiles.commits)} sub={scope === 'mine' ? 'yours + agent' : 'all authors'} delta={vs(tiles.commits, prior.commits)} />
        <Tile label="Cost per commit" value={tiles.cost_per_commit === null ? '—' : usd(tiles.cost_per_commit)} delta={vs(tiles.cost_per_commit ?? 0, prior.cost_per_commit ?? 0)} upIsBad />
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_240px] gap-4">
        <div className="flex flex-col gap-4 min-w-0">
          <Panel title="Claude's work vs commits, by day" sub="click a day or drag a range" right={
            <div className="flex items-center gap-2">
              <Seg options={METRIC_TABS} value={metric} onChange={setMetric} />
              {sel && <Chip active onClick={() => setSel(null)}>{label} ✕</Chip>}
            </div>
          }>
            <ChartWithTable table={table} chart={
              <div className="flex flex-col gap-2">
                <ChartLabel name={m.name} peak={m.format(Math.max(0, ...m.values))} />
                <DayColumns key={metric} label={m.aria} days={daily.days} values={m.values} format={m.format} sel={sel} onSel={setSel} />
                <ChartLabel name="Commits landed" peak={num(Math.max(0, ...daily.commits))} />
                <DayColumns label="Commits by day" days={daily.days} values={daily.commits} format={(v) => `${v} commits`} sel={sel} onSel={setSel} height={40} />
              </div>
            } />
          </Panel>
          <section className="flex flex-col gap-3">
            <div className="flex items-center gap-2 text-[10px] tracking-[0.08em] text-ink-2">
              WORK · {label.toUpperCase()} · {stories.length} PIECES
              {branch && <Chip active onClick={() => setBranch(null)}>branch: {branch} ✕</Chip>}
              {skill && <Chip active onClick={() => setSkill(null)}>skill: {skill} ✕</Chip>}
            </div>
            {loading ? <div className="text-[12px] text-ink-2 py-6 text-center">Loading {label}…</div>
              : stories.length ? stories.map((s) => <StoryCard key={s.session_ids.join()} s={s} repo={repo} />) : <EmptyState title="No work in this range" />}
          </section>
        </div>
        <aside className="flex flex-col gap-4">
          <Panel title="By branch">
            <div className="flex flex-col gap-1">
              {(detail?.breakdowns.branches ?? []).map((b) => (
                <button key={b.name} type="button" onClick={() => setBranch(b.name)} className="text-left font-mono text-[11px] text-ink-1 hover:text-ink-0">{b.name}</button>
              ))}
            </div>
            <Bars rows={detail?.breakdowns.branches ?? []} format={usd} className="mt-2" />
          </Panel>
          <Panel title="By skill / MCP" sub="share of cost">
            <div className="flex flex-col gap-1">
              {(detail?.breakdowns.skills ?? []).map((b) => (
                <button key={b.name} type="button" onClick={() => setSkill(b.name)} className="text-left text-[11px] text-ink-1 hover:text-ink-0 truncate">{b.name} · {pct(b.value, 0)}</button>
              ))}
            </div>
          </Panel>
          <Panel title="Files most changed"><Bars rows={detail?.breakdowns.files ?? []} format={(v) => `${v}×`} /></Panel>
        </aside>
      </div>
    </div>
  );
}
