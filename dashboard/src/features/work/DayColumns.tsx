import { useRef, useState } from 'react';

export type Sel = { a: number; b: number } | null;
export const normSel = (a: number, b: number) => ({ a: Math.min(a, b), b: Math.max(a, b) });

/** Single-series day columns (series slot 1) with click / drag-to-select. Hand-rolled SVG, like Bars. */
export function DayColumns({ days, values, format, sel, onSel, height = 64, label }: {
  days: string[]; values: number[]; format: (v: number) => string; sel: Sel; onSel: (s: Sel) => void; height?: number; label: string;
}) {
  // A drag is previewed locally and committed once on release: committing on every
  // column crossed fired one API query per day, which queued up behind each other.
  const drag = useRef<number | null>(null);
  const [preview, setPreview] = useState<Sel>(null);
  const shown = preview ?? sel;
  const max = Math.max(1, ...values);
  const w = 100 / Math.max(1, days.length);
  const end = (i: number) => {
    if (drag.current !== null) onSel(normSel(drag.current, i));
    drag.current = null;
    setPreview(null);
  };
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="w-full select-none touch-none" style={{ height }}
      onPointerLeave={() => { drag.current = null; setPreview(null); }}>
      {days.map((d, i) => {
        const on = shown !== null && i >= shown.a && i <= shown.b;
        const h = (values[i] / max) * (height - 2);
        return (
          <g key={d} data-testid="daycol" data-selected={on} className="cursor-pointer"
            onPointerDown={() => { drag.current = i; setPreview({ a: i, b: i }); }}
            onPointerEnter={() => { if (drag.current !== null) setPreview(normSel(drag.current, i)); }}
            onPointerUp={() => end(i)}>
            <title>{`${d} · ${format(values[i])}`}</title>
            <rect x={i * w} y={0} width={w} height={height} fill={on ? 'var(--color-accent)' : 'transparent'} fillOpacity={on ? 0.1 : 0} />
            <rect x={i * w + w * 0.15} y={height - h} width={w * 0.7} height={h} rx={0.6} fill="#3987e5" fillOpacity={shown && !on ? 0.35 : 1} />
          </g>
        );
      })}
    </svg>
  );
}
