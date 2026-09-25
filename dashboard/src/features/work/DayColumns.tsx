import { useRef } from 'react';

export type Sel = { a: number; b: number } | null;
export const normSel = (a: number, b: number) => ({ a: Math.min(a, b), b: Math.max(a, b) });

/** Single-series day columns (series slot 1) with click / drag-to-select. Hand-rolled SVG, like Bars. */
export function DayColumns({ days, values, format, sel, onSel, height = 64, label }: {
  days: string[]; values: number[]; format: (v: number) => string; sel: Sel; onSel: (s: Sel) => void; height?: number; label: string;
}) {
  const drag = useRef<number | null>(null);
  const max = Math.max(1, ...values);
  const w = 100 / Math.max(1, days.length);
  const end = (i: number) => { if (drag.current !== null) { onSel(normSel(drag.current, i)); drag.current = null; } };
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="w-full select-none touch-none" style={{ height }}
      onPointerLeave={() => { drag.current = null; }}>
      {days.map((d, i) => {
        const on = sel !== null && i >= sel.a && i <= sel.b;
        const h = (values[i] / max) * (height - 2);
        return (
          <g key={d} data-testid="daycol" data-selected={on} className="cursor-pointer"
            onPointerDown={() => { drag.current = i; }}
            onPointerEnter={() => { if (drag.current !== null) onSel(normSel(drag.current, i)); }}
            onPointerUp={() => end(i)}>
            <title>{`${d} · ${format(values[i])}`}</title>
            <rect x={i * w} y={0} width={w} height={height} fill={on ? 'var(--color-accent)' : 'transparent'} fillOpacity={on ? 0.1 : 0} />
            <rect x={i * w + w * 0.15} y={height - h} width={w * 0.7} height={h} rx={0.6} fill="#3987e5" fillOpacity={sel && !on ? 0.35 : 1} />
          </g>
        );
      })}
    </svg>
  );
}
