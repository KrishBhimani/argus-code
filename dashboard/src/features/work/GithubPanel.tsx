import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Panel } from '@/components/ui/Panel';
import { Button } from '@/components/ui/Button';
import { ago } from '@/lib/format/format';
import { useWorkGithub, workApi } from './api';

/** Opt-in GitHub PR status: the same switch and clock as `argus work github enable|disable|refresh`. */
export function GithubPanel() {
  const qc = useQueryClient();
  const q = useWorkGithub();
  const done = () => qc.invalidateQueries({ queryKey: ['work'] });
  const enable = useMutation({ mutationFn: workApi.githubEnable, onSuccess: done });
  const disable = useMutation({ mutationFn: workApi.githubDisable, onSuccess: done });
  const refresh = useMutation({ mutationFn: workApi.githubRefresh, onSuccess: done });
  const s = q.data;
  const busy = enable.isPending || refresh.isPending;
  return (
    <Panel title="GitHub PR status" sub={s ? (s.enabled ? 'on' : 'off') : ''}>
      <p className="text-xs text-ink-1 m-0 mb-3">
        Asks GitHub, through your <code>gh</code> CLI, whether PRs your sessions opened were merged or closed, so threads show the exact state. Only PR numbers and repo names are sent. Off by default; while on, it checks every 30 minutes. Same as <code>argus work github enable</code>.
      </p>
      {s && !s.gh_available && <p className="text-xs text-warn m-0 mb-3">gh CLI not found: install it and run <code>gh auth login</code>.</p>}
      {s?.checked_at && <p className="text-xs text-ink-2 m-0 mb-1">last checked {ago(s.checked_at)}</p>}
      {s?.last_error && <p className="text-xs text-warn m-0 mb-3">{s.last_error}</p>}
      <div className="flex gap-2">
        {s?.enabled ? (
          <>
            <Button onClick={() => refresh.mutate()} disabled={busy}>{refresh.isPending ? 'Checking…' : 'Check now'}</Button>
            <Button onClick={() => disable.mutate()}>Turn off</Button>
          </>
        ) : (
          <Button variant="primary" onClick={() => enable.mutate()} disabled={busy}>{enable.isPending ? 'Checking…' : 'Turn on'}</Button>
        )}
      </div>
    </Panel>
  );
}
