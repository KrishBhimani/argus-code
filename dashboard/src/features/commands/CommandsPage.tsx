import { useEffect, useState } from 'react';
import { Link, useSearch } from '@tanstack/react-router';
import { TopBar, Page } from '@/app/shell/TopBar';
import { Panel } from '@/components/ui/Panel';
import { Chip } from '@/components/ui/Chip';
import { Icon } from '@/components/ui/Icon';
import { Pill, type PillKind } from '@/components/ui/Pill';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { useCommands } from '@/lib/api/hooks';
import { shortPath } from '@/lib/format/format';
import { agentLabel } from '@/lib/agents';

type Severity = 'low' | 'medium' | 'high';
type Window = '7d' | '30d' | 'all';
const WINDOWS: { k: Window; l: string }[] = [{ k: '7d', l: '7 days' }, { k: '30d', l: '30 days' }, { k: 'all', l: 'All time' }];
const LEVELS: { k: Severity; l: string }[] = [{ k: 'high', l: 'High risk' }, { k: 'medium', l: 'Medium or higher' }, { k: 'low', l: 'Any flag' }];
const KIND: Record<Severity, PillKind> = { high: 'crit', medium: 'warn', low: 'mute' };
const PAGE = 100;

function useDebounced<T>(v: T, ms = 250) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

/**
 * The command ledger: what shell commands agents ran. Every command is rendered as a text
 * node. It is never turned into markup, a link target or anything runnable.
 */
export default function CommandsPage() {
  const from = useSearch({ strict: false }) as { session?: string };
  const [q, setQ] = useState('');
  const [win, setWin] = useState<Window>('30d');
  const [severity, setSeverity] = useState<Severity | undefined>();
  const [secrets, setSecrets] = useState(false);
  const [pages, setPages] = useState(1);
  const dq = useDebounced(q);
  const session = from.session || undefined;
  const res = useCommands({ window: session ? 'all' : win, q: dq.trim(), severity, secrets, session, limit: PAGE * pages });
  const d = res.data;
  const items = d?.items ?? [];
  const c = d?.counts;
  return (
    <>
      <TopBar crumbs={['Govern', 'Commands']} />
      <Page>
        <p className="text-ink-2 text-xs m-0">
          Every shell command your agents ran. Secrets are masked before anything is stored. A flag means &quot;worth a look&quot;:
          the rules match a command&apos;s text and do not know what it did.
        </p>
        <div className="flex items-center gap-2 flex-wrap">
          <label className="flex items-center gap-2 h-8 px-2.5 border border-line-2 rounded-md flex-1 min-w-72 text-ink-2">
            <Icon name="search" size={13} />
            <input value={q} onChange={(e) => { setQ(e.target.value); setPages(1); }} placeholder="Find text in a command…" maxLength={500}
              aria-label="Find text in a command" className="bg-transparent outline-none text-ink-0 text-sm w-full placeholder:text-ink-2" />
          </label>
          {!session && WINDOWS.map((w) => <Chip key={w.k} active={win === w.k} onClick={() => { setWin(w.k); setPages(1); }}>{w.l}</Chip>)}
        </div>
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="text-ink-2">Show:</span>
          {LEVELS.map((s) => <Chip key={s.k} active={severity === s.k} onClick={() => { setSeverity(severity === s.k ? undefined : s.k); setPages(1); }}>{s.l}</Chip>)}
          <Chip active={secrets} onClick={() => { setSecrets(!secrets); setPages(1); }}>With a secret</Chip>
          {session && <Pill kind="info" icon={false}>one session</Pill>}
          {c && <span className="ml-auto font-mono text-[11px] text-ink-2">{c.total} commands · {c.high} high · {c.medium} medium · {c.low} low · {c.secrets} with a secret</span>}
        </div>
        {res.error ? <ErrorPanel error={res.error} /> : (
          <Panel padded={false} className="grow">
            {items.length === 0 && <div className="text-center text-ink-2 py-10">{res.isFetching ? 'Loading…' : 'No commands match.'}</div>}
            {items.map((it) => (
              <div key={it.id} data-row className="grid grid-cols-[120px_1fr_170px] gap-3 px-3.5 py-2.5 border-b border-line items-start">
                <div className="flex flex-col gap-1 items-start">
                  {it.severity ? <Pill kind={KIND[it.severity]}>{it.severity.toUpperCase()}</Pill> : <span className="h-5" />}
                  <span className="font-mono text-[10px] text-ink-2">{new Date(it.timestamp).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                </div>
                <div className="flex flex-col gap-1.5 min-w-0">
                  <pre className="m-0 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap break-words max-h-40 overflow-auto">{it.command}</pre>
                  <div className="flex gap-1.5 flex-wrap items-center text-[10px] font-mono text-ink-2">
                    {it.risks.map((r) => <span key={r.rule} className="px-1.5 rounded-sm bg-bg-3 text-ink-1">{r.rule}</span>)}
                    {it.secrets.some((s) => s !== 'env-file') && <span className="px-1.5 rounded-sm bg-warn/10 text-warn">secret masked</span>}
                    {it.secrets.includes('env-file') && <span className="px-1.5 rounded-sm bg-bg-3 text-ink-1">read an env file</span>}
                    {it.failed && <span className="px-1.5 rounded-sm bg-crit/12 text-crit-ink">failed</span>}
                    {it.background && <span className="px-1.5 rounded-sm bg-bg-3">background</span>}
                    {it.truncated && <span className="px-1.5 rounded-sm bg-bg-3">truncated</span>}
                    <span title={it.cwd}>in {shortPath(it.cwd, 48)}</span>
                  </div>
                </div>
                <div className="text-right flex flex-col gap-1 items-end min-w-0">
                  <span className="font-mono text-[11px] text-ink-2 truncate max-w-full" title={it.project_path}>{shortPath(it.project_path)}</span>
                  <span className="font-mono text-[10px] text-ink-2">{agentLabel(it.agent)} · {it.shell}</span>
                  <Link to="/sessions/$id" params={{ id: it.session_id }} search={{ tab: 'timeline' }} className="text-[11px]">Open session →</Link>
                </div>
              </div>
            ))}
            {d && d.total > items.length && (
              <button type="button" onClick={() => setPages(pages + 1)} className="w-full py-2.5 text-xs text-ink-1 hover:text-ink-0">
                Show more ({items.length} of {d.total})
              </button>
            )}
          </Panel>
        )}
      </Page>
    </>
  );
}
