import { useEffect, useState, type CSSProperties, type RefObject } from 'react';
import { isTimed, type Cue } from './subtitles';

const REFERENCE_WIDTH = 1920;
export type Style = {
  font: string;
  size: number;
  color: string;
  opacity: number;
  outline: number;
  outline_color: string;
  shadow: number;
  letter_spacing: number;
  line_spacing: number;
  anchor: number;
  fade_in: number;
  fade_out: number;
};

export const defaultStyle: Style = {
  font: '', size: 64, color: '#ffffff', opacity: 100, outline: 3,
  outline_color: '#101117', shadow: 4, letter_spacing: 0, line_spacing: 8,
  anchor: 2, fade_in: 0, fade_out: 0,
};

const positions = ['左下', '中央下', '右下', '左中央', '中央', '右中央', '左上', '中央上', '右上'];
const sliders = [
  { key: 'size', label: '文字サイズ', min: 16, max: 144, unit: 'px' },
  { key: 'opacity', label: '不透明度', min: 0, max: 100, unit: '%' },
  { key: 'outline', label: '縁取り', min: 0, max: 12, unit: 'px' },
  { key: 'shadow', label: '影の距離', min: 0, max: 18, unit: 'px' },
  { key: 'letter_spacing', label: '文字間隔', min: 0, max: 20, unit: 'px' },
  { key: 'line_spacing', label: '行間隔', min: 0, max: 64, unit: 'px' },
  { key: 'fade_in', label: 'フェードイン', min: 0, max: 2000, unit: 'ms' },
  { key: 'fade_out', label: 'フェードアウト', min: 0, max: 2000, unit: 'ms' },
] as const;

function stagePixels(value: number) {
  return value / REFERENCE_WIDTH * 100 + 'cqw';
}

export function SubtitleOverlay({ cues, position, playing, player, style, sample }: {
  cues: Cue[];
  position: number;
  playing: boolean;
  player: RefObject<HTMLMediaElement | null>;
  style: Style;
  sample: string;
}) {
  const [frameTime, setFrameTime] = useState(position);
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    function displayFrame() {
      if (player.current) setFrameTime(player.current.currentTime);
      frame = requestAnimationFrame(displayFrame);
    }
    displayFrame();
    return () => cancelAnimationFrame(frame);
  }, [playing, player]);
  const now = Math.round((playing ? frameTime : position) * 1000);
  const cue = cues.find(cue => isTimed(cue) && cue.start_ms <= now && now < cue.end_ms);
  const showSample = !playing && !!sample;
  const text = cue?.text || (showSample ? sample : '');
  if (!text) return null;
  let fade = 1;
  if (!showSample && cue && isTimed(cue)) {
    const scale = Math.min(1, (cue.end_ms - cue.start_ms) / Math.max(1, style.fade_in + style.fade_out));
    const fadeIn = Math.floor(style.fade_in * scale);
    const fadeOut = Math.floor(style.fade_out * scale);
    if (fadeIn) fade = Math.min(fade, (now - cue.start_ms) / fadeIn);
    if (fadeOut) fade = Math.min(fade, (cue.end_ms - now) / fadeOut);
  }
  return <StyledText text={text} style={style} fade={fade} />;
}

function StyledText({ text, style, fade = 1 }: { text: string; style: Style; fade?: number }) {
  const column = (style.anchor - 1) % 3;
  const row = Math.floor((style.anchor - 1) / 3);
  const shadow = stagePixels(style.shadow);
  const css: CSSProperties = {
    left: [7.5, 50, 92.5][column] + '%',
    top: [92.5, 50, 7.5][row] + '%',
    transform: 'translate(' + [0, -50, -100][column] + '%, ' + [-100, -50, 0][row] + '%)',
    textAlign: (['left', 'center', 'right'] as const)[column],
    fontFamily: (style.font ? JSON.stringify(style.font) + ', ' : '') + '"Yu Gothic UI", sans-serif',
    fontSize: stagePixels(style.size),
    lineHeight: stagePixels(style.size),
    gap: stagePixels(style.line_spacing),
    letterSpacing: stagePixels(style.letter_spacing),
    color: style.color,
    WebkitTextStroke: stagePixels(style.outline * 2) + ' ' + style.outline_color,
    filter: style.shadow ? 'drop-shadow(' + shadow + ' ' + shadow + ' 0 #000)' : undefined,
    opacity: style.opacity / 100 * fade,
  };
  return <div className="subtitle-overlay" style={css}>
    {text.split('\n').map((line, index) => <span key={index}>{line || '\u00a0'}</span>)}
  </div>;
}

