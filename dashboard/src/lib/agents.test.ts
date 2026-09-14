import { agentLabel, resumeHint } from './agents';

it('labels known agents and falls back to the id', () => {
  expect(agentLabel('claude_code')).toBe('Claude Code');
  expect(agentLabel('codex')).toBe('Codex');
  expect(agentLabel('hermes')).toBe('hermes');
});

it('builds the resume command per agent', () => {
  expect(resumeHint('claude_code:abc')).toBe('claude --resume abc');
  expect(resumeHint('codex:abc')).toBe('codex resume abc');
  expect(resumeHint('codex:abc/child')).toBe('codex resume abc/child');
  expect(resumeHint('plain')).toBe('plain');
});
