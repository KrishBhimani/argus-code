import { useState } from 'react';
import { Link, useParams, useSearch } from '@tanstack/react-router';
import { TopBar, Page } from '@/app/shell/TopBar';
import { useWorkProjects, type WorkProject } from './api';
import { shortPath } from './fmt';
import OverviewTab from './OverviewTab';
import ActivityTab from './ActivityTab';
import { PeriodChips } from './PeriodChips';

export type WorkTab = 'overview' | 'activity';
const TABS: { id: WorkTab; label: string }[] = [{ id: 'overview', label: 'Overview' }, { id: 'activity', label: 'Activity' }];

/** Where this project lives on disk: one line, short paths, the full path on hover. */
function Folders({ project }: { project: WorkProject }) {
  const n = project.folders.length;
  return (
    <div className="flex items-center gap-1.5 flex-wrap text-[11px] text-ink-2 -mb-2">
      {n > 1 && <span className="text-ink-1">{n} folders</span>}
      {project.folders.map((f, i) => (
        <span key={f.id} className="flex items-center gap-1.5">
          {(i > 0 || n > 1) && <span aria-hidden>·</span>}
          <span className={`font-mono ${f.present ? '' : 'line-through opacity-60'}`}
            title={f.present ? f.root : `${f.root} (folder no longer exists)`}>{shortPath(f.root)}</span>
        </span>
      ))}
    </div>
  );
}

export default function ProjectPage() {
  const { repo } = useParams({ from: '/projects/$repo' });
  const { tab, focus } = useSearch({ from: '/projects/$repo' });
  const id = Number(repo);
  const [days, setDays] = useState(30);
  const project = useWorkProjects().data?.projects.find((p) => p.folder_ids.includes(id));
  return (
    <>
      <TopBar crumbs={[{ label: 'Projects', to: '/projects' }, project?.display_name ?? `#${repo}`]} />
      <Page>
        {project && <Folders project={project} />}
        <nav className="flex items-center gap-5 border-b border-line -mb-1">
          {TABS.map((t) => (
            <Link key={t.id} to="/projects/$repo" params={{ repo }} search={{ tab: t.id }}
              className={`pb-2 text-[13px] ${tab === t.id ? 'text-ink-0 shadow-[inset_0_-2px_0_var(--color-accent)]' : 'text-ink-1 hover:text-ink-0'}`}>{t.label}</Link>
          ))}
          <div className="ml-auto pb-2"><PeriodChips days={days} onChange={setDays} /></div>
        </nav>
        {tab === 'overview' && <OverviewTab key={days} repo={id} days={days} />}
        {tab === 'activity' && <ActivityTab repo={id} days={days} focus={focus} />}
      </Page>
    </>
  );
}
