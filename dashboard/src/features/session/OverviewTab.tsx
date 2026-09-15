import type { Session, TimelineTurn } from '@/lib/api/client';
import { Panel } from '@/components/ui/Panel';
import { Bars } from '@/components/charts/Bars';
import { fmtLocalDateTime, num } from '@/lib/format/format';
import { CopyId } from '@/components/ui/CopyId';
import { agentLabel, resumeHint } from '@/lib/agents';
import { cacheRatio, toolMix } from './model';

export function OverviewTab({ session: s, turns }: { session: Session; turns: TimelineTurn[] }) {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const meta: [string, string][] = [
    ['Project', s.project_path],
    ['Primary model', s.primary_model],
    [agentLabel(s.agent), s.agent_version ?? '—'],
    ['Started', `${fmtLocalDateTime(s.started_at)} (${tz})`],
    ['Ended', s.ended_at ? fmtLocalDateTime(s.ended_at) : 'running'],
    ['Fresh input', `${num(s.total_fresh_input_tokens)} tokens`],
    ['Output', `${num(s.total_output_tokens)} tokens`],
    ['Cache writes', `${num(s.total_cache_write_tokens)} tokens`],
    ['Cache reads', `${num(s.total_cache_read_tokens)} tokens`],
    ['Pricing version', s.pricing_table_version],
  ];
  const mix = toolMix(turns);
  return (
    <div className="grid grid-cols-[1fr_1fr] gap-3">
      <Panel title="Session" padded={false}>
        <dl className="grid grid-cols-[140px_1fr] text-xs m-0">
          {meta.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="px-3.5 py-2 border-b border-line text-ink-1">{k}</dt>
              <dd className="px-3.5 py-2 border-b border-line font-mono m-0 break-all">{v}</dd>
            </div>
          ))}
          <div className="contents">
            <dt className="px-3.5 py-2 border-b border-line text-ink-1">Session id</dt>
            <dd className="px-3.5 py-2 border-b border-line font-mono m-0 break-all flex items-center gap-2"><span>{s.id}</span><CopyId value={s.id} chars={0} hint={resumeHint(s.id)} /></dd>
          </div>
        </dl>
      </Panel>
      <div className="flex flex-col gap-3">
        <Panel title="Tool mix" sub="calls · red segment = errors">
          {mix.length ? <Bars rows={mix} format={num} /> : <div className="text-ink-2 text-center py-4">No tool calls.</div>}
        </Panel>
        <Panel title="Cache ratio per turn" sub="share of each turn read from cache">
          <svg className="block w-full" viewBox={`0 0 1000 48`} preserveAspectRatio="none" style={{ aspectRatio: '1000 / 48' }} role="img" aria-label="Cache ratio per turn">
            {turns.map((t, i) => {
              const bw = 1000 / Math.max(1, turns.length);
              const r = cacheRatio(t);
              return (
                <g key={t.sequence}>
                  <title>{`#${t.sequence} · ${Math.round(r * 100)}%`}</title>
                  <rect x={i * bw} y={0} width={Math.max(0.5, bw - 0.5)} height={48} fill="#1d222c" />
                  <rect x={i * bw} y={48 - r * 48} width={Math.max(0.5, bw - 0.5)} height={r * 48} fill="#3987e5" />
                </g>
              );
            })}
          </svg>
        </Panel>
      </div>
    </div>
  );
}
