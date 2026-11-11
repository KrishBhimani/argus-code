import type { Session } from '@/lib/api/client';

const LABEL: Record<string, string> = { claude_code: 'Claude Code', codex: 'Codex' };

/** Human name for an adapter id ("claude_code" -> "Claude Code"). */
export const agentLabel = (agent: string): string => LABEL[agent] ?? agent;

const SHORT: Record<string, string> = { claude_code: 'claude', codex: 'codex' };

/** Compact lowercase name for pills ("claude_code" -> "claude"). */
export const agentShortName = (agent: string): string => SHORT[agent] ?? agent;

/** Session ids are "<agent>:<native id>". */
export const agentOfId = (id: string): string => (id.includes(':') ? id.slice(0, id.indexOf(':')) : 'claude_code');

/** CLI command that resumes this session, when the agent has one. */
export function resumeCommand(agent: string, bareId: string): string | null {
  if (agent === 'claude_code') return `claude --resume ${bareId}`;
  if (agent === 'codex') return `codex resume ${bareId}`;
  return null;
}

/** Agents to offer in a filter: empty unless there is a choice to make. */
export function agentOptions(sessions: Session[]): string[] {
  const all = [...new Set(sessions.map((s) => s.agent))].sort();
  return all.length > 1 ? all : [];
}
