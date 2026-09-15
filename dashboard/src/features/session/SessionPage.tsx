import { useMemo, useState } from 'react';
import { useNavigate, useParams, useSearch } from '@tanstack/react-router';
import { TopBar, Page } from '@/app/shell/TopBar';
import { Seg } from '@/components/ui/Seg';
import { Tile } from '@/components/ui/Tile';
import { Pill } from '@/components/ui/Pill';
import { Panel } from '@/components/ui/Panel';
import { ErrorPanel } from '@/components/ui/ErrorPanel';
import { Skeleton } from '@/components/ui/Skeleton';
import { CopyId } from '@/components/ui/CopyId';
import { Minimap } from '@/components/charts/Minimap';
import { AreaLine } from '@/components/charts/AreaLine';
import { Legend } from '@/components/charts/Legend';
import { useSession, useSubagents, useTimeline } from '@/lib/api/hooks';
import { agentLabel, resumeHint } from '@/lib/agents';
import { dur, fmtLocalDateTime, num, pct, shortPath, tok, usd } from '@/lib/format/format';
import { cumulativeCost, orderTurns, turnHasError } from './model';
import { OverviewTab } from './OverviewTab';
import { TimelineTab } from './TimelineTab';
import { SubagentsTab } from './SubagentsTab';

export type Tab = 'overview' | 'timeline' | 'subagents';

export default function SessionPage() {
  const { id } = useParams({ from: '/sessions/$id' });
  const { tab } = useSearch({ from: '/sessions/$id' });
  const nav = useNavigate();
  const s = useSession(id);
  const tl = useTimeline(id);
  const subs = useSubagents(id);
  const [active, setActive] = useState<number | undefined>();
  const turns = useMemo(() => orderTurns(tl.data?.turns ?? []), [tl.data]);
  const errors = turns.filter(turnHasError).length;
  const cum = useMemo(() => cumulativeCost(turns), [turns]);
  const cumSeries = useMemo(() => [{ name: 'cumulative cost', values: cum }], [cum]);
  const cumLabels = useMemo(() => turns.map((t) => `#${t.sequence}`), [turns]);
  const setTab = (t: Tab) => nav({ to: '/sessions/$id', params: { id }, search: { tab: t } });
  const pick = (seq: number) => {
    setActive(seq);
    if (tab !== 'timeline') setTab('timeline');
    requestAnimationFrame(() => document.getElementById(`turn-${seq}`)?.scrollIntoView({ block: 'center' }));
  };

  const crumbs = ['Analyze', { label: 'Sessions', to: '/sessions' }, s.data ? shortPath(s.data.session.project_path, 30) : '…'] as const;
  if (s.error) return (<><TopBar crumbs={[...crumbs]} /><Page><ErrorPanel error={s.error} /></Page></>);

  const sess = s.data?.session;
  const tokensAll = sess ? sess.total_fresh_input_tokens + sess.total_output_tokens + sess.total_cache_read_tokens + sess.total_cache_write_tokens : 0;
  const toolCalls = turns.reduce((a, t) => a + t.tool_calls.length, 0);

  return (
    <>
      <TopBar crumbs={[...crumbs]}>
        <CopyId value={id} hint={resumeHint(id)} />
        <Seg
          options={[{ value: 'overview', label: 'Overview' }, { value: 'timeline', label: 'Timeline' }, { value: 'subagents', label: `Sub-agents · ${subs.data?.length ?? 0}` }]}
          value={tab}
          onChange={setTab}
        />
        {sess && <Pill kind="mute">{sess.primary_model}</Pill>}
        {sess?.agent_version && <Pill kind="mute">{agentLabel(sess.agent)} {sess.agent_version}</Pill>}
      </TopBar>
      <Page>
        {!sess ? (
          <Skeleton w="100%" h="80px" />
        ) : (
          <div className="grid grid-cols-5 gap-3">
            <Tile label="Tokens" value={tok(tokensAll)} sub={`${num(tokensAll)} · ${pct(tokensAll ? sess.total_cache_read_tokens / tokensAll : 0, 0)} cache read`} />
            <Tile label="Estimated cost" value={usd(sess.total_cost_usd)} sub={sess.turn_count ? `${usd(sess.total_cost_usd / sess.turn_count)} / turn` : undefined} />
            <Tile label="Turns" value={turns.length ? num(turns.length) : num(sess.turn_count)} sub={`${num(toolCalls)} tool calls`} note={turns.length && turns.length !== sess.turn_count ? `${num(sess.turn_count)} raw` : undefined} />
            <Tile label="Duration" value={sess.ended_at ? dur(sess.duration_sec) : 'live'} sub={fmtLocalDateTime(sess.started_at)} />
            <Tile label="Tool errors" value={String(errors)} tone={errors ? 'crit' : 'default'} sub={errors ? `first at turn #${turns.find(turnHasError)?.sequence}` : 'none'} />
          </div>
        )}
        <Panel title="Session shape" sub="tokens per turn · click to jump" right={<Legend names={['fresh + output', 'cache read', 'tool error']} colors={['#3987e5', '#1f3c63', '#d03b3b']} />}>
          {turns.length ? (
            <>
              <Minimap turns={turns} onPick={pick} active={active} />
              <div className="mt-2"><AreaLine labels={cumLabels} series={cumSeries} format={(n) => (n === 0 ? '$0' : usd(n))} height={110} /></div>
            </>
          ) : tl.isLoading ? <Skeleton w="100%" h="96px" /> : <div className="text-ink-2 text-center py-4">No turns recorded.</div>}
        </Panel>
        {tab === 'overview' && sess && <OverviewTab session={sess} turns={turns} />}
        {tab === 'timeline' && <TimelineTab id={id} turns={turns} active={active} />}
        {tab === 'subagents' && <SubagentsTab subs={subs.data ?? []} />}
      </Page>
    </>
  );
}
