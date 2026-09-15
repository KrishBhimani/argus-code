import { useMemo } from 'react';
import { useNavigate } from '@tanstack/react-router';
import type { ColumnDef } from '@tanstack/react-table';
import type { Session } from '@/lib/api/client';
import { DataTable } from '@/components/ui/DataTable';
import { Pill } from '@/components/ui/Pill';
import { Icon } from '@/components/ui/Icon';
import { agentLabel } from '@/lib/agents';
import { sessionTokens } from '@/lib/analysis/rollups';
import { dur, shortDate, shortPath, tok, usd } from '@/lib/format/format';
import { errorCount } from './filters';

const Code = ({ children }: { children: string }) => <span className="font-mono text-[11.5px] text-ink-1 bg-bg-3 px-1.5 rounded-sm">{children}</span>;

export function SessionsTable({ rows, showErrors, showAgent = false }: { rows: Session[]; showErrors: boolean; showAgent?: boolean }) {
  const nav = useNavigate();
  const max = useMemo(() => Math.max(1, ...rows.map(sessionTokens)), [rows]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cols = useMemo<ColumnDef<Session, any>[]>(() => [
    { id: 'started', accessorKey: 'started_at', header: 'STARTED', size: 110, cell: ({ row }) => <span className="font-mono">{shortDate(row.original.started_at)}</span> },
    { id: 'project', accessorKey: 'project_path', header: 'PROJECT', size: 340, cell: ({ getValue }) => <span className="font-mono text-ink-1" title={getValue<string>()}>{shortPath(getValue<string>(), 48)}</span> },
    { id: 'model', accessorKey: 'primary_model', header: 'MODEL', size: 170, cell: ({ getValue }) => <Code>{getValue<string>()}</Code> },
    ...(showAgent
      ? [{
          id: 'agent', accessorKey: 'agent', header: 'AGENT', size: 100,
          cell: ({ getValue }: { getValue: <V>() => V }) => <Pill kind="mute" icon={false}>{agentLabel(getValue<string>())}</Pill>,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as ColumnDef<Session, any>]
      : []),
    { id: 'dur', accessorKey: 'duration_sec', header: 'DURATION', size: 100, meta: { right: true }, cell: ({ row }) => row.original.ended_at ? dur(row.original.duration_sec) : <Pill kind="good" icon={false}><span className="w-1.5 h-1.5 rounded-full bg-current" />live</Pill> },
    { id: 'turns', accessorKey: 'turn_count', header: 'TURNS', size: 70, meta: { right: true } },
    ...(showErrors
      ? [{
          id: 'errors', accessorFn: (s: Session) => errorCount(s) ?? 0, header: 'ERRORS', size: 80, meta: { right: true },
          cell: ({ getValue }: { getValue: <V>() => V }) => { const n = getValue<number>(); return n > 0 ? <span className="text-crit-ink inline-flex items-center gap-0.5"><Icon name="x" size={9} />{n}</span> : <span className="text-ink-2">0</span>; },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as ColumnDef<Session, any>]
      : []),
    { id: 'tokens', accessorFn: sessionTokens, header: 'TOKENS', size: 210, cell: ({ getValue }) => { const v = getValue<number>(); return <div className="flex items-center gap-2"><div className="relative h-1.5 flex-1 rounded-sm bg-bg-3 overflow-hidden"><i className="absolute inset-y-0 left-0 bg-s1 rounded-r-sm" style={{ width: `${(v / max) * 100}%` }} /></div><span className="font-mono num w-14 text-right">{tok(v)}</span></div>; } },
    { id: 'cost', accessorKey: 'total_cost_usd', header: 'EST. COST', size: 90, meta: { right: true }, cell: ({ getValue }) => usd(getValue<number>()) },
  ], [max, showErrors, showAgent]);
  return (
    <DataTable
      columns={cols}
      data={rows}
      getRowId={(s) => s.id}
      initialSort={[{ id: 'started', desc: true }]}
      onRowClick={(s) => nav({ to: '/sessions/$id', params: { id: s.id }, search: { tab: 'overview' } })}
      emptyText="No sessions match."
    />
  );
}
