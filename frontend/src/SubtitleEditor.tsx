import { useLayoutEffect, useState, type Ref } from 'react';
import { cueReasons, cueStatus, isTimed, timestamp, type Cue, type Edit } from './subtitles';

function seconds(ms: number | null) {
  return ms === null ? '' : (ms / 1000).toFixed(3);
}

function milliseconds(input: string) {
  return input.trim() ? Math.round(Number(input) * 1000) : null;
}

function CueInspector({ cue, position, duration, error, onEdit }: {
  cue: Cue; position: number; duration: number; error?: string; onEdit: (edit: Edit) => void;
}) {
  const [text, setText] = useState(cue.text);
  const [start, setStart] = useState(seconds(cue.start_ms));
  const [end, setEnd] = useState(seconds(cue.end_ms));
  useLayoutEffect(() => {
    setText(cue.text);
    setStart(seconds(cue.start_ms));
    setEnd(seconds(cue.end_ms));
  }, [cue]);
  const editing = text !== cue.text || milliseconds(start) !== cue.start_ms || milliseconds(end) !== cue.end_ms;
  const sourceTime = Math.round(position * 1000);

  return <form className="cue-inspector" onSubmit={event => {
    event.preventDefault();
    onEdit({type:'update', id:cue.id, text, start:milliseconds(start), end:milliseconds(end)});
  }}>
    <label htmlFor="subtitle-text">字幕テキスト</label>
    <textarea id="subtitle-text" value={text} onChange={event => setText(event.currentTarget.value)} rows={2} />
    <div className="cue-time-fields">
      <label>開始（秒）<input name="start" type="number" min="0" max={duration} step="0.001"
        value={start} onChange={event => setStart(event.currentTarget.value)} /></label>
      <label>終了（秒）<input name="end" type="number" min="0" max={duration} step="0.001"
        value={end} onChange={event => setEnd(event.currentTarget.value)} /></label>
    </div>
    <div className="edit-actions">
      <button className="primary" type="submit" disabled={!editing}>変更を反映</button>
      {isTimed(cue) ? <>
        <button type="button" disabled={editing} onClick={() => onEdit({type:'edge', id:cue.id, edge:'start_ms', time:sourceTime})}>再生位置を開始に</button>
        <button type="button" disabled={editing} onClick={() => onEdit({type:'edge', id:cue.id, edge:'end_ms', time:sourceTime})}>再生位置を終了に</button>
      </> : <button type="button" disabled={editing} onClick={() => onEdit({type:'place', id:cue.id, position:sourceTime})}>再生位置から配置</button>}
    </div>
    {error && <p className="editor-inline-error" role="alert">{error}</p>}
    {!isTimed(cue) && <p className="filename">開始・終了を入力するか、再生位置から配置できます。</p>}
    {cue.reasons.length > 0 && <ul className="editor-reasons">{cue.reasons.map(reason => <li key={reason}>{cueReasons[reason] ?? reason}</li>)}</ul>}
  </form>;
}

export function SubtitleEditor({ headingRef, cues, selected, duration, position, error, canUndo, canRedo, onSelect, onEdit, onUndo, onRedo }: {
  headingRef: Ref<HTMLHeadingElement>;
  cues: Cue[];
  selected?: string;
  duration: number;
  position: number;
  error?: { operation: Edit['type']; message: string };
  canUndo: boolean;
  canRedo: boolean;
  onSelect: (cue: Cue) => void;
  onEdit: (edit: Edit) => void;
  onUndo: () => void;
  onRedo: () => void;
}) {
  const [offset, setOffset] = useState('');
  const index = cues.findIndex(cue => cue.id === selected);
  const cue = cues[index];
  const positionMs = Math.round(position * 1000);
  const available = duration > 0 && Number.isFinite(duration);
  const inspectorError = error && ['edge', 'place', 'update'].includes(error.operation);
  const offsetError = error?.operation === 'shift';

  return <section className="subtitle-editor" aria-labelledby="subtitle-editor-title">
    <div className="section-head editor-heading">
      <h3 id="subtitle-editor-title" ref={headingRef} tabIndex={-1}>字幕編集</h3>
      <div className="edit-actions">
        <button type="button" disabled={!canUndo} onClick={onUndo}>元に戻す</button>
        <button type="button" disabled={!canRedo} onClick={onRedo}>やり直す</button>
      </div>
    </div>
    <div className="edit-actions editor-toolbar">
      <button type="button" disabled={!available} onClick={() => onEdit({type:'add', id:crypto.randomUUID(), position:positionMs})}>字幕を追加</button>
      <button type="button" disabled={!cue} onClick={() => onEdit({type:'delete', id:cue.id})}>削除</button>
      <button type="button" disabled={!cue || !isTimed(cue)}
        onClick={() => onEdit({type:'split', id:cue.id, newId:crypto.randomUUID(), position:positionMs})}>再生位置で分割</button>
      <button type="button" disabled={!cue || index >= cues.length - 1} onClick={() => onEdit({type:'merge', id:cue.id})}>次と結合</button>
    </div>
    {error && !inspectorError && !offsetError && <p className="notice error" role="alert">{error.message}</p>}
    {cues.length > 0 && <ol className="lines editor-list" aria-label="編集中の字幕">
      {cues.map(item => <li key={item.id}>
        <button type="button" aria-pressed={item.id === selected} onClick={() => onSelect(item)}>
          <span className="editor-cue-text">{item.text || '（字幕なし）'}</span>
          <span className="cue-info">
            <span className={'cue-status ' + item.status}>{cueStatus[item.status]}</span>
            {isTimed(item) && <span>{timestamp(item.start_ms)} – {timestamp(item.end_ms)}</span>}
          </span>
        </button>
      </li>)}
    </ol>}
    {cue && <CueInspector key={cue.id} cue={cue} position={position} duration={duration} error={inspectorError ? error?.message : undefined} onEdit={onEdit} />}
    {cues.some(isTimed) && <form className="offset-form" onSubmit={event => {
      event.preventDefault();
      onEdit({type:'shift', offset:milliseconds(offset) ?? NaN});
    }}>
      <label>一括移動（秒）<input type="number" step="0.001" value={offset} placeholder="例 −0.500"
        onChange={event => setOffset(event.currentTarget.value)} /></label>
      <button type="submit" disabled={!offset.trim()}>配置済み字幕を移動</button>
      {offsetError && <p className="editor-inline-error" role="alert">{error?.message}</p>}
    </form>}
    {!cues.length && <p className="list-empty">{available ? '同期候補を適用するか、再生位置で字幕を追加できます。' : '素材を読み込むと字幕を編集できます。'}</p>}
  </section>;
}
