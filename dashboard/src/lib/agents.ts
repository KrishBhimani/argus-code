/** Display names and CLI hints per adapter id (`sessions.agent`). Unknown ids fall back to the raw id. */
export const AGENT_LABEL: Record<string, string> = { claude_code: 'Claude Code', codex: 'Codex' };

export const agentLabel = (agent: string): string => AGENT_LABEL[agent] ?? agent;

/** The command that reopens a session in its own CLI. Ids are `<agent>:<native id>`. */
export function resumeHint(sessionId: string): string {
  const i = sessionId.indexOf(':');
  if (i < 0) return sessionId;
  const agent = sessionId.slice(0, i);
  const bare = sessionId.slice(i + 1);
  if (agent === 'claude_code') return `claude --resume ${bare}`;
  if (agent === 'codex') return `codex resume ${bare}`;
  return bare;
}
