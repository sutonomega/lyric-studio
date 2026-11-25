import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { SyncPanel } from './SyncPanel';
import { Projects, type ProjectDocument, type AssetReference } from './Projects';
import { SubtitleEditor } from './SubtitleEditor';
import { defaultStyle, SubtitleOutput, SubtitleOverlay } from './SubtitleOutput';
import { commit, editCues, emptyHistory, isTimed, redo, undo, type Cue, type Edit } from './subtitles';
import { Timeline, type WaveformData } from './Timeline';
import { VideoOutput, type VideoRatio } from './VideoOutput';
import './studio.css';

type Notice = { text: string; error?: boolean };
type Material = { file: File; url: string; video: boolean; waveform: WaveformData | null };
type Artwork = { file: File; url: string };
type CapturableVideo = HTMLVideoElement & { captureStream?: () => MediaStream };
type Lyrics = { text: string; lines: [number, string][] };

async function post<T>(route: string, body: BodyInit, signal: AbortSignal): Promise<T> {
  const response = await fetch('/api/preview/' + route, { method: 'POST', body, signal });
  if (!response.ok) {
    const failure = await response.json().catch(() => null);
    throw new Error(typeof failure?.detail === 'string' ? failure.detail : '読み込みに失敗しました。');
  }
  return response.json();
}

function extension(file: File) {
  return /\.([^.]+)$/.exec(file.name)?.[1].toLowerCase();
}

function checkPlayback(url: string, video: boolean, signal: AbortSignal) {
  const player = document.createElement(video ? 'video' : 'audio');
  const limit = AbortSignal.any([signal, AbortSignal.timeout(15000)]);
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      player.removeAttribute('src');
      player.load();
      limit.removeEventListener('abort', cancel);
    };
    const cancel = () => {
      cleanup();
      reject(new Error('ブラウザで素材を読み込めませんでした。'));
    };
    limit.throwIfAborted();
    limit.addEventListener('abort', cancel, { once: true });
    player.preload = 'auto';
    const rejectPlayback = () => {
      cleanup();
      reject(new Error('このブラウザで再生できないコーデックか、破損した素材です。'));
    };
    player.addEventListener('loadeddata', () => {
      if (player instanceof HTMLVideoElement) {
        if (player.videoWidth === 0) {
          rejectPlayback();
          return;
        }
        const stream = (player as CapturableVideo).captureStream?.();
        if (stream) {
          const hasAudio = stream.getAudioTracks().length > 0;
          for (const track of stream.getTracks()) track.stop();
          if (!hasAudio) {
            cleanup();
            reject(new Error('このブラウザで再生できない音声コーデックです。'));
            return;
          }
        }
      }
      cleanup();
      resolve();
    }, { once: true });
    player.addEventListener('error', rejectPlayback, { once: true });
    player.src = url;
  });
}

async function checkImage(file: File, url: string) {
  const signature = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  const png = [137, 80, 78, 71, 13, 10, 26, 10].every((byte, i) => signature[i] === byte);
  const jpeg = signature[0] === 255 && signature[1] === 216 && signature[2] === 255;
  const kind = extension(file);
  if (!(kind === 'png' && png) && !((kind === 'jpg' || kind === 'jpeg') && jpeg)) {
    throw new Error('PNG または JPEG の画像を選んでください。');
  }
  const image = new Image();
  image.src = url;
  await image.decode().catch(() => { throw new Error('画像が破損しているため表示できません。'); });
}

function failure(error: unknown): Notice {
  return { text: error instanceof Error ? error.message : '読み込みに失敗しました。', error: true };
}

function FileChoice({ id, accept, label, select, disabled = false }: {
  id: string; accept: string; label: string; select: (file: File) => void; disabled?: boolean;
}) {
  return <label className={disabled ? 'file-control disabled' : 'file-control'}>
    {label}
    <input id={id} type="file" accept={accept} disabled={disabled} onChange={(event) => {
      const file = event.currentTarget.files?.[0];
      event.currentTarget.value = '';
      if (file) select(file);
    }} />
  </label>;
}

