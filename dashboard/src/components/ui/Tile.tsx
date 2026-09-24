import { Icon } from './Icon';
import { deltaTone, type Delta } from '@/lib/analysis/deltas';
import { pct } from '@/lib/format/format';

export function Tile({ label, value, delta, upIsBad = false, sub, note, tone = 'default', fmt }: {
  label: string; value: string; delta?: Delta | null; upIsBad?: boolean; sub?: string; note?: string; tone?: 'default' | 'crit'; fmt?: (d: Delta) => string;
}) {
  const t = delta ? deltaTone(delta, upIsBad) : 'neutral';
  const cls = t === 'bad' ? 'text-crit-ink' : t === 'ok' ? 'text-good' : 'text-ink-1';
  const sign = delta && delta.abs >= 0 ? '+' : '−';
  // No prior value (pct null): a raw difference is meaningless without the tile's
  // own formatter (it printed "+55.413754999999995" for a cost), so say "new".
  const text = delta
    ? fmt
      ? fmt(delta)
      : delta.pct != null
        ? `${sign}${pct(Math.abs(delta.pct), 0)}`
        : delta.abs === 0
          ? pct(0, 0)
          : 'new'
    : '';
  return (
    <div className={`bg-bg-1 border rounded-md px-4 py-3.5 flex flex-col gap-1.5 min-w-0 ${tone === 'crit' ? 'border-crit/50' : 'border-line'}`}>
      <span className="text-[11px] text-ink-1 font-medium">{label}</span>
      <span className={`text-[26px] font-semibold leading-[1.1] num tracking-tight truncate ${tone === 'crit' ? 'text-crit-ink' : ''}`}>{value}</span>
      <span className="flex items-center gap-1.5 text-[11px] font-mono text-ink-2 min-h-4">
        {delta && (
          <span data-testid="delta" className={`inline-flex items-center gap-0.5 font-medium ${cls}`}>
            <Icon name={delta.dir === 'down' ? 'down' : 'up'} size={10} />
            {text}
          </span>
        )}
        {sub && <span className="truncate">{sub}</span>}
        {note && <span className="ml-auto text-ink-2 truncate">{note}</span>}
      </span>
    </div>
  );
}
