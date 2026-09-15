import type { Session, Window } from '@/lib/api/client';
import { inWindow } from '@/lib/analysis/rollups';
import { agentLabel } from '@/lib/agents';

export type Filters = { q: string; project: string | null; model: string | null; window: Window; hasErrors: boolean; agent?: string | null };

export function applyFilters(sessions: Session[], f: Filters, errorsById: Record<string, number>, today: Date): Session[] {
  const q = f.q.trim().toLowerCase();
  return inWindow(sessions, f.window, today).filter(
    (s) =>
      (!q ||
        s.project_path.toLowerCase().includes(q) ||
        s.primary_model.toLowerCase().includes(q) ||
        s.id.toLowerCase().includes(q) ||
        agentLabel(s.agent).toLowerCase().includes(q)) &&
      (!f.project || s.project_path === f.project) &&
      (!f.model || s.primary_model === f.model) &&
      (!f.agent || s.agent === f.agent) &&
      (!f.hasErrors || (errorsById[s.id] ?? 0) > 0),
  );
}

/** `/api/sessions` carries no per-session error count; use it only if the adapter put one in metadata. */
export const errorCount = (s: Session): number | null =>
  typeof s.metadata?.tool_error_count === 'number' ? (s.metadata.tool_error_count as number) : null;
