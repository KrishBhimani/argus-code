import { Link, useParams, useSearch } from '@tanstack/react-router';
import { TopBar, Page } from '@/app/shell/TopBar';
import { EmptyState } from '@/components/ui/EmptyState';
import { useWorkProjects } from './api';
import OverviewTab from './OverviewTab';
import TimelineTab from './TimelineTab';

export type WorkTab = 'overview' | 'resume' | 'timeline' | 'report';
const TABS: { id: WorkTab; label: string }[] = [
  { id: 'overview', label: 'Overview' }, { id: 'resume', label: 'Resume' }, { id: 'timeline', label: 'Timeline' }, { id: 'report', label: 'Report' },
];

export default function ProjectPage() {
  const { repo } = useParams({ from: '/projects/$repo' });
  const { tab } = useSearch({ from: '/projects/$repo' });
  const id = Number(repo);
  const project = useWorkProjects().data?.projects.find((p) => p.id === id);
  return (
    <>
      <TopBar crumbs={[{ label: 'Projects', to: '/projects' }, project?.display_name ?? `#${repo}`]} />
      <Page>
        <nav className="flex gap-5 border-b border-line -mb-1">
          {TABS.map((t) => (
            <Link key={t.id} to="/projects/$repo" params={{ repo }} search={{ tab: t.id }}
              className={`pb-2 text-[13px] ${tab === t.id ? 'text-ink-0 shadow-[inset_0_-2px_0_var(--color-accent)]' : 'text-ink-1 hover:text-ink-0'}`}>{t.label}</Link>
          ))}
        </nav>
        {tab === 'overview' && <OverviewTab repo={id} />}
        {tab === 'timeline' && <TimelineTab repo={id} />}
        {(tab === 'resume' || tab === 'report') && (
          <EmptyState title={tab === 'resume' ? 'Resume is coming in a later piece' : 'Reports are coming in a later piece'}
            hint="The work trial currently covers Overview and Timeline." />
        )}
      </Page>
    </>
  );
}