export function SubtitleOutput({ cues, duration, filename, fonts, style, sample, onStyle, onToggleSample }: {
  cues: Cue[];
  duration: number;
  filename: string;
  fonts?: string[];
  style: Style;
  sample: string;
  onStyle: (style: Style) => void;
  onToggleSample: () => void;
}) {
  const [skip, setSkip] = useState(false);
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const placed = cues.filter(cue => isTimed(cue) && cue.text.trim()).length;
  const excluded = cues.length - placed;
  useEffect(() => { setSkip(false); setError(''); }, [cues, duration]);

  async function save(kind: 'ass' | 'srt') {
    setPending(kind);
    setError('');
    try {
      const response = await fetch('/api/subtitles/' + kind, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cues, duration_ms: Math.round(duration * 1000), style: kind === 'ass' ? style : undefined, skip_excluded: skip }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw Error(typeof data?.detail === 'string' ? data.detail : '字幕を保存できませんでした。');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = filename.replace(/\.[^.]+$/, '') + '.' + kind;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : '字幕を保存できませんでした。');
    } finally {
      setPending('');
    }
  }

  const blocked = !placed || duration <= 0 || Boolean(pending) || (excluded > 0 && !skip);
  return <section className="subtitle-output" aria-labelledby="style-title">
    <div className="section-head">
      <h3 id="style-title">字幕スタイル</h3>
      <button type="button" aria-pressed={Boolean(sample)} onClick={onToggleSample}>字幕見本</button>
    </div>
    {sample && <div className="style-sample" aria-label="字幕見本の表示">
      <StyledText text={sample} style={style} />
    </div>}
    <div className="style-fields">
      <label>フォント
        <select value={style.font} disabled={!fonts?.length} onChange={event => onStyle({...style, font:event.currentTarget.value})}>
          {!fonts?.length && <option value="">{fonts ? '利用できるフォントがありません' : 'フォントを取得中'}</option>}
          {!!fonts?.length && style.font && !fonts.includes(style.font) &&
            <option value={style.font} disabled>{style.font}（未インストール）</option>}
          {fonts?.map(font => <option key={font} value={font}>{font}</option>)}
        </select>
      </label>
      <label>表示位置
        <select value={style.anchor} onChange={event => onStyle({...style, anchor:Number(event.currentTarget.value)})}>
          {positions.map((label,index) => <option key={label} value={index+1}>{label}</option>)}
        </select>
      </label>
      <label className="style-color">文字色
        <input type="color" value={style.color} onChange={event => onStyle({...style, color:event.currentTarget.value})} />
      </label>
      <label className="style-color">縁取りの色
        <input type="color" value={style.outline_color} onChange={event => onStyle({...style, outline_color:event.currentTarget.value})} />
      </label>
      {sliders.map(field => <label key={field.key} className="style-slider">
        <span>{field.label}</span><output>{style[field.key]} {field.unit}</output>
        <input type="range" aria-label={field.label} min={field.min} max={field.max} step={field.unit === 'ms' ? 10 : 1}
          value={style[field.key]} onChange={event => onStyle({...style, [field.key]:event.currentTarget.valueAsNumber})} />
      </label>)}
    </div>
    <div className="section-head subtitle-export">
      <h3>字幕ファイル</h3>
      <div className="edit-actions">
        <button type="button" disabled={blocked} onClick={() => void save('srt')}>SRTを保存</button>
        <button type="button" disabled={blocked || !fonts?.includes(style.font)} onClick={() => void save('ass')}>ASSを保存</button>
      </div>
    </div>
    <div className="export-status">
      {!placed ? <p>本文と時刻のある字幕を追加すると保存できます。</p> : <p>保存対象 {placed} 行</p>}
      {excluded > 0 && <label><input type="checkbox" checked={skip} onChange={event => setSkip(event.currentTarget.checked)} />
        未配置または本文が空の {excluded} 行を除いて保存する</label>}
      <p>SRTは本文と時刻、ASSは字幕スタイルも保存します。</p>
      {error && <p className="editor-inline-error" role="alert">{error}</p>}
    </div>
  </section>;
}
