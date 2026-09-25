import { useNavigate } from '@tanstack/react-router';
import { TopBar, Page } from '@/app/shell/TopBar';
import { Panel } from '@/components/ui/Panel';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { Sparkline } from '@/components/charts/Sparkline';
import { ago, num } from '@/lib/format/format';
import { useWorkProjects } from './api';
import { hours } from './fmt';

export default function ProjectsPage() {
  const q = useWorkProjects();
  const nav = useNavigate();
  const rows = q.data?.projects ?? [];
  return (
    <>
      <TopBar crumbs={['Work', 'Projects']} />
      <Page>
        {q.error ? <ErrorPanel error={q.error} /> : (
          <Panel title="Projects" sub="repos your agent sessions worked in · last 30 days" padded={false}>
            {!q.isLoading && rows.length === 0 ? (
              <EmptyState title="No repos found yet" hint="The work scan runs every 5 minutes; `argus work scan` runs one now." />
            ) : (
              <table className="w-full text-xs">
                <thead className="text-[10px] tracking-[0.08em] text-ink-2">
                  <tr className="border-b border-line">
                    <th className="text-left font-medium px-3.5 py-2">PROJECT</th>
                    <th className="text-right font-medium px-3.5">ACTIVE 30D</th>
                    <th className="text-right font-medium px-3.5">COMMITS</th>
                    <th className="text-left font-medium px-3.5">ACTIVITY 30D</th>
                    <th className="text-right font-medium px-3.5">LAST WORKED</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.id} className="border-b border-line hover:bg-bg-2 cursor-pointer"
                      onClick={() => nav({ to: '/projects/$repo', params: { repo: String(p.id) }, search: { tab: 'overview' } })}>
                      <td className="px-3.5 py-2.5 font-mono" title={p.root}>
                        {p.display_name}
                        {p.folders.length > 1 && (
                          <span className="ml-2 text-[10px] px-1.5 rounded-full border border-line-2 text-ink-2 font-sans" title={p.folders.map((f) => f.root).join('\n')}>
                            {p.folders.length} folders
                          </span>
                        )}
                        {!p.present && <span className="ml-2 text-[10px] px-1.5 rounded-sm border border-line-2 text-ink-2 font-sans">repo gone · archived</span>}
                        {p.last_error && <span className="ml-2 text-[10px] text-warn font-sans" title={p.last_error}>scan warning</span>}
                      </td>
                      <td className="px-3.5 text-right font-mono num">{hours(p.active_ms_30d, p.active_estimated)}</td>
                      <td className="px-3.5 text-right font-mono num">{num(p.commits_30d)}</td>
                      <td className="px-3.5"><Sparkline values={p.daily_active_ms} width={120} height={22} /></td>
                      <td className="px-3.5 text-right text-ink-1">{p.last_worked_at ? ago(p.last_worked_at) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>
        )}
      </Page>
    </>
  );
}
