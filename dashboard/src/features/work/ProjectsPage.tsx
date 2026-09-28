import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { TopBar, Page } from '@/app/shell/TopBar';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { ago, num, usd } from '@/lib/format/format';
import { useWorkProjects, useWorkThreads, type WorkProject } from './api';
import { PeriodChips, periodLabel } from './PeriodChips';
import { ThreadCard } from './ThreadCard';

/** A project with no Claude output, commits or time in the period folds away. */
const isQuiet = (p: WorkProject) => p.tokens === 0 && p.commits === 0 && p.active_ms === 0;
const SHOWN = 8;
const plural = (n: number, w: string) => `${num(n)} ${w}${n === 1 ? '' : 's'}`;
const pct = (v: number | null) => (v === null ? '—' : `${Math.round(v * 100)}%`);

function OutcomeBar({ o }: { o: WorkProject['outcomes'] }) {
  const total = o.shipped + o.open + o.dropped || 1;
  const w = (n: number) => `${(n / total) * 100}%`;
  return (
    <span className="flex flex-col gap-1.5">
      <span className="flex h-2 rounded-sm overflow-hidden bg-bg-3" aria-hidden>
        <span style={{ width: w(o.shipped) }} className="bg-[#4fc59a]" />
        <span style={{ width: w(o.open) }} className="bg-s1" />
        <span style={{ width: w(o.dropped) }} className="bg-line-2" />
      </span>
      <span className="text-[11px] text-ink-1">{o.shipped} shipped · {o.open} open · {o.dropped} dropped</span>
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span className="text-[10px] tracking-[0.06em] text-ink-2">{label}</span>
      <span className="font-mono text-[14px]">{value}</span>
    </span>
  );
}

function ProjectCard({ p, onOpen }: { p: WorkProject; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} title={p.root}
      className={`text-left rounded-lg border border-line bg-bg-1 hover:bg-bg-2 p-4 flex flex-col gap-3 ${isQuiet(p) ? 'opacity-60' : ''}`}>
      <span className="flex items-baseline justify-between gap-3">
        <b className="font-mono text-[14px] font-medium truncate">{p.display_name}</b>
        <span className="text-[11px] text-ink-2 shrink-0">{p.last_worked_at ? ago(p.last_worked_at) : '—'}</span>
      </span>
      {!p.present && <span className="text-[10px] text-ink-2">repo gone · archived</span>}
      {p.last_error && <span className="text-[10px] text-warn" title={p.last_error}>scan warning</span>}
      <OutcomeBar o={p.outcomes} />
      <span className="grid grid-cols-3 gap-2 border-t border-line pt-2.5">
        <Stat label="SPENT" value={usd(p.cost)} />
        <Stat label="PER SHIPPED" value={p.cost_per_shipped === null ? '—' : usd(p.cost_per_shipped)} />
        <Stat label="CLAUDE-WRITTEN" value={pct(p.claude_share)} />
      </span>
    </button>
  );
}

export default function ProjectsPage() {
  const [days, setDays] = useState(30);
  const [showQuiet, setShowQuiet] = useState(false);
  const [allOpen, setAllOpen] = useState(false);
  const projects = useWorkProjects(days);
  const threads = useWorkThreads(days);
  const nav = useNavigate();
  const all = projects.data?.projects ?? [];
  const active = all.filter((p) => !isQuiet(p));
  const quiet = all.filter(isQuiet);
  const open = threads.data?.open ?? [];
  const counts = threads.data?.counts;
  const inProjects = new Set(open.map((t) => t.project_id)).size;
  const error = projects.error ?? threads.error;
  return (
    <>
      <TopBar crumbs={['Work', 'Projects']}><PeriodChips days={days} onChange={setDays} /></TopBar>
      <Page>
        {error ? <ErrorPanel error={error} /> : (
          <>
            <div className="flex items-end justify-between gap-6 flex-wrap">
              <div className="flex flex-col gap-1">
                <h1 className="text-[20px] font-semibold">
                  {open.length ? `${plural(open.length, 'thread')} open across ${plural(inProjects, 'project')}` : 'Nothing open'}
                </h1>
                <p className="text-[13px] text-ink-1">A thread is a piece of work with Claude. It closes when it lands on main, or after 7 quiet days.</p>
              </div>
              {counts && (
                <p className="text-[12px] text-ink-2 whitespace-nowrap">
                  {periodLabel(days)} · <b className="text-[#5fd3a6] font-medium">{counts.shipped} shipped</b> · <b className="text-[#8ab8ff] font-medium">{counts.open} open</b> · <b className="text-ink-1 font-medium">{counts.dropped} dropped</b>
                </p>
              )}
            </div>
            {threads.data?.remote_as_of && (
              <p className="text-[11px] text-ink-2 -mt-2">Remote state as of your last fetch, {ago(threads.data.remote_as_of)}. Argus never fetches.</p>
            )}
            {open.length === 0 && !threads.isLoading ? (
              <EmptyState title="Nothing open" hint="Everything has shipped or gone quiet." />
            ) : (
              <div className="flex flex-col gap-2">
                <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
                  {(allOpen ? open : open.slice(0, SHOWN)).map((t) => <ThreadCard key={t.key} t={t} />)}
                </div>
                {open.length > SHOWN && (
                  <button type="button" onClick={() => setAllOpen((v) => !v)} className="self-start text-[12px] text-accent-ink">
                    {allOpen ? 'Show fewer' : `Show all ${open.length}`}
                  </button>
                )}
              </div>
            )}
            <section className="flex flex-col gap-3">
              <div className="flex items-baseline justify-between">
                <h2 className="m-0 text-[14px] font-semibold">Projects</h2>
                <span className="text-[12px] text-ink-2">most recent first</span>
              </div>
              {!projects.isLoading && all.length === 0 ? (
                <EmptyState title="No repos found yet" hint="The work scan runs every 5 minutes; `argus work scan` runs one now." />
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">
                  {(showQuiet ? [...active, ...quiet] : active).map((p) => (
                    <ProjectCard key={p.id} p={p}
                      onOpen={() => nav({ to: '/projects/$repo', params: { repo: String(p.id) }, search: { tab: 'threads' } })} />
                  ))}
                </div>
              )}
              {quiet.length > 0 && (
                <button type="button" onClick={() => setShowQuiet((v) => !v)} aria-expanded={showQuiet}
                  className="self-start text-[12px] text-ink-1 hover:text-ink-0">
                  {showQuiet ? 'Hide' : 'Show'} {quiet.length} quiet project{quiet.length === 1 ? '' : 's'}, no Claude activity in {days === 0 ? 'all time' : `the ${periodLabel(days)}`}
                </button>
              )}
            </section>
          </>
        )}
      </Page>
    </>
  );
}
