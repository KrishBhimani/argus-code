import { useMutation, useQueryClient } from '@tanstack/react-query';
import { TopBar, Page } from '@/app/shell/TopBar';
import { Panel } from '@/components/ui/Panel';
import { Button } from '@/components/ui/Button';
import { Pill } from '@/components/ui/Pill';
import { api } from '@/lib/api/client';
import { useIngestStatus, useParseErrors, usePricing, useSearchIndexStatus } from '@/lib/api/hooks';
import { num } from '@/lib/format/format';

const Mono = ({ children }: { children: string }) => <span className="font-mono text-ink-0">{children}</span>;

export default function SettingsPage() {
  const qc = useQueryClient();
  const idx = useSearchIndexStatus();
  const ingest = useIngestStatus();
  const pricing = usePricing();
  const errors = useParseErrors();
  const inv = () => qc.invalidateQueries({ queryKey: ['searchIndex'] });
  const enable = useMutation({ mutationFn: api.searchIndexEnable, onSuccess: inv });
  const disable = useMutation({ mutationFn: api.searchIndexDisable, onSuccess: inv });
  const clear = useMutation({ mutationFn: api.searchIndexClear, onSuccess: inv });
  const d = idx.data;
  const ing = ingest.data;
  return (
    <>
      <TopBar crumbs={['Govern', 'Settings']} />
      <Page>
        <div className="grid grid-cols-2 gap-3 items-start">
          <div className="flex flex-col gap-3">
            <Panel title="Search indexing" sub={d ? (d.enabled ? 'on' : 'off') : ''}>
              <p className="text-xs text-ink-1 m-0 mb-3">
                Indexes agent replies, thinking and tool output into a local FTS5 table so Transcripts search covers more than your prompts. Stored in <Mono>~/.argus/argus.db</Mono>; nothing leaves the machine.
              </p>
              {d && (
                <div className="text-xs font-mono text-ink-1 flex flex-col gap-1 mb-3">
                  <span>{num(d.segment_count)} segments · {num(d.indexed_sessions)} sessions indexed</span>
                  {d.backfill.in_progress && <span className="text-warn">backfilling {d.backfill.processed}/{d.backfill.total}</span>}
                </div>
              )}
              <div className="flex gap-2">
                {d?.enabled ? <Button onClick={() => disable.mutate()}>Disable indexing</Button> : <Button variant="primary" onClick={() => enable.mutate()}>Enable indexing</Button>}
                {d && d.segment_count > 0 && (
                  <Button variant="danger" onClick={() => { if (window.confirm('Delete all indexed transcript segments? Your prompts and sessions are unaffected.')) clear.mutate(); }}>Clear indexed data</Button>
                )}
              </div>
            </Panel>
            <Panel title="Pricing">
              <p className="text-xs text-ink-1 m-0">
                Costs are estimated from pricing table <Mono>{pricing.data?.version ?? '—'}</Mono>. Tokens are exact. Run <Mono>argus pricing refresh</Mono> to update (the only network call Argus ever makes, and only when you ask).
              </p>
            </Panel>
            <Panel title="Export data">
              <div className="flex gap-2">
                <a href="/api/export.json" download><Button>Download JSON</Button></a>
                <a href="/api/export.csv" download><Button>Download CSV</Button></a>
              </div>
            </Panel>
            <Panel title="Wipe local data">
              <p className="text-xs text-ink-1 m-0">To remove everything Argus stores, stop the server and delete <Mono>~/.argus/</Mono>. The dashboard never deletes your data on its own.</p>
            </Panel>
          </div>
          <div className="flex flex-col gap-3">
            <Panel title="Ingest status">
              {ing ? (
                <div className="text-xs font-mono text-ink-1 flex flex-col gap-1.5">
                  <span>{ing.foregroundComplete ? <Pill kind="good">READY</Pill> : <Pill kind="warn">INGESTING</Pill>}</span>
                  <span>{ing.processed}/{ing.total} files · {ing.pending} pending</span>
                  {ing.sessionCount != null && <span>{num(ing.sessionCount)} sessions</span>}
                  <span>{ing.daemon ? 'argusd daemon' : 'foreground watcher'}</span>
                </div>
              ) : null}
            </Panel>
            <Panel title="Parse errors" sub={errors.data ? String(errors.data.length) : ''} padded={false}>
              {errors.data?.length ? (
                errors.data.slice(0, 50).map((e, i) => (
                  <details key={i} className="px-3.5 py-2 border-b border-line text-xs">
                    <summary className="cursor-pointer font-mono text-ink-1 truncate">{e.file}</summary>
                    <div className="text-ink-1 mt-1">{e.reason}</div>
                    <pre className="mt-1 p-2 bg-bg-0 border border-line rounded-md text-[11px] overflow-auto whitespace-pre-wrap">{e.raw_line_truncated}</pre>
                  </details>
                ))
              ) : <div className="text-ink-2 text-center py-6 text-xs">No parse errors.</div>}
            </Panel>
          </div>
        </div>
      </Page>
    </>
  );
}
