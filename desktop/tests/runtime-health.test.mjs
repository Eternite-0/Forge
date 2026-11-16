import test from 'node:test';
import assert from 'node:assert/strict';
import { heartbeatState } from '../src/runtimeHealth.ts';

test('an ok heartbeat expires at its server timeout using Unix seconds', () => {
  const row = { component: 'worker', state: 'ok', last_seen: 1000, timeout_seconds: 60 };
  assert.equal(heartbeatState(row, 1059), 'healthy');
  assert.equal(heartbeatState(row, 1060), 'stale');
  assert.equal(heartbeatState({ ...row, state: 'retrying' }, 1001), 'attention');
});
test('invalid or future heartbeat timestamps never produce a healthy status', () => {
  const row = { component: 'worker', state: 'ok', last_seen: 1000, timeout_seconds: 60 };
  assert.equal(heartbeatState({ ...row, last_seen: 1000000 }, 1000), 'unknown');
  assert.equal(heartbeatState({ ...row, timeout_seconds: 0 }, 1001), 'unknown');
  assert.equal(heartbeatState({ ...row, last_seen: NaN }, 1001), 'unknown');
});
