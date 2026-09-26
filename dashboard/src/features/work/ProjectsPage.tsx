import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { TopBar, Page } from '@/app/shell/TopBar';
import { Panel } from '@/components/ui/Panel';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { Sparkline } from '@/components/charts/Sparkline';
import { ago, num, tok, usd } from '@/lib/format/format';
import { useWorkProjects, type WorkProject } from './api';
import { PeriodChips } from './PeriodChips';

/** A project with no Claude output, commits or time in the period folds away. */
const isQuiet = (p: WorkProject) => p.tokens === 0 && p.commits === 0 && p.active_ms === 0;

export default function ProjectsPage() {
  const [days, setDays] = useState(30);
  const [showQuiet, setShowQuiet] = useState(false);
  const q = useWorkProjects(days);
  const nav = useNavigate();
  const all = q.data?.projects ?? [];
  const active = all.filter((p) => !isQuiet(p));
  const quiet = all.filter(isQuiet);
  const rows = showQuiet ? [...active, ...quiet] : active;
  const when = days === 0 ? 'all time' : `the last ${days} days`;
  const total = (k: 'tokens' | 'cost' | 'commits') => active.reduce((s, p) => s + p[k], 0);
  return (
    <>
      <TopBar crumbs={['Work', 'Projects']}><PeriodChips days={days} onChange={setDays} /></TopBar>
      <Page>
        {q.error ? <ErrorPanel error={q.error} /> : (
          <>
            <div className="flex items-end justify-between gap-6">
              <div className="flex flex-col gap-1">
                <h1 className="text-[20px] font-semibold">{days === 0 ? 'Where you worked, all time' : `Where you worked in the last ${days} days`}</h1>
                <p className="text-[13px] text-ink-1">{active.length} project{active.length === 1 ? '' : 's'} with Claude activity · most recent first</p>
              </div>
              {active.length > 0 && (
                <p className="text-[12px] text-ink-2 whitespace-nowrap">
                  Total: <b className="text-ink-0">{tok(total('tokens'))}</b> tokens · <b className="text-ink-0">{usd(total('cost'))}</b> · <b className="text-ink-0">{num(total('commits'))}</b> commits
                </p>
              )}
            </div>
            <Panel title="Projects" sub="one row per repo, however many folders it lives in" padded={false}>
              {!q.isLoading && all.length === 0 ? (
                <EmptyState title="No repos found yet" hint="The work scan runs every 5 minutes; `argus work scan` runs one now." />
              ) : (
                <table className="w-full text-xs">
                  <thead className="text-[10px] tracking-[0.08em] text-ink-2">
                    <tr className="border-b border-line">
                      <th className="text-left font-medium px-3.5 py-2">PROJECT</th>
                      <th className="text-left font-medium px-3.5">LAST PIECE OF WORK</th>
                      <th className="text-left font-medium px-3.5">CLAUDE OUTPUT / DAY</th>
                      <th className="text-right font-medium px-3.5">TOKENS</th>
                      <th className="text-right font-medium px-3.5">COST</th>
                      <th className="text-right font-medium px-3.5">COMMITS</th>
                      <th className="text-right font-medium px-3.5">LAST ACTIVE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((p) => (
                      <tr key={p.id} className={`border-b border-line hover:bg-bg-2 cursor-pointer ${isQuiet(p) ? 'opacity-60' : ''}`}
                        onClick={() => nav({ to: '/projects/$repo', params: { repo: String(p.id) }, search: { tab: 'overview' } })}>
                        <td className="px-3.5 py-2.5" title={p.root}>
                          <span className="font-mono">{p.display_name}</span>
                          {p.folders.length > 1 && (
                            <span className="ml-2 text-[10px] px-1.5 rounded-full border border-line-2 text-ink-2" title={p.folders.map((f) => f.root).join('\n')}>
                              {p.folders.length} folders
                            </span>
                          )}
                          {!p.present && <span className="ml-2 text-[10px] px-1.5 rounded-sm border border-line-2 text-ink-2">repo gone · archived</span>}
                          {p.last_error && <span className="ml-2 text-[10px] text-warn" title={p.last_error}>scan warning</span>}
                        </td>
                        <td className="px-3.5 max-w-[340px]">
                          {p.latest ? (
                            <div className="flex flex-col min-w-0">
                              <span className="truncate">{p.latest.title ?? 'Untitled session'}</span>
                              {p.latest.branch && <span className="font-mono text-[10px] text-ink-2">{p.latest.branch}</span>}
                            </div>
                          ) : <span className="text-ink-2">—</span>}
                        </td>
                        <td className="px-3.5"><Sparkline values={p.daily_tokens} width={120} height={22} /></td>
                        <td className="px-3.5 text-right font-mono num">{tok(p.tokens)}</td>
                        <td className="px-3.5 text-right font-mono num">{usd(p.cost)}</td>
                        <td className="px-3.5 text-right font-mono num">{num(p.commits)}</td>
                        <td className="px-3.5 text-right text-ink-1">{p.last_worked_at ? ago(p.last_worked_at) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {quiet.length > 0 && (
                <button type="button" onClick={() => setShowQuiet((v) => !v)} aria-expanded={showQuiet}
                  className="w-full text-left text-[12px] text-ink-1 hover:text-ink-0 px-3.5 py-2.5">
                  {showQuiet ? 'Hide' : 'Show'} {quiet.length} quiet project{quiet.length === 1 ? '' : 's'}, no Claude activity in {when}
                </button>
              )}
            </Panel>
          </>
        )}
      </Page>
    </>
  );
}
