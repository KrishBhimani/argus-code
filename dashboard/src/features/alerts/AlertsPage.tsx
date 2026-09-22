import { useMemo, useState } from 'react';
import { TopBar, Page } from '@/app/shell/TopBar';
import { Seg } from '@/components/ui/Seg';
import { Panel } from '@/components/ui/Panel';
import { Pill } from '@/components/ui/Pill';
import { Button } from '@/components/ui/Button';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { AlertStrip } from '@/components/charts/AlertStrip';
import { useAlerts, useMarkAlertSeen, useUnseenAlerts } from '@/lib/api/hooks';
import type { Alert } from '@/lib/api/client';
import { alertWhen } from '@/features/overview/AttentionStrip';

const KIND = { info: 'info', warning: 'warn', critical: 'crit' } as const;
const RAIL = { info: 'shadow-[inset_3px_0_0_var(--color-s1)]', warning: 'shadow-[inset_3px_0_0_var(--color-warn)]', critical: 'shadow-[inset_3px_0_0_var(--color-crit)]' } as const;
type Filter = 'unseen' | 'all' | 'warning' | 'critical';
const Code = ({ children }: { children: string }) => <span className="font-mono text-[11px] text-ink-1 bg-bg-3 px-1.5 rounded-sm">{children}</span>;

const DETECTORS = [
  { n: 'tool_error_rate_spike', on: true, note: '7d vs 28d' },
  { n: 'cost_spike', on: true, note: '7d vs 28d/wk' },
  { n: 'cache_hit_drop', on: true, note: '7d vs 28d' },
];

/** Identifying chips for a finding — the detectors key on a tool or a project. */
const subject = (a: Alert) => {
  const tool = typeof a.metadata?.tool_name === 'string' ? (a.metadata.tool_name as string) : null;
  const project = typeof a.metadata?.project === 'string' ? (a.metadata.project as string) : null;
  return [tool && `tool=${tool}`, project && `project=${project}`].filter(Boolean) as string[];
};

export default function AlertsPage() {
  const all = useAlerts();
  const unseen = useUnseenAlerts();
  const mark = useMarkAlertSeen();
  const [f, setF] = useState<Filter>('unseen');
  const unseenIds = useMemo(() => new Set((unseen.data ?? []).map((a) => a.id)), [unseen.data]);
  const sorted = useMemo(() => [...(all.data ?? [])].sort((x, y) => alertWhen(y).localeCompare(alertWhen(x))), [all.data]);
  const rows = f === 'all' ? sorted : f === 'unseen' ? sorted.filter((x) => unseenIds.has(x.id)) : sorted.filter((x) => x.severity === f);
  const last30 = sorted.filter((a) => Date.now() - new Date(alertWhen(a)).getTime() < 30 * 86_400_000);
  const fmtWhen = (a: Alert) => (alertWhen(a) ? new Date(alertWhen(a)).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '');

  if (all.error) return (<><TopBar crumbs={['Monitor', 'Alerts']} /><Page><ErrorPanel error={all.error} /></Page></>);
  return (
    <>
      <TopBar crumbs={['Monitor', 'Alerts']}>
        <Seg
          options={[{ value: 'unseen', label: `Unseen · ${unseenIds.size}` }, { value: 'all', label: `All · ${sorted.length}` }, { value: 'warning', label: 'Warning' }, { value: 'critical', label: 'Critical' }]}
          value={f}
          onChange={setF}
        />
        <Button variant="ghost" disabled={!unseenIds.size} onClick={() => unseenIds.forEach((id) => mark.mutate(id))}>Mark all seen</Button>
      </TopBar>
      <Page row>
        <Panel padded={false} className="flex-1 self-start">
          {rows.length === 0 && (
            <div className="text-center text-ink-2 py-10 flex items-center justify-center gap-2"><Pill kind="good">ALL CLEAR</Pill>nothing {f === 'all' ? 'recorded' : f}</div>
          )}
          {rows.map((a) => {
            const seen = !unseenIds.has(a.id);
            const chips = subject(a);
            return (
              <div key={a.id} className={`flex items-start gap-3.5 p-3.5 border-b border-line ${seen ? 'opacity-70' : RAIL[a.severity]}`}>
                <Pill kind={KIND[a.severity]} className="mt-px">{a.severity.toUpperCase()}</Pill>
                <div className="flex flex-col gap-1 flex-1 min-w-0">
                  <span className="font-semibold">{a.title}</span>
                  <span className="text-ink-1">{a.message}</span>
                  <div className="flex gap-2 mt-1.5 items-center"><Code>{a.detector}</Code>{chips.map((c) => <Code key={c}>{c}</Code>)}</div>
                </div>
                <div className="flex flex-col items-end gap-2">
                  <span className="font-mono text-[11px] text-ink-2 whitespace-nowrap">{fmtWhen(a)}</span>
                  {seen ? <Pill kind="mute">seen</Pill> : <Button size="sm" onClick={() => mark.mutate(a.id)}>Mark seen</Button>}
                </div>
              </div>
            );
          })}
        </Panel>
        <div className="w-[300px] shrink-0 flex flex-col gap-3">
          <Panel title="Detectors" sub="run after each ingest" padded={false}>
            {DETECTORS.map((d) => (
              <div key={d.n} className={`flex items-center gap-2.5 px-3.5 py-2.5 border-b border-line last:border-b-0 ${d.on ? '' : 'text-ink-2'}`}>
                <span className={`w-[7px] h-[7px] rounded-full ${d.on ? 'bg-good' : 'border border-line-2'}`} />
                <span className="font-mono text-[11.5px]">{d.n}</span>
                <span className={`ml-auto font-mono text-[11px] ${d.on ? 'text-good' : ''}`}>{d.on ? 'on' : 'off'} · {d.note}</span>
              </div>
            ))}
          </Panel>
          <Panel title="Last 30 days">
            <AlertStrip alerts={last30.map((a) => ({ at: new Date(alertWhen(a)).getTime(), severity: a.severity }))} />
            <div className="flex flex-col gap-2 mt-3 text-xs">
              <div className="flex justify-between"><span className="text-ink-1">Alerts raised</span><span className="font-mono num">{last30.length}</span></div>
              <div className="flex justify-between"><span className="text-ink-1">Critical</span><span className="font-mono num">{last30.filter((a) => a.severity === 'critical').length}</span></div>
            </div>
          </Panel>
        </div>
      </Page>
    </>
  );
}
