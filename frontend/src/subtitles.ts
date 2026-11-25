export type Cue = {
  id: string;
  text: string;
  start_ms: number | null;
  end_ms: number | null;
  status: 'candidate' | 'review' | 'unplaced' | 'edited';
  reasons: string[];
};
export type TimedCue = Cue & { start_ms: number; end_ms: number };
export type Boundary = 'start_ms' | 'end_ms';
export type Track = { sourceJob?: string; cues: Cue[] };
type History = { past: Track[]; current: Track; future: Track[] };
export type Edit =
  | { type: 'add'; id: string; position: number }
  | { type: 'delete'; id: string }
  | { type: 'update'; id: string; text: string; start: number | null; end: number | null }
  | { type: 'edge'; id: string; edge: Boundary; time: number }
  | { type: 'place'; id: string; position: number }
  | { type: 'split'; id: string; newId: string; position: number }
  | { type: 'merge'; id: string }
  | { type: 'shift'; offset: number };

export function emptyHistory(): History {
  return { past: [], current: { cues: [] }, future: [] };
}
export function commit(history: History, current: Track): History {
  return { past: [...history.past, history.current], current, future: [] };
}
export function undo(history: History): History {
  const previous = history.past.at(-1);
  return previous ? { past: history.past.slice(0, -1), current: previous, future: [history.current, ...history.future] } : history;
}
export function redo(history: History): History {
  const next = history.future[0];
  return next ? { past: [...history.past, history.current], current: next, future: history.future.slice(1) } : history;
}

export function isTimed(cue: Cue): cue is TimedCue {
  return cue.start_ms !== null && cue.end_ms !== null;
}

export function timeBounds(cues: Cue[], index: number, duration: number, replaced = 1) {
  const before = cues.slice(0, index).reverse().find(isTimed);
  const after = cues.slice(index + replaced).find(isTimed);
  return { start: before?.end_ms ?? 0, end: Math.min(after?.start_ms ?? duration, duration) };
}

function withTimes(cue: Cue, start: number | null, end: number | null): Cue {
  if (cue.start_ms === start && cue.end_ms === end) return cue;
  if (start === null) return { ...cue, start_ms: null, end_ms: null, status: 'unplaced', reasons: [] };
  const review = cue.status === 'review';
  return { ...cue, start_ms: start, end_ms: end, status: review ? 'review' : 'edited', reasons: review ? cue.reasons : [] };
}

function validate(start: number | null, end: number | null, bounds: { start: number; end: number }) {
  if (start === null && end === null) return;
  if (start === null || end === null || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end <= start) {
    throw Error('開始と終了を秒で入力し、終了を開始より後にしてください。');
  }
  if (start < bounds.start || end > bounds.end) {
    throw Error('他の字幕と重ならず、素材の長さに収まる時刻を指定してください。');
  }
}

