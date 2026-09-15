import { useMemo, useState } from 'react';
import { TopBar, Page } from '@/app/shell/TopBar';
import { Chip } from '@/components/ui/Chip';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Panel } from '@/components/ui/Panel';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { Skeleton } from '@/components/ui/Skeleton';
import { WINDOWS, type Window } from '@/lib/api/client';
import { useSessions } from '@/lib/api/hooks';
import { agentLabel } from '@/lib/agents';
import { sessionTokens } from '@/lib/analysis/rollups';
import { num, tok, usd } from '@/lib/format/format';
import { applyFilters, errorCount, type Filters } from './filters';
import { SessionsTable } from './SessionsTable';
import { AnalysisStrip } from './AnalysisStrip';

const WL: Record<Window, string> = { '24h': 'Last 24h', '7d': 'Last 7 days', '30d': 'Last 30 days', all: 'All time' };
const SEL = 'h-6 bg-bg-2 border border-line-2 rounded-md text-[11px] text-ink-1 px-1.5 max-w-60';

export default function SessionsPage() {
  const q = useSessions();
  const all = useMemo(() => q.data ?? [], [q.data]);
  const [f, setF] = useState<Filters>({ q: '', project: null, model: null, agent: null, window: '30d', hasErrors: false });
  const errorsById = useMemo(() => Object.fromEntries(all.map((s) => [s.id, errorCount(s) ?? 0])), [all]);
  const showErrors = all.some((s) => errorCount(s) != null);
  const rows = useMemo(() => applyFilters(all, f, errorsById, new Date()), [all, f, errorsById]);
  const projects = useMemo(() => [...new Set(all.map((s) => s.project_path))].sort(), [all]);
  const models = useMemo(() => [...new Set(all.map((s) => s.primary_model))].sort(), [all]);
  const agents = useMemo(() => [...new Set(all.map((s) => s.agent))].sort(), [all]);
  // Agent filter and column only earn their space once a second agent shows up.
  const showAgent = agents.length > 1;
  const nextWindow = (w: Window): Window => WINDOWS[(WINDOWS.indexOf(w) + 1) % WINDOWS.length];
  const tot = rows.reduce((a, s) => ({ t: a.t + sessionTokens(s), c: a.c + s.total_cost_usd }), { t: 0, c: 0 });

  if (q.error) return (<><TopBar crumbs={['Analyze', 'Sessions']} /><Page><ErrorPanel error={q.error} /></Page></>);
  return (
    <>
      <TopBar crumbs={['Analyze', 'Sessions']} />
      <Page>
        <div className="flex items-center gap-2 flex-wrap">
          <label className="flex items-center gap-2 h-7 px-2.5 border border-line-2 rounded-md w-64 text-ink-2">
            <Icon name="search" size={13} />
            <input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="Filter by project, model or agent" className="bg-transparent outline-none text-ink-0 text-xs w-full placeholder:text-ink-2" />
          </label>
          <select value={f.project ?? ''} onChange={(e) => setF({ ...f, project: e.target.value || null })} className={SEL}>
            <option value="">Project: all</option>
            {projects.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <select value={f.model ?? ''} onChange={(e) => setF({ ...f, model: e.target.value || null })} className={SEL}>
            <option value="">Model: all</option>
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          {showAgent && (
            <select value={f.agent ?? ''} onChange={(e) => setF({ ...f, agent: e.target.value || null })} className={SEL}>
              <option value="">Agent: all</option>
              {agents.map((a) => <option key={a} value={a}>{agentLabel(a)}</option>)}
            </select>
          )}
          <Chip active caret onClick={() => setF({ ...f, window: nextWindow(f.window) })}>{WL[f.window]}</Chip>
          {showErrors && <Chip active={f.hasErrors} onClick={() => setF({ ...f, hasErrors: !f.hasErrors })}>Has errors</Chip>}
          <span className="ml-auto font-mono text-[11px] text-ink-2">{num(rows.length)} sessions · {tok(tot.t)} tokens · {usd(tot.c)}</span>
          <a href="/api/export.csv" download><Button variant="ghost"><Icon name="download" size={13} />Export CSV</Button></a>
        </div>
        {q.isLoading ? <Skeleton w="100%" h="200px" /> : <AnalysisStrip sessions={rows} />}
        <Panel padded={false} className="flex-1 min-h-[320px]">
          <SessionsTable rows={rows} showErrors={showErrors} showAgent={showAgent} />
          <div className="flex items-center gap-3 px-3.5 py-2 border-t border-line text-[11px] text-ink-2 font-mono">
            <span>{num(rows.length)} of {num(all.length)}</span>
            <span className="ml-auto">rows ↑↓ · open ⏎ · token bar is relative to the largest session in view</span>
          </div>
        </Panel>
      </Page>
    </>
  );
}
