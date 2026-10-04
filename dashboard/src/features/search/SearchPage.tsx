import { useEffect, useMemo, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { TopBar, Page } from '@/app/shell/TopBar';
import { Panel } from '@/components/ui/Panel';
import { Chip } from '@/components/ui/Chip';
import { Icon } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';
import { Pill } from '@/components/ui/Pill';
import { Sparkline } from '@/components/charts/Sparkline';
import { usePromptProjects, useSearch, useSearchIndexStatus } from '@/lib/api/hooks';
import { api } from '@/lib/api/client';
import { dayKeys } from '@/lib/analysis/windows';
import { shortPath } from '@/lib/format/format';
import { agentOfId, agentShortName } from '@/lib/agents';
import { cleanSnippet, splitMarks } from './cleanSnippet';

const ROLES = [
  { k: 'prompt', l: 'Your prompts' }, { k: 'user', l: 'Your replies' }, { k: 'assistant', l: 'Assistant' }, { k: 'thinking', l: 'Thinking' }, { k: 'tool_result', l: 'Tool output' },
];
const ROLE_KIND: Record<string, 'good' | 'info' | 'mute'> = { prompt: 'good', user: 'good', assistant: 'info' };
// An assistant reply is labelled with the agent that wrote it (from the session id).
const roleLabel = (r: string, sessionId: string | null) =>
  r === 'assistant' ? (sessionId ? agentShortName(agentOfId(sessionId)) : 'assistant') : r === 'prompt' || r === 'user' ? 'you' : r;

function useDebounced<T>(v: T, ms = 250) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

export default function SearchPage() {
  const [q, setQ] = useState('');
  const [project, setProject] = useState('');
  const [roles, setRoles] = useState<string[]>(['prompt', 'user', 'assistant']);
  const [slash, setSlash] = useState(false);
  const dq = useDebounced(q);
  const projects = usePromptProjects().data ?? [];
  const idx = useSearchIndexStatus();
  const qc = useQueryClient();
  const enable = useMutation({ mutationFn: api.searchIndexEnable, onSuccess: () => qc.invalidateQueries({ queryKey: ['searchIndex'] }) });
  const ready = dq.trim().length >= 2;
  const res = useSearch({ q: dq, limit: 200, project: project || undefined, includeSlash: slash, roles }, ready);
  const hits = useMemo(() => res.data?.results ?? [], [res.data]);
  const spark = useMemo(() => {
    const keys = dayKeys(new Date(), 30);
    const m: Record<string, number> = Object.fromEntries(keys.map((k) => [k, 0]));
    for (const h of hits) {
      const d = new Date(h.timestamp_ms);
      const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      if (k in m) m[k]++;
    }
    return keys.map((k) => m[k]);
  }, [hits]);

  return (
    <>
      <TopBar crumbs={['Search', 'Transcripts']} />
      <Page>
        <p className="text-ink-2 text-xs m-0">Searches every prompt you typed and every transcript your agents wrote. 100% local — SQLite FTS5, no embeddings, no API calls.</p>
        {idx.data && !idx.data.enabled && (
          <div className="flex items-center gap-3 border border-warn/30 bg-warn/8 rounded-md px-3.5 py-2.5 text-xs">
            <Pill kind="warn">INDEXING OFF</Pill>
            <span className="text-ink-1">Only your prompts are searchable. Enable transcript indexing to search agent replies and tool output.</span>
            <Button className="ml-auto" onClick={() => enable.mutate()}>Enable indexing</Button>
          </div>
        )}
        <div className="flex items-center gap-2 flex-wrap">
          <label className="flex items-center gap-2 h-8 px-2.5 border border-line-2 rounded-md flex-1 min-w-72 text-ink-2">
            <Icon name="search" size={13} />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" className="bg-transparent outline-none text-ink-0 text-sm w-full placeholder:text-ink-2" />
          </label>
          <select value={project} onChange={(e) => setProject(e.target.value)} className="h-8 bg-bg-2 border border-line-2 rounded-md text-xs text-ink-1 px-2 max-w-72">
            <option value="">All projects</option>
            {projects.map((p) => <option key={p} value={p}>{shortPath(p, 60)}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="text-ink-2">Show:</span>
          {ROLES.map((r) => <Chip key={r.k} active={roles.includes(r.k)} onClick={() => setRoles(roles.includes(r.k) ? roles.filter((x) => x !== r.k) : [...roles, r.k])}>{r.l}</Chip>)}
          <Chip active={slash} onClick={() => setSlash(!slash)}>Include slash commands</Chip>
          {res.data && (
            <span className="ml-auto flex items-center gap-3 font-mono text-[11px] text-ink-2">
              {res.data.total} matches · {res.data.prompt_total} prompts · {res.data.transcript_total} transcript
              <span title="hits per day, last 30 days"><Sparkline values={spark} /></span>
            </span>
          )}
        </div>
        <Panel padded={false} className="grow">
          {hits.length === 0 && <div className="text-center text-ink-2 py-10">{!ready ? 'Type at least two characters to search.' : res.isFetching ? 'Searching…' : 'No matches.'}</div>}
          {hits.map((h, i) => {
            const text = cleanSnippet(h.snippet || h.text);
            if (!text) return null;
            return (
              <div key={i} className="grid grid-cols-[90px_1fr_160px] gap-3 px-3.5 py-2.5 border-b border-line items-start">
                <div className="flex flex-col gap-1">
                  <Pill kind={ROLE_KIND[h.role] ?? 'mute'} icon={false}>{roleLabel(h.role, h.session_id)}</Pill>
                  <span className="font-mono text-[10px] text-ink-2">{new Date(h.timestamp_ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                </div>
                <div className="text-xs leading-relaxed break-words">
                  {splitMarks(text).map((p, j) => (p.m ? <mark key={j} className="bg-accent/25 text-ink-0 rounded-sm px-0.5">{p.t}</mark> : <span key={j}>{p.t}</span>))}
                  {h.pasted_chars > 0 && <span className="text-ink-2 font-mono text-[10px] ml-2">+{h.pasted_chars} pasted chars</span>}
                </div>
                <div className="text-right flex flex-col gap-1 items-end min-w-0">
                  <span className="font-mono text-[11px] text-ink-2 truncate max-w-full" title={h.project_path ?? ''}>{h.project_path ? shortPath(h.project_path) : ''}</span>
                  {h.session_id && <Link to="/sessions/$id" params={{ id: h.session_id }} search={{ tab: 'overview' }} className="text-[11px]">Open session →</Link>}
                </div>
              </div>
            );
          })}
        </Panel>
      </Page>
    </>
  );
}
