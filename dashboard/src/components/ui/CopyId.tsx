import { useState } from 'react';
import { Icon } from './Icon';
import { agentOfId, resumeCommand } from '@/lib/agents';

/** Short id chip that copies the session's native id (usable with the agent's resume command). */
export function CopyId({ value, chars = 8 }: { value: string; chars?: number }) {
  const [done, setDone] = useState(false);
  const bare = value.includes(':') ? value.slice(value.indexOf(':') + 1) : value;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(bare);
      setDone(true);
      setTimeout(() => setDone(false), 1400);
    } catch {
      window.prompt('Copy the session id', bare);
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title={(() => { const cmd = resumeCommand(agentOfId(value), bare); return `Session id ${bare}\nClick to copy${cmd ? ` (use with: ${cmd})` : ''}`; })()}
      className={`inline-flex items-center gap-1.5 font-mono text-[11px] px-1.5 h-5 rounded-sm border transition-colors ${done ? 'text-good border-good/40 bg-good/10' : 'text-ink-1 bg-bg-3 border-transparent hover:border-line-2 hover:text-ink-0'}`}
    >
      {done ? <Icon name="check" size={10} /> : null}
      {done ? 'copied' : chars ? bare.slice(0, chars) : 'copy'}
    </button>
  );
}
