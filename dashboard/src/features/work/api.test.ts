import { WorkOverview, WorkProjects, WorkStatus, WorkTimeline } from './api';

it('parses the server shapes', () => {
  expect(WorkStatus.parse({ enabled: true, last_scan_at: null, repos: 1, errors: {} }).enabled).toBe(true);
  WorkProjects.parse({ projects: [{ id: 1, display_name: 'p', root: '/r', present: true, last_error: null, active_ms_30d: 1, active_estimated: false, commits_30d: 1, daily_active_ms: [0, 1], last_worked_at: null,
    folder_ids: [1, 2], folders: [{ id: 1, root: '/r', present: true }, { id: 2, root: '/old/r', present: false }] }] });
  WorkOverview.parse({
    tiles: { active_ms: 1, active_estimated: false, cost: 1, commits: 1, cost_per_commit: null }, prior: { active_ms: 0, active_estimated: false, cost: 0, commits: 0, cost_per_commit: null },
    daily: { days: ['2026-09-01'], active_ms: [1], commits: [0] },
    stories: [{ title: 't', branch: null, prs: [], sessions: 1, session_ids: ['a'], active_ms: 1, active_estimated: false, cost: 1, first_ts: 'x', last_ts: 'y',
      commits: { total: 0, exact: 0, coauthored: 0, inferred: 0 }, added: 0, deleted: 0, files: 0, skills: [] }],
    breakdowns: { branches: [], skills: [], files: [] },
  });
  WorkTimeline.parse({ days: [{ day: '2026-09-01', items: [{ kind: 'commit', sha: 'a', subject: 's', author_name: 'x', authored_at: 't', evidence: null, session_id: null, added: 1, deleted: 0, sort_ts: 't' }] }] });
});
