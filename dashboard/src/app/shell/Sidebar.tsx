import { Link, useRouterState } from '@tanstack/react-router';
import { Icon, type IconName } from '@/components/ui/Icon';
import { useIngestStatus, usePricing, useUnseenAlerts } from '@/lib/api/hooks';
import { useWorkStatus } from '@/features/work/api';

const GROUPS: { h: string; items: { to: string; label: string; icon: IconName }[] }[] = [
  { h: 'MONITOR', items: [{ to: '/', label: 'Overview', icon: 'grid' }, { to: '/alerts', label: 'Alerts', icon: 'bell' }] },
  {
    h: 'ANALYZE',
    items: [
      { to: '/sessions', label: 'Sessions', icon: 'list' },
      { to: '/tools', label: 'Tools', icon: 'wrench' },
      { to: '/models', label: 'Models', icon: 'layers' },
      { to: '/trends', label: 'Trends', icon: 'trend' },
    ],
  },
  { h: 'SEARCH', items: [{ to: '/search', label: 'Transcripts', icon: 'search' }] },
  { h: 'GOVERN', items: [{ to: '/settings', label: 'Settings', icon: 'gear' }] },
];

export function Sidebar() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const unseen = useUnseenAlerts().data?.length ?? 0;
  const ingest = useIngestStatus().data;
  const pricing = usePricing().data;
  const busy = ingest && !(ingest.foregroundComplete && ingest.pending === 0);
  const isActive = (to: string) => (to === '/' ? path === '/' : path.startsWith(to));
  const workOn = useWorkStatus().enabled;
  const groups = workOn
    ? [...GROUPS.slice(0, 2), { h: 'WORK', items: [{ to: '/projects', label: 'Projects', icon: 'folder' as const }] }, ...GROUPS.slice(2)]
    : GROUPS;
  return (
    <aside className="w-52 shrink-0 bg-bg-1 border-r border-line flex flex-col">
      <div className="flex items-center gap-2 h-12 px-4 border-b border-line">
        <span className="w-[18px] h-[18px] rounded-md bg-accent flex items-center justify-center text-[#1a0f06]">
          <Icon name="eye" size={11} />
        </span>
        <span className="font-semibold tracking-[0.14em] text-xs">ARGUS</span>
        <span
          title={busy ? 'ingesting' : 'live'}
          className={`ml-auto w-[7px] h-[7px] rounded-full ${busy ? 'bg-warn animate-pulse shadow-[0_0_0_3px_rgba(250,178,25,.18)]' : 'bg-good shadow-[0_0_0_3px_rgba(12,163,12,.18)]'}`}
        />
      </div>
      <nav className="p-2 flex flex-col gap-0.5 flex-1">
        {groups.map((g) => (
          <div key={g.h} className="contents">
            <div className="text-[10px] tracking-[0.12em] text-ink-2 px-2 pt-3 pb-1 font-medium">
              {g.h}
              {g.h === 'WORK' && <span className="ml-1.5 px-1 rounded-sm border border-accent/40 text-accent-ink tracking-normal">TRIAL</span>}
            </div>
            {g.items.map((it) => (
              <Link
                key={it.to}
                to={it.to}
                data-active={isActive(it.to)}
                className={`flex items-center gap-2.5 px-2 h-7 rounded-md font-medium ${isActive(it.to) ? 'bg-bg-3 text-ink-0 shadow-[inset_2px_0_0_var(--color-accent)]' : 'text-ink-1 hover:text-ink-0'}`}
              >
                <Icon name={it.icon} size={15} className="opacity-85" />
                {it.label}
                {it.label === 'Alerts' && unseen > 0 && (
                  <span className="ml-auto font-mono text-[10px] px-1.5 rounded-sm bg-warn/14 text-warn">{unseen}</span>
                )}
              </Link>
            ))}
          </div>
        ))}
      </nav>
      <div className="border-t border-line px-4 py-2.5 text-[11px] text-ink-2 flex flex-col gap-1 font-mono">
        <span>
          {ingest
            ? busy
              ? `ingesting ${ingest.processed}/${ingest.total}`
              : `tracking ${ingest.sessionCount ?? ingest.processed} sessions`
            : 'connecting…'}
        </span>
        <span>pricing {pricing?.version ?? '—'}</span>
        <span>{ingest?.daemon ? 'argusd daemon' : 'foreground watcher'}</span>
      </div>
    </aside>
  );
}
