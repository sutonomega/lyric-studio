import { useEffect, useRef, useState, type PointerEvent } from 'react';
import WaveSurfer from 'wavesurfer.js';
import type { Cue } from './SyncPanel';

export type WaveformData = { duration_ms: number; peaks: number[] };
type TimedCue = Cue & { start_ms: number; end_ms: number };
type Boundary = 'start_ms' | 'end_ms';
type Drag = { id: string; edge: Boundary; time: number; origin: number };

function timestamp(ms: number, detailed = false) {
  const seconds = String(Math.floor(ms / 1000) % 60).padStart(2, '0');
  const fraction = detailed ? '.' + String(ms % 1000).padStart(3, '0') : '';
  return Math.floor(ms / 60000) + ':' + seconds + fraction;
}

function Waveform({ data, duration }: { data: WaveformData; duration: number }) {
  const container = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!container.current || data.duration_ms <= 0 || !data.peaks.length) return;
    let disposed = false;
    const wave = WaveSurfer.create({
      container: container.current, height: 64, waveColor: '#85a7da', progressColor: '#85a7da',
      cursorWidth: 0, interact: false, autoScroll: false,
    });
    void wave.load('', [data.peaks], data.duration_ms / 1000).catch(() => {
      if (!disposed) setFailed(true);
    });
    return () => { disposed = true; wave.destroy(); };
  }, [data]);
  return failed ? <p className="timeline-message">波形を表示できませんでした。</p> :
    <div ref={container} className="waveform" style={{ width: (data.duration_ms / duration) * 100 + '%' }} aria-hidden="true" />;
}