export function editCues(cues: Cue[], edit: Edit, duration: number): { cues: Cue[]; selection?: string } {
  if (edit.type === 'shift') {
    if (!Number.isSafeInteger(edit.offset)) throw Error('移動する秒数を入力してください。');
    if (!edit.offset || !cues.some(isTimed)) return { cues };
    const shifted = cues.map(cue => {
      if (!isTimed(cue)) return cue;
      const start = cue.start_ms + edit.offset;
      const end = cue.end_ms + edit.offset;
      if (start < 0 || end > duration) throw Error('一括移動すると素材の長さを超える字幕があります。');
      return withTimes(cue, start, end);
    });
    return { cues: shifted };
  }
  if (edit.type === 'add') {
    const next = cues.findIndex(cue => isTimed(cue) && cue.start_ms > edit.position);
    const index = next < 0 ? cues.length : next;
    const bounds = timeBounds(cues, index, duration, 0);
    const end = Math.min(edit.position + 2000, bounds.end);
    const blank: Cue = { id: edit.id, text: '', start_ms: null, end_ms: null, status: 'unplaced', reasons: [] };
    const cue = edit.position >= bounds.start && end > edit.position ? withTimes(blank, edit.position, end) : blank;
    return { cues: [...cues.slice(0, index), cue, ...cues.slice(index)], selection: cue.id };
  }
  const index = cues.findIndex(cue => cue.id === edit.id);
  if (index < 0) return { cues };
  const cue = cues[index];
  const bounds = timeBounds(cues, index, duration);
  function replace(items: Cue[], count = 1, selection = cue.id) {
    return { cues: [...cues.slice(0, index), ...items, ...cues.slice(index + count)], selection };
  }
  if (edit.type === 'delete') return replace([], 1, cues[index + 1]?.id ?? cues[index - 1]?.id ?? '');
  if (edit.type === 'split') {
    if (!isTimed(cue) || edit.position <= cue.start_ms || edit.position >= cue.end_ms) {
      throw Error('分割する位置を、選択した字幕の開始と終了の間に合わせてください。');
    }
    const characters = Array.from(new Intl.Segmenter('ja', { granularity: 'grapheme' }).segment(cue.text), item => item.segment);
    if (characters.length < 2) throw Error('分割する字幕には2文字以上入力してください。');
    const fraction = (edit.position - cue.start_ms) / (cue.end_ms - cue.start_ms);
    const at = Math.max(1, Math.min(characters.length - 1, Math.round(characters.length * fraction)));
    const first = { ...withTimes(cue, cue.start_ms, edit.position), text: characters.slice(0, at).join('') };
    const second = { ...withTimes(cue, edit.position, cue.end_ms), id: edit.newId, text: characters.slice(at).join('') };
    return replace([first, second], 1, second.id);
  }
  if (edit.type === 'merge') {
    const next = cues[index + 1];
    if (!next) return { cues };
    const start = cue.start_ms ?? next.start_ms;
    const end = next.end_ms ?? cue.end_ms;
    const status = start === null ? 'unplaced' : cue.status === 'review' || next.status === 'review' ? 'review' : 'edited';
    const reasons = start === null ? [...cue.reasons, ...next.reasons] :
      [cue, next].flatMap(item => item.status === 'review' ? item.reasons : []);
    return replace([{ ...cue, start_ms: start, end_ms: end, status,
      text: [cue.text, next.text].filter(Boolean).join('\n'), reasons: [...new Set(reasons)],
    }], 2);
  }
  const start = edit.type === 'update' ? edit.start : edit.type === 'place' ? edit.position :
    edit.edge === 'start_ms' ? edit.time : cue.start_ms;
  const end = edit.type === 'update' ? edit.end : edit.type === 'place' ? Math.min(edit.position + 2000, bounds.end) :
    edit.edge === 'end_ms' ? edit.time : cue.end_ms;
  validate(start, end, bounds);
  const timed = withTimes(cue, start, end);
  const updated = edit.type === 'update' && cue.text !== edit.text ? { ...timed, text: edit.text } : timed;
  return updated === cue ? { cues } : replace([updated]);
}

export const cueStatus = { candidate: '候補', review: '要確認', unplaced: '未配置', edited: '手動' };
export const cueReasons: Record<string, string> = {
  partial_text_match: '認識結果と歌詞の一致が部分的です。',
  partial_boundary_match: '認識結果で行の端を確認できませんでした。',
  short_line: '短い歌詞行のため対応を確認してください。',
  segment_boundary_estimate: '認識区間から時刻を推定しました。',
  reading_and_surface_disagree: '読みと表記から得た時刻に差があります。',
  insufficient_text_match: '認識結果との一致が不足しています。',
  implausible_span: '一致した語の時刻が大きく離れています。',
  long_internal_gap: '行内に長い空白があります。',
  invalid_or_overlapping_time: '時刻の順序または重複を解消できませんでした。',
  experimental_recovery_requires_review: '複数の認識とボーカル音量を基に補完しました。',
  experimental_boundary_requires_review: '複数の認識から境界を調整しました。',
  vocal_tail_before_pause: '分離ボーカルが静かになる位置まで終了を延ばしました。',
  trailing_vocal_silence: '分離ボーカルの末尾の無音を除きました。',
  uncertain_trailing_vocal_silence: '末尾に静かな区間があります。時刻を確認してください。',
};

export function timestamp(ms: number, detailed = true) {
  const seconds = String(Math.floor(ms / 1000) % 60).padStart(2, '0');
  const fraction = detailed ? '.' + String(ms % 1000).padStart(3, '0') : '';
  return Math.floor(ms / 60000) + ':' + seconds + fraction;
}
