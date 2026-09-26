import { Chip } from '@/components/ui/Chip';

/** Span in days; 0 = all time (the API starts at the project's first commit or session). */
const PERIODS: { days: number; label: string }[] = [
  { days: 30, label: '30 days' }, { days: 90, label: '90 days' }, { days: 365, label: '1 year' }, { days: 0, label: 'All time' },
];

export const periodLabel = (days: number): string => (days === 0 ? 'all time' : `last ${days} days`);

export function PeriodChips({ days, onChange }: { days: number; onChange: (days: number) => void }) {
  return (
    <div className="flex gap-1.5" role="group" aria-label="Period">
      {PERIODS.map((p) => <Chip key={p.days} active={days === p.days} onClick={() => onChange(p.days)}>{p.label}</Chip>)}
    </div>
  );
}
