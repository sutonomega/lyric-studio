import assert from 'node:assert/strict';
import test from 'node:test';
import { cueForProject } from '../src/subtitles.ts';

test('recognition evidence outside the project schema does not change a saved cue', () => {
  const saved = {
    id: 'line-1', text: '確認用の字幕', start_ms: 1000, end_ms: 3000,
    status: 'review', reasons: ['partial_text_match'],
  };
  const candidate = { ...saved, match_coverage: 0.87 };
  assert.deepEqual(cueForProject(candidate), saved);
  assert.equal(candidate.match_coverage, 0.87);
});
