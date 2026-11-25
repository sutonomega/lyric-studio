import { useEffect, useRef, useState } from 'react';
import { isTimed, type Cue } from './subtitles';
import type { Style } from './SubtitleOutput';

export type VideoRatio = '16:9' | '9:16';
type Job = {
  id: string;
  state: 'queued' | 'uploading' | 'running' | 'cancelling' | 'cancelled' | 'succeeded' | 'failed';
  ratio: VideoRatio;
  stage: string;
  error: string | null;
  percent: number | null;
};

function active(job?: Job) {
  return job && ['queued', 'uploading', 'running', 'cancelling'].includes(job.state);
}

class VideoError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

async function request<T>(route: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api/video/' + route, options);
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new VideoError(typeof error?.detail === 'string' ? error.detail : '動画書き出しに接続できません。', response.status);
  }
  return response.json();
}

function extension(file: File) {
  return file.name.split('.').at(-1)?.toLowerCase();
}

export function VideoOutput({ media, image, duration, cues, style, ratio, onRatio }: {
  media?: File;
  image?: File;
  duration: number;
  cues: Cue[];
  style: Style;
  ratio: VideoRatio;
  onRatio: (ratio: VideoRatio) => void;
}) {
  const [available, setAvailable] = useState<boolean>();
  const [job, setJob] = useState<Job>();
  const [ready, setReady] = useState<Job>();
  const [filename, setFilename] = useState('');
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [notice, setNotice] = useState('');
  const [retrying, setRetrying] = useState(false);
  const [skip, setSkip] = useState(false);
  const transmitting = useRef<AbortController | null>(null);
  const suggestedName = useRef('');
  const activeId = active(job) ? job?.id : undefined;
  const kind = media && extension(media);
  const needsImage = Boolean(media && kind !== 'mp4');
  const placed = cues.filter(cue => isTimed(cue) && cue.text.trim()).length;
  const excluded = cues.length - placed;

  useEffect(() => {
    const controller = new AbortController();
    void request<{ available: boolean }>('config', { signal: controller.signal })
      .then(value => setAvailable(value.available), () => { if (!controller.signal.aborted) setAvailable(false); });
    return () => controller.abort();
  }, []);
  useEffect(() => { setSkip(false); }, [cues]);

  useEffect(() => {
    if (!activeId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    async function poll() {
      try {
        const state = await request<Job>('jobs/' + activeId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setRetrying(false);
        setJob(state);
        if (state.state === 'succeeded') {
          setReady(state);
          setFilename(suggestedName.current);
        }
        if (!active(state)) return;
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof VideoError && error.status === 404) {
          setJob(undefined);
          setRetrying(false);
          setNotice(error.message);
          return;
        }
        setRetrying(true);
      }
      timer = window.setTimeout(poll, 350);
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [activeId]);

  async function start() {
    if (!media || (needsImage && !image)) return;
    setNotice('');
    setStarting(true);
    const controller = new AbortController();
    transmitting.current = controller;
    let created: Job | undefined;
    try {
      created = await request<Job>('jobs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ratio, media_kind: kind, image_kind: needsImage && image ? (extension(image) === 'png' ? 'png' : 'jpg') : null,
          duration_ms: Math.round(duration * 1000), cues, style, skip_excluded: skip,
        }),
      });
      const stem = media.name.replace(/\.[^.]+$/, '') || 'Lyric Studio';
      suggestedName.current = stem + ' ' + ratio.replace(':', 'x') + ' 字幕付き';
      setJob(created);
      const parts: [string, File][] = [['media', media]];
      if (needsImage && image) parts.push(['image', image]);
      for (const [part, file] of parts) {
        const state = await request<Job>('jobs/' + created.id + '/' + part, {
          method: 'PUT', body: file, signal: controller.signal,
        });
        if (state.state === 'failed' || state.state === 'cancelled') break;
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setNotice(error instanceof Error ? error.message : '書き出しを開始できませんでした。');
        if (created) await request('jobs/' + created.id + '/cancel', { method: 'POST' }).catch(() => {});
      }
    } finally {
      setStarting(false);
    }
  }

  async function cancel() {
    if (!activeId) return;
    setCancelling(true);
    setNotice('');
    const controller = transmitting.current;
    try {
      await request('jobs/' + activeId + '/cancel', { method: 'POST' });
      controller?.abort();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'キャンセルできませんでした。');
    } finally {
      setCancelling(false);
    }
  }

  const preparing = starting && !activeId;
  const message = !media ? '動画、または音声と背景画像を読み込んでください。' :
    needsImage && !image ? '背景画像を選択してください。' : !placed ? '時刻と本文のある字幕を用意してください。' :
    !style.font ? '字幕スタイルでフォントを選択してください。' : '';
  const outputName = (filename.trim().replace(/\.mp4$/i, '').replace(/[<>:"/\\|?*]/g, '_').replace(/[. ]+$/, '') || 'Lyric Studio') + '.mp4';

  return <section className="video-output" aria-labelledby="video-output-title">
    <div className="section-head">
      <h3 id="video-output-title">MP4出力</h3>
      {activeId ? <button type="button" disabled={cancelling || job?.state === 'cancelling'} onClick={() => void cancel()}>書き出しをキャンセル</button> :
        <button type="button" className="primary" disabled={!available || !duration || !!message || starting || cancelling || (excluded > 0 && !skip)}
          onClick={() => void start()}>MP4を書き出す</button>}
    </div>
    <div className="video-output-settings">
      <label>画面比率
        <select value={ratio} onChange={event => onRatio(event.currentTarget.value as VideoRatio)}>
          <option value="16:9">16:9 横長・1920×1080</option>
          <option value="9:16">9:16 縦長・1080×1920</option>
        </select>
      </label>
      {available === false && <p>FFmpegを指定してLyric Studioを起動してください。</p>}
      {message && <p>{message}</p>}
      {excluded > 0 && <label className="video-exclude"><input type="checkbox" checked={skip} onChange={event => setSkip(event.currentTarget.checked)} />
        未配置または本文が空の {excluded} 行を除いて書き出す</label>}
      {(job || preparing) && <div className="video-progress" role="status">
        {(activeId || preparing) && <progress aria-label="MP4書き出しの進捗" max={100} value={preparing ? undefined : job?.percent ?? undefined} />}
        <span>{retrying ? '進捗を再取得しています。' : preparing ? '書き出しを準備中' :
          [job?.stage, job?.percent != null ? job.percent + '%' : ''].filter(Boolean).join(' · ')}</span>
      </div>}
      {(notice || (!preparing && job?.error)) && <p className="editor-inline-error" role="alert">{notice || job?.error}</p>}
      {ready && <div className="video-download">
        <label>ファイル名<input value={filename} maxLength={150} onChange={event => setFilename(event.currentTarget.value)} /></label>
        <a className="download-button" href={'/api/video/jobs/' + ready.id + '/file'} download={outputName}>
          MP4を保存（{ready.ratio}）
        </a>
      </div>}
    </div>
  </section>;
}
