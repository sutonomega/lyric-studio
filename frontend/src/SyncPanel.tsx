import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cueStatus, cueReasons, timestamp, type Cue } from './subtitles';

type Job = {
  id: string;
  state: 'queued' | 'uploading' | 'running' | 'cancelling' | 'cancelled' | 'succeeded' | 'failed';
  stage: string;
  error: string | null;
  cues: Cue[] | null;
};

type Config = { available: boolean; profile?: 'baseline' | 'refined'; device?: 'cpu' | 'cuda' };

function busy(job?: Job) {
  return job && ['queued', 'uploading', 'running', 'cancelling'].includes(job.state);
}

class SyncError extends Error {
  constructor(message: string, readonly code: number) { super(message); }
}

async function request<T>(route: string, options?: RequestInit): Promise<T> {
  const reply = await fetch('/api/sync/' + route, options);
  if (!reply.ok) {
    const failure = await reply.json().catch(() => null);
    throw new SyncError(typeof failure?.detail === 'string' ? failure.detail : '同期処理に接続できません。', reply.status);
  }
  return reply.json();
}

export function SyncPanel({ controls, media, lyrics, revision, hasLines, appliedJob, hasEditedCues, onApply, onSeek }: {
  controls: HTMLDivElement | null;
  media?: File;
  lyrics: string;
  revision: number;
  hasLines: boolean;
  appliedJob?: string;
  hasEditedCues: boolean;
  onApply: (id: string, cues: Cue[]) => void;
  onSeek: (seconds: number) => void;
}) {
  const [config, setConfig] = useState<Config>();
  const [job, setJob] = useState<Job>();
  const [startedRevision, setStartedRevision] = useState<number>();
  const [notice, setNotice] = useState('');
  const [pollError, setPollError] = useState(false);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const upload = useRef<AbortController | null>(null);
  const activeId = busy(job) ? job?.id : undefined;

  useEffect(() => {
    const controller = new AbortController();
    void request<Config>('config', { signal: controller.signal }).then(setConfig, () => {
      if (!controller.signal.aborted) setNotice('同期の設定を取得できませんでした。');
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!activeId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    async function check() {
      try {
        const current = await request<Job>('jobs/' + activeId, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setPollError(false);
        setJob(current);
        if (!busy(current)) return;
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof SyncError && error.code === 404) {
          setPollError(false);
          setJob(undefined);
          setNotice(error.message);
          return;
        }
        setPollError(true);
      }
      timer = window.setTimeout(check, 250);
    }
    void check();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [activeId]);

  async function start() {
    if (!media || !hasLines || starting || activeId) return;
    setNotice('');
    setStarting(true);
    const controller = new AbortController();
    upload.current = controller;
    let created: Job | undefined;
    try {
      created = await request<Job>('jobs', {
        method: 'POST',
        body: JSON.stringify({ kind: media.name.split('.').at(-1)?.toLowerCase(), lyrics }),
      });
      setJob(created);
      setStartedRevision(revision);
      await request<Job>('jobs/' + created.id + '/media', { method: 'PUT', body: media, signal: controller.signal });
    } catch (error) {
      if (!controller.signal.aborted) {
        setNotice(error instanceof Error ? error.message : '同期を開始できませんでした。');
        if (created) await request('jobs/' + created.id + '/cancel', { method: 'POST' }).catch(() => {});
      }
    } finally {
      setStarting(false);
    }
  }

  async function cancel() {
    if (!activeId) return;
    const controller = upload.current;
    setCancelling(true);
    setNotice('');
    try {
      await request('jobs/' + activeId + '/cancel', { method: 'POST' });
      controller?.abort();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'キャンセルできませんでした。');
    } finally {
      setCancelling(false);
    }
  }

  const stale = startedRevision !== revision;
  const preparing = starting && !activeId;
  const appliedHere = Boolean(appliedJob && appliedJob === job?.id);
  const cues = preparing ? undefined : job?.cues;
  const inputHint = config?.available
    ? (!media || !hasLines) && '素材と歌詞を読み込むと開始できます'
    : config && '同期を有効にするには、起動時にローカルモデルを指定してください。';
  const error = notice || (!preparing && job?.error);
  return <section className="sync" aria-labelledby="sync-title">
    {controls && createPortal(<>
      {(job || preparing || inputHint || error) && <div className="sync-header-status">
        {(job || preparing) && <div className="job-state" role="status">
          {(activeId || preparing) && <progress aria-label="同期の進捗" />}
          <span>{pollError ? '同期の状態を再取得しています。' : preparing ? '同期を準備中' : job?.stage}</span>
        </div>}
        {inputHint && !activeId && <p id="sync-input-hint">{inputHint}</p>}
        {error && <p className="sync-header-error" role="alert">{error}</p>}
      </div>}
      {activeId
        ? <button type="button" onClick={() => void cancel()} disabled={cancelling || job?.state === 'cancelling'}>キャンセル</button>
        : <button type="button" className="primary" disabled={!config?.available || !media || !hasLines || starting || cancelling}
            aria-describedby={inputHint ? 'sync-input-hint' : undefined}
            onClick={() => void start()}>{job ? '再実行' : '同期を開始'}</button>}
    </>, controls)}
    <div className="list-head sync-heading">
      <div><h3 id="sync-title">自動同期</h3>
        {config?.available && <p className="filename">{config.profile === 'refined' ? 'ボーカル分離と補正あり' : '原音を使う同期'} · {config.device?.toUpperCase()}</p>}
      </div>
    </div>
    {job && cues && <>
      <div className="candidate-summary">
        <h4>同期候補</h4>
        <p>{cues.filter(cue => cue.status === 'candidate').length} 候補 ／ {cues.filter(cue => cue.status === 'review').length} 要確認 ／ {cues.filter(cue => cue.status === 'unplaced').length} 未配置</p>
        {appliedHere ? <p>適用済み · <a href="#subtitle-editor-title">字幕編集を開く</a></p> : stale ? <p role="status">同期開始後に素材または歌詞が変わりました。現在の入力で再実行してください。</p> :
          <button type="button" disabled={!cues.length}
            onClick={() => onApply(job.id, cues)}>{hasEditedCues ? '同期候補で字幕を置き換える' : '候補をプレビューに適用'}</button>}
      </div>
      {!appliedHere && <ol className="lines candidates" aria-label="同期候補">
        {cues.map(cue => <li key={cue.id}><div>
          <p>{cue.text}</p>
          <div className="cue-info">
            <span className={'cue-status ' + cue.status}>{cueStatus[cue.status]}</span>
            {cue.start_ms !== null && cue.end_ms !== null && <button type="button" disabled={stale} onClick={() => onSeek(cue.start_ms! / 1000)}>
              {timestamp(cue.start_ms)} – {timestamp(cue.end_ms)}
            </button>}
          </div>
          {cue.reasons.length > 0 && <ul>{cue.reasons.map(reason => <li key={reason}>{cueReasons[reason] ?? reason}</li>)}</ul>}
        </div></li>)}
      </ol>}
    </>}
  </section>;
}
