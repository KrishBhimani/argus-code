import { agentLabel, agentOfId, agentOptions, agentShortName, resumeCommand } from './agents';
import { mk } from '@/lib/analysis/testutil';

it('labels known agents and passes unknown ones through', () => {
  expect(agentLabel('claude_code')).toBe('Claude Code');
  expect(agentLabel('codex')).toBe('Codex');
  expect(agentLabel('hermes')).toBe('hermes');
});

it('derives the agent from a session id', () => {
  expect(agentOfId('codex:01a1/02b2')).toBe('codex');
  expect(agentOfId('claude_code:abc')).toBe('claude_code');
});

it('gives each agent its own resume command', () => {
  expect(resumeCommand('claude_code', 'abc')).toBe('claude --resume abc');
  expect(resumeCommand('codex', 'T')).toBe('codex resume T');
  expect(resumeCommand('other', 'x')).toBeNull();
});

it('offers an agent filter only when more than one agent is present', () => {
  expect(agentOptions([mk({ id: 'claude_code:a' }), mk({ id: 'claude_code:b' })])).toEqual([]);
  expect(agentOptions([mk({ id: 'claude_code:a' }), mk({ id: 'codex:b', agent: 'codex' })])).toEqual(['claude_code', 'codex']);
});

it('gives a compact pill name per agent', () => {
  expect(agentShortName('claude_code')).toBe('claude');
  expect(agentShortName('codex')).toBe('codex');
  expect(agentShortName('hermes')).toBe('hermes');
});