export function Timeline({ duration, position, waveform, cues, onSeek, onEdit }: {
  duration: number;
  position: number;
  waveform?: WaveformData | null;
  cues: Cue[];
  onSeek: (seconds: number) => void;
  onEdit: (id: string, edge: Boundary, ms: number) => void;
}) {
  const durationMs = Number.isFinite(duration) ? Math.round(duration * 1000) : 0;
  const positionMs = Math.min(durationMs, Math.max(0, Math.round(position * 1000)));
  const [zoom, setZoom] = useState(1);
  const [selected, setSelected] = useState<string>();
  const [draft, setDraft] = useState<{ id: string; edge: Boundary; time: number }>();
  const viewport = useRef<HTMLDivElement>(null);
  const scale = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const timed = cues.filter((cue): cue is TimedCue => cue.start_ms !== null && cue.end_ms !== null);
  const current = timed.find(cue => cue.id === selected);
  const percent = (ms: number) => (Math.min(durationMs, ms) / durationMs) * 100 + '%';

  useEffect(() => {
    const visible = viewport.current;
    const surface = scale.current;
    if (!visible || !surface || !durationMs || drag.current) return;
    const x = positionMs / durationMs * surface.clientWidth;
    if (x < visible.scrollLeft || x > visible.scrollLeft + visible.clientWidth) {
      visible.scrollLeft = x - visible.clientWidth / 2;
    }
  }, [positionMs, durationMs, zoom]);

  function timeAt(clientX: number) {
    const rect = scale.current?.getBoundingClientRect();
    return rect?.width ? Math.round((clientX - rect.left) / rect.width * durationMs) : 0;
  }

  function constrain(cue: TimedCue, edge: Boundary, ms: number) {
    const index = timed.indexOf(cue);
    const lower = edge === 'start_ms' ? timed[index - 1]?.end_ms ?? 0 : cue.start_ms + 1;
    const upper = edge === 'start_ms' ? cue.end_ms - 1 : Math.min(durationMs, timed[index + 1]?.start_ms ?? durationMs);
    return upper < lower ? cue[edge] : Math.max(lower, Math.min(ms, upper));
  }

  function begin(event: PointerEvent<HTMLButtonElement>, cue: TimedCue, edge: Boundary) {
    if (event.button !== 0 || drag.current) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { id: cue.id, edge, time: cue[edge], origin: timeAt(event.clientX) };
  }

  function move(event: PointerEvent<HTMLButtonElement>, cue: TimedCue, commit: boolean) {
    const active = drag.current;
    if (!active || !event.currentTarget.hasPointerCapture(event.pointerId) || active.id !== cue.id) return;
    const time = constrain(cue, active.edge, active.time + timeAt(event.clientX) - active.origin);
    if (commit) {
      drag.current = null;
      setDraft(undefined);
      if (time !== active.time) {
        onEdit(cue.id, active.edge, time);
        if (active.edge === 'start_ms') onSeek(time / 1000);
      }
    } else {
      setDraft({ id: cue.id, edge: active.edge, time });
    }
  }

  function release() {
    drag.current = null;
    setDraft(undefined);
  }

  function handle(cue: TimedCue, edge: Boundary) {
    const label = edge === 'start_ms' ? '開始' : '終了';
    return <button type="button" className={edge === 'start_ms' ? 'edge start' : 'edge end'}
      aria-label={label + '時刻を調整'} title={label + '時刻を調整'}
      onPointerDown={event => begin(event, cue, edge)}
      onPointerMove={event => move(event, cue, false)}
      onPointerUp={event => move(event, cue, true)}
      onLostPointerCapture={release}
      onPointerCancel={release}
      onKeyDown={event => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        const step = (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 500 : 50);
        const time = constrain(cue, edge, cue[edge] + step);
        if (time === cue[edge]) return;
        onEdit(cue.id, edge, time);
        if (edge === 'start_ms') onSeek(time / 1000);
      }} />;
  }

  const goal = durationMs / (zoom * 6);
  const tickSize = [250, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000]
    .find(step => step >= goal) ?? Math.ceil(goal / 60000) * 60000;
  const activeId = timed.find(cue => cue.start_ms <= positionMs && positionMs < cue.end_ms)?.id;

  return <section className="timeline-panel" aria-labelledby="timeline-title">
    <div className="timeline-heading">
      <h2 id="timeline-title">タイムライン</h2>
      {durationMs > 0 && <div className="timeline-tools">
        <output aria-label="タイムラインの再生位置">{timestamp(positionMs, true)} / {timestamp(durationMs)}</output>
        <label>拡大 <select value={zoom} onChange={event => setZoom(Number(event.target.value))}>
          {[1, 2, 4, 8, 16].map(value => <option value={value} key={value}>{value}×</option>)}
        </select></label>
      </div>}
    </div>
    {durationMs > 0 ? <>
      <div className="timeline-scroll" ref={viewport}>
        <div className="timeline-scale" ref={scale} style={{ width: zoom * 100 + '%' }}>
          <div className="time-ruler" aria-hidden="true">
            {Array.from({ length: Math.ceil(durationMs / tickSize) }, (_, index) => {
              const time = index * tickSize;
              return <span key={time} className="time-tick" style={{ left: percent(time) }}>
                {timestamp(time, tickSize < 1000)}
              </span>;
            })}
          </div>
          <div className="wave-track" onClick={event => onSeek(Math.min(durationMs, Math.max(0, timeAt(event.clientX))) / 1000)}>
            {waveform ? <Waveform data={waveform} duration={durationMs} /> :
              <p className="timeline-message">波形を取得できませんでした。</p>}
          </div>
          <div className="cue-track" aria-label="字幕トラック">
            {!timed.length && <p className="timeline-message">同期候補をプレビューに適用すると字幕が表示されます。</p>}
            {timed.map(cue => {
              const start = draft?.id === cue.id && draft.edge === 'start_ms' ? draft.time : cue.start_ms;
              const end = draft?.id === cue.id && draft.edge === 'end_ms' ? draft.time : cue.end_ms;
              const chosen = cue.id === selected;
              return <div key={cue.id} className={'cue-block' + (chosen ? ' selected' : '') + (activeId === cue.id ? ' current' : '')}
                style={{ left: percent(start), width: 'calc(' + percent(end) + ' - ' + percent(start) + ')' }}>
                <button className="cue-body" type="button" aria-pressed={chosen} title={cue.text}
                  onClick={() => { setSelected(cue.id); onSeek(cue.start_ms / 1000); }}>{cue.text}</button>
                {chosen && handle(cue, 'start_ms')}
                {chosen && handle(cue, 'end_ms')}
              </div>;
            })}
          </div>
          <div className="timeline-playhead" style={{ left: percent(positionMs) }} aria-hidden="true" />
        </div>
      </div>
      {current && <div className="timeline-selection">
        <span>{current.text}</span>
        <output aria-label="選択した字幕の時刻">
          {timestamp(draft?.edge === 'start_ms' ? draft.time : current.start_ms, true)} – {timestamp(draft?.edge === 'end_ms' ? draft.time : current.end_ms, true)}
        </output>
        <span className="filename">字幕の両端をドラッグして時刻を調整</span>
      </div>}
    </> : <p className="timeline-message">動画または音声を読み込むとタイムラインが表示されます。</p>}
  </section>;
}