function Status({ notice }: { notice?: Notice }) {
  return notice && <p className={notice.error ? 'notice error' : 'notice'} role={notice.error ? 'alert' : 'status'}>
    {notice.text}
  </p>;
}

function timestamp(value: number) {
  const seconds = Math.floor(value);
  return [Math.floor(seconds / 60), seconds % 60].map((part) => String(part).padStart(2, '0')).join(':');
}

function App() {
  const [title, setTitle] = useState('無題のプロジェクト');
  const [mediaRef, setMediaRef] = useState<AssetReference | null>(null);
  const [imageRef, setImageRef] = useState<AssetReference | null>(null);
  const [material, setMaterial] = useState<Material>();
  const [artwork, setArtwork] = useState<Artwork>();
  const [mediaNotice, setMediaNotice] = useState<Notice>();
  const [imageNotice, setImageNotice] = useState<Notice>();
  const [lyricNotice, setLyricNotice] = useState<Notice>();
  const [text, setText] = useState('');
  const [lines, setLines] = useState<Lyrics['lines']>([]);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [revision, setRevision] = useState(0);
  const [syncControls, setSyncControls] = useState<HTMLDivElement | null>(null);
  const [history, setHistory] = useState(emptyHistory);
  const [style, setStyle] = useState(defaultStyle);
  const [videoRatio, setVideoRatio] = useState<VideoRatio>('16:9');
  const [fonts, setFonts] = useState<string[]>();
  const [showSample, setShowSample] = useState(false);
  const [selection, setSelection] = useState<string>();
  const [editError, setEditError] = useState<{ operation: Edit['type']; message: string }>();
  const track = history.current;
  const selected = track.cues.find(cue => cue.id === selection)?.id ?? track.cues[0]?.id;
  const previewSample = showSample ? track.cues.find(cue => cue.id === selected)?.text || '字幕プレビュー' : '';
  function markSourceChanged() {
    setRevision(previous => previous + 1);
  }
  const player = useRef<HTMLMediaElement | null>(null);
  const mediaRequest = useRef<AbortController | null>(null);
  const imageRequest = useRef<AbortController | null>(null);
  const lyricRequest = useRef<AbortController | null>(null);
  const typingTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    const request = new AbortController();
    void fetch('/api/subtitles/fonts', { signal: request.signal })
      .then(response => {
        if (!response.ok) throw Error('font list unavailable');
        return response.json() as Promise<{ families: string[] }>;
      })
      .then(({ families }) => {
        if (request.signal.aborted) return;
        setFonts(families);
        const preferred = ['Meiryo UI', 'Yu Gothic UI', 'Noto Sans JP'].find(font => families.includes(font));
        setStyle(previous => previous.font ? previous : { ...previous, font: preferred ?? families[0] ?? '' });
      })
      .catch(() => { if (!request.signal.aborted) setFonts([]); });
    return () => request.abort();
  }, []);
  useEffect(() => () => { if (material) URL.revokeObjectURL(material.url); }, [material]);
  useEffect(() => () => { if (artwork) URL.revokeObjectURL(artwork.url); }, [artwork]);
  useEffect(() => () => {
    mediaRequest.current?.abort();
    imageRequest.current?.abort();
    lyricRequest.current?.abort();
    window.clearTimeout(typingTimer.current);
  }, []);

  async function chooseMedia(file: File, replace = false) {
    mediaRequest.current?.abort();
    const kind = extension(file);
    if (kind !== 'mp4' && kind !== 'mp3' && kind !== 'wav') {
      setMediaNotice({ text: 'MP4・MP3・WAV を選んでください。', error: true });
      return;
    }
    const request = new AbortController();
    mediaRequest.current = request;
    const video = kind === 'mp4';
    const url = URL.createObjectURL(file);
    let accepted = false;
    setMediaNotice({ text: '素材を確認中…' });
    try {
      const result = await post<{ waveform: WaveformData | null; sha256: string }>('media/' + kind, file, request.signal);
      const same = !!mediaRef && result.sha256 === mediaRef.sha256;
      if (mediaRef && !material && !same && !replace) throw Error('保存時と同じ素材を選んでください。移動や改名したファイルも選べます。');
      await checkPlayback(url, video, request.signal);
      request.signal.throwIfAborted();
      player.current?.pause();
      setPlaying(false);
      if (!same && track.cues.length && !window.confirm('素材を差し替えると現在の字幕がリセットされます。続けますか？')) {
        setMediaNotice(undefined);
        return;
      }
      setPosition(0);
      if (!same) {
        setDuration(0);
        setHistory(emptyHistory());
        setSelection(undefined);
        setEditError(undefined);
      }
      setMediaRef({ name: file.name, sha256: result.sha256 });
      setMaterial({ file, video, url, waveform: result.waveform });
      markSourceChanged();
      setMediaNotice(undefined);
      accepted = true;
    } catch (error) {
      if (!request.signal.aborted) setMediaNotice(failure(error));
    } finally {
      if (!accepted) URL.revokeObjectURL(url);
    }
  }

  async function chooseImage(file: File, replace = false) {
    imageRequest.current?.abort();
    const request = new AbortController();
    imageRequest.current = request;
    const url = URL.createObjectURL(file);
    setImageNotice({ text: '画像を確認中…' });
    try {
      await checkImage(file, url);
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()));
      const sha256 = Array.from(digest, value => value.toString(16).padStart(2, '0')).join('');
      request.signal.throwIfAborted();
      if (imageRef && !artwork && !replace && imageRef.sha256 !== sha256) throw Error('保存時と同じ背景画像を選んでください。');
      setImageRef({ name: file.name, sha256 });
      setArtwork({ file, url });
      setImageNotice(undefined);
    } catch (error) {
      URL.revokeObjectURL(url);
      if (!request.signal.aborted) setImageNotice(failure(error));
    }
  }

  async function loadLyrics(source: 'text' | 'txt', value: string | File, request: AbortController) {
    try {
      const result = await post<Lyrics>('lyrics/' + source, value, request.signal);
      request.signal.throwIfAborted();
      if (source === 'txt') {
        setText(result.text);
        markSourceChanged();
      }
      setLines(result.lines);
      setLyricNotice(result.lines.length ? undefined : { text: '字幕にする歌詞がありません。' });
    } catch (error) {
      if (!request.signal.aborted) setLyricNotice(failure(error));
    }
  }

  function startLyricsRequest() {
    window.clearTimeout(typingTimer.current);
    lyricRequest.current?.abort();
    lyricRequest.current = new AbortController();
    return lyricRequest.current;
  }

  function enterLyrics(value: string) {
    const request = startLyricsRequest();
    setText(value);
    markSourceChanged();
    setLines([]);
    setLyricNotice(undefined);
    if (value.trim()) typingTimer.current = window.setTimeout(() => void loadLyrics('text', value, request), 180);
  }

  function importLyrics(file: File) {
    if (extension(file) !== 'txt') {
      setLyricNotice({ text: 'TXT ファイルを選んでください。', error: true });
      return;
    }
    const request = startLyricsRequest();
    setLyricNotice({ text: 'TXT を読み込み中…' });
    void loadLyrics('txt', file, request);
  }

  function restoreProject(project?: ProjectDocument) {
    mediaRequest.current?.abort();
    imageRequest.current?.abort();
    player.current?.pause();
    setPlaying(false);
    setPosition(0);
    setDuration((project?.duration_ms ?? 0) / 1000);
    setMaterial(undefined);
    setArtwork(undefined);
    setMediaRef(project?.media ?? null);
    setImageRef(project?.image ?? null);
    setMediaNotice(undefined);
    setImageNotice(undefined);
    setSelection(undefined);
    setEditError(undefined);
    setShowSample(false);
    setTitle(project?.title ?? '無題のプロジェクト');
    setHistory({ ...emptyHistory(), current: { cues: project?.cues ?? [] } });
    setStyle(project?.style ?? { ...defaultStyle, font: fonts?.includes('Meiryo UI') ? 'Meiryo UI' : fonts?.[0] ?? '' });
    setVideoRatio(project?.video_ratio ?? '16:9');
    enterLyrics(project?.lyrics ?? '');
  }

  function togglePlayback() {
    const media = player.current;
    if (!media) return;
    if (media.paused) void media.play().catch(() => setMediaNotice({ text: '素材を再生できませんでした。', error: true }));
    else media.pause();
  }

  function seek(value: number) {
    if (player.current) player.current.currentTime = value;
    setPosition(value);
  }

  function selectCue(cue: Cue) {
    setSelection(cue.id);
    setEditError(undefined);
    if (isTimed(cue)) seek(cue.start_ms / 1000);
  }

  function editSubtitle(edit: Edit) {
    setEditError(undefined);
    try {
      const result = editCues(track.cues, edit, Math.round(duration * 1000));
      if (result.cues === track.cues) return;
      setHistory(commit(history, { ...track, cues: result.cues }));
      if (result.selection !== undefined) setSelection(result.selection);
    } catch (error) {
      setEditError({ operation: edit.type, message: error instanceof Error ? error.message : '字幕を編集できませんでした。' });
    }
  }

  function navigateHistory(forward: boolean) {
    setEditError(undefined);
    setHistory(forward ? redo : undo);
  }

  const mediaProps = {
    ref: (node: HTMLMediaElement | null) => { player.current = node; },
    src: material?.url,
    preload: 'metadata',
    onLoadedMetadata: (event: React.SyntheticEvent<HTMLMediaElement>) => setDuration(event.currentTarget.duration),
    onTimeUpdate: (event: React.SyntheticEvent<HTMLMediaElement>) => setPosition(event.currentTarget.currentTime),
    onPlay: () => setPlaying(true),
    onPause: () => setPlaying(false),
    onError: () => setMediaNotice({ text: '素材を再生できませんでした。', error: true }),
  };
  const seekable = !!material && Number.isFinite(duration) && duration > 0;

  return <>
    <header className="topbar">
      <div className="brand"><span className="brand-icon" aria-hidden="true"><i /><i /><i /></span>Lyric Studio</div>
      <div className="topbar-sync" ref={setSyncControls} />
    </header>
    <main onKeyDown={event => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || (event.target as HTMLElement).closest('input, textarea, [contenteditable]')) return;
      const key = event.key.toLowerCase();
      if (key !== 'z' && key !== 'y') return;
      event.preventDefault();
      navigateHistory(key === 'y' || event.shiftKey);
    }}>
      <Projects document={{title, lyrics:text, cues:track.cues, style, video_ratio:videoRatio,
        duration_ms:Math.round(duration * 1000) || 0, media:mediaRef, image:imageRef}}
        onTitle={setTitle} onLoad={restoreProject} />
      <div className="preview-column">
        <section className="workspace" aria-labelledby="preview-title">
          <div className="section-head">
            <div>
              <h1 id="preview-title">プレビュー</h1>
              <p className="filename">{material?.file.name ?? (mediaRef ? '再リンク待ち · ' + mediaRef.name : '素材を選択してください')}</p>
            </div>
            <div className="edit-actions">
              <FileChoice id="media-file" label={!material && mediaRef ? '元の素材を再リンク' : '動画・音声を選ぶ'}
                accept=".mp4,.mp3,.wav" select={chooseMedia} />
              {!material && mediaRef && <FileChoice id="replace-media" label="別の素材を選ぶ" accept=".mp4,.mp3,.wav"
                select={file => void chooseMedia(file, true)} />}
            </div>
          </div>
          <div className={videoRatio === '9:16' ? 'stage portrait' : 'stage'}>
            {material?.video ? <video key={material.url} {...mediaProps} playsInline /> : <>
              {material && <audio key={material.url} {...mediaProps} />}
              {artwork ? <img src={artwork.url} alt={artwork.file.name} /> :
                <div className="stage-empty">
                  <svg viewBox="0 0 44 38" fill="none" aria-hidden="true">
                    <rect x="2" y="2" width="40" height="34" rx="5" />
                    <path d="m5 30 11-12 8 9 6-7 9 10M31 12h.01" />
                  </svg>
                  <p>{material ? '背景画像を選択してください' : 'MP4 または MP3・WAV'}</p>
                </div>}
            </>}
            <SubtitleOverlay cues={track.cues} position={position} playing={playing} player={player}
              style={style} sample={previewSample} />
          </div>
          <div className="transport">
            <button className="primary" type="button" onClick={togglePlayback} disabled={!seekable}>{playing ? '一時停止' : '再生'}</button>
            <span className="clock">{timestamp(position)}</span>
            <input aria-label="再生位置" type="range" min="0" max={seekable ? duration : 1} step="0.001"
              value={position} disabled={!seekable} onChange={(event) => seek(event.currentTarget.valueAsNumber)} />
            <span className="clock">{timestamp(seekable ? duration : 0)}</span>
            {seekable && <a className="timeline-link" href="#timeline-title">タイムライン</a>}
            <a className="timeline-link" href="#style-title">字幕スタイル</a>
            <a className="timeline-link" href="#video-output-title">MP4出力</a>
          </div>
          <Status notice={mediaNotice} />
          <div className="artwork">
            <div><h2>背景画像</h2><p className="filename">{material?.video ? 'MP4の映像を使用しています' : artwork?.file.name ?? (imageRef ? '再リンク待ち · ' + imageRef.name : 'PNG・JPEG')}</p></div>
            <div className="edit-actions">
              <FileChoice id="image-file" label={!artwork && imageRef ? '背景画像を再リンク' : '画像を選ぶ'} accept=".png,.jpg,.jpeg"
                select={chooseImage} disabled={material?.video} />
              {!artwork && imageRef && !material?.video && <FileChoice id="replace-image" label="別の画像を選ぶ"
                accept=".png,.jpg,.jpeg" select={file => void chooseImage(file, true)} />}
            </div>
          </div>
          {!material?.video && <Status notice={imageNotice} />}
        </section>
      </div>
      <section className="lyric-panel" aria-labelledby="lyrics-title">
        <div className="section-head">
          <h2 id="lyrics-title">同期用の歌詞</h2>
          <FileChoice id="lyrics-file" label="TXTを読み込む" accept=".txt" select={importLyrics} />
        </div>
        <label className="editor-label" htmlFor="lyrics">歌詞テキスト</label>
        <textarea id="lyrics" value={text} onChange={(event) => enterLyrics(event.currentTarget.value)}
          placeholder={'[Verse]\n歌詞を貼り付けるか、TXTを読み込んでください。'} spellCheck={false} />
        <Status notice={lyricNotice} />
        <div className="list-head"><h3>字幕対象の行</h3><span className="count" role="status">{lines.length} 行</span></div>
        {lines.length ? <ol className="lines" aria-label="字幕対象の行">
          {lines.map(([number, lyric]) => <li key={number}><p>{lyric}</p></li>)}
        </ol> : <p className="list-empty">歌詞を入力すると、字幕に使う行が表示されます。</p>}
        <SyncPanel controls={syncControls} media={material?.file} lyrics={text} revision={revision} hasLines={lines.length > 0}
          appliedJob={track.sourceJob} hasEditedCues={track.cues.length > 0} onApply={(id, cues) => {
            setHistory(commit(history, { sourceJob: id, cues }));
            const first = cues.find(isTimed) ?? cues[0];
            if (first) selectCue(first);
          }} onSeek={seek} />
        <SubtitleEditor cues={track.cues} selected={selected} duration={duration} position={position} error={editError}
          canUndo={history.past.length > 0} canRedo={history.future.length > 0}
          onSelect={selectCue} onEdit={editSubtitle} onUndo={() => navigateHistory(false)} onRedo={() => navigateHistory(true)} />
        <SubtitleOutput cues={track.cues} duration={duration} filename={material?.file.name ?? 'subtitles'}
          fonts={fonts} style={style} sample={previewSample} onStyle={setStyle}
          onToggleSample={() => setShowSample(value => !value)} />
        <VideoOutput media={material?.file} image={artwork?.file} duration={duration} cues={track.cues}
          style={style} ratio={videoRatio} onRatio={setVideoRatio} />
      </section>
      <Timeline key={material?.url} duration={duration} position={position} waveform={material?.waveform}
        cues={track.cues} selected={selected} onSelect={selectCue} onSeek={seek}
        onEdit={(id, edge, time) => editSubtitle({ type: 'edge', id, edge, time })} />
    </main>
  </>;
}

createRoot(document.getElementById('root')!).render(<App />);
