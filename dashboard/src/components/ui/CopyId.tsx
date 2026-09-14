import { useState } from 'react';
import { Icon } from './Icon';

/** Short id chip that copies the bare native session id on click. `hint` is the agent's resume command (see `lib/agents.ts`). */
export function CopyId({ value, chars = 8, hint }: { value: string; chars?: number; hint?: string }) {
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
      title={`Session id ${bare}\nClick to copy${hint ? ` (use with: ${hint})` : ''}`}
      className={`inline-flex items-center gap-1.5 font-mono text-[11px] px-1.5 h-5 rounded-sm border transition-colors ${done ? 'text-good border-good/40 bg-good/10' : 'text-ink-1 bg-bg-3 border-transparent hover:border-line-2 hover:text-ink-0'}`}
    >
      {done ? <Icon name="check" size={10} /> : null}
      {done ? 'copied' : chars ? bare.slice(0, chars) : 'copy'}
    </button>
  );
}
