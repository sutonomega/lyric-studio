import { useEffect, useRef, useState } from 'react';
import type { Cue } from './subtitles';
import type { Style } from './SubtitleOutput';
import type { VideoRatio } from './VideoOutput';

export type AssetReference = { name: string; sha256: string };
export type ProjectDocument = {
  title: string;
  lyrics: string;
  cues: Cue[];
  style: Style;
  video_ratio: VideoRatio;
  duration_ms: number;
  media: AssetReference | null;
  image: AssetReference | null;
};
type Record = { id: string; revision: number; saved_at: string; document: ProjectDocument };
type Summary = { id: string; title: string; saved_at: string };
const LAST_PROJECT = 'lyric-studio-last-project';

function signature(document: ProjectDocument) {
  return JSON.stringify(document, (_key, value) => {
    if (!value || Array.isArray(value) || typeof value !== 'object') return value;
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]]));
  });
}

class ProjectError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

async function request<T>(path = '', method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch('/api/projects' + path, {
    method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new ProjectError(typeof error?.detail === 'string' ? error.detail : 'プロジェクトに接続できません。', response.status);
  }
  return response.json();
}

function remember(id?: string) {
  try {
    if (id) localStorage.setItem(LAST_PROJECT, id);
    else localStorage.removeItem(LAST_PROJECT);
  } catch { /* Project files remain available from the list. */ }
}

export function Projects({ document, onLoad, onTitle }: {
  document: ProjectDocument;
  onLoad: (document?: ProjectDocument) => void;
  onTitle: (title: string) => void;
}) {
  const [records, setRecords] = useState<Summary[]>([]);
  const [typed, setTyped] = useState('');
  const [initialized, setInitialized] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [saved, setSaved] = useState('');
  const active = useRef<Record | undefined>(undefined);
  const saving = useRef<Promise<boolean> | null>(null);
  const latest = useRef(document);
  const load = useRef(onLoad);
  latest.current = document;
  load.current = onLoad;
  const key = signature(document);
  const meaningful = !!(document.lyrics || document.cues.length || document.media || document.image ||
    document.title !== '無題のプロジェクト');

  function accept(record: Record) {
    active.current = record;
    setSaved(signature(record.document));
    setTyped(record.id);
    remember(record.id);
    setRecords(current => [{id:record.id, title:record.document.title, saved_at:record.saved_at},
      ...current.filter(item => item.id !== record.id)]);
  }

  useEffect(() => {
    let disposed = false;
    async function initialize() {
      try {
        const items = await request<Summary[]>();
        if (disposed) return;
        setRecords(items);
        let last: string | null = null;
        try { last = localStorage.getItem(LAST_PROJECT); } catch { /* The list is still usable. */ }
        if (last && items.some(item => item.id === last)) {
          const record = await request<Record>('/' + last);
          if (disposed) return;
          const current = latest.current;
          if (current.lyrics || current.cues.length || current.media || current.image || current.title !== '無題のプロジェクト') return;
          load.current(record.document);
          accept(record);
          setStatus('自動復元しました');
        }
      } catch (issue) {
        if (!disposed) setError(issue instanceof Error ? issue.message : 'プロジェクトを読み込めませんでした。');
      } finally {
        if (!disposed) setInitialized(true);
      }
    }
    void initialize();
    return () => { disposed = true; };
  }, []);

  function save(copy = false): Promise<boolean> {
    if (saving.current) return saving.current;
    const current = copy ? undefined : active.current;
    const value = latest.current;
    const document = copy ? {...value,title:value.title.slice(0,110) + '（コピー）'} : value;
    setBusy(true);
    setError('');
    const operation = request<Record>(current ? '/' + current.id : '', current ? 'PUT' : 'POST',
      current ? { revision:current.revision, document } : document)
      .then(record => {
        if (copy) onTitle(document.title);
        accept(record);
        setConflict(false);
        setStatus('保存済み');
        return true;
      }, issue => {
        setConflict(issue instanceof ProjectError && issue.status === 409);
        setError(issue instanceof Error ? issue.message : 'プロジェクトを保存できませんでした。');
        return false;
      })
      .finally(() => { saving.current = null; setBusy(false); });
    saving.current = operation;
    return operation;
  }

  useEffect(() => {
    if (!initialized || busy || conflict || error || !meaningful || !latest.current.title.trim() || key === saved) return;
    const timer = window.setTimeout(() => void save(), 650);
    return () => window.clearTimeout(timer);
  }, [initialized, busy, conflict, error, meaningful, key, saved]);

  useEffect(() => {
    if (!initialized || !meaningful || key === saved) return;
    function warn(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = true;
    }
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [initialized, meaningful, key, saved]);

  async function switchProject(id?: string) {
    const before = signature(latest.current);
    if (conflict) {
      if (!window.confirm('この画面で未保存の変更を破棄して切り替えますか？')) return;
    } else if ((active.current || meaningful) && before !== saved) {
      if (!await save()) return;
    }
    setBusy(true);
    try {
      const record = id ? await request<Record>('/' + id) : undefined;
      if (signature(latest.current) !== before) {
        setError('切り替え中の編集を保持しました。保存してからもう一度開いてください。');
        return;
      }
      if (record) {
        load.current(record.document);
        accept(record);
        setStatus('読み込み済み');
      } else {
        load.current(undefined);
        active.current = undefined;
        setSaved('');
        setTyped('');
        remember();
        setStatus('');
      }
      setConflict(false);
      setError('');
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : 'プロジェクトを読み込めませんでした。');
    } finally {
      setBusy(false);
    }
  }

  return <section className="projects-panel" aria-labelledby="projects-title">
    <h2 id="projects-title">プロジェクト</h2>
    <div className="project-actions">
      <label>プロジェクト名<input value={document.title} maxLength={120}
        onChange={event => onTitle(event.currentTarget.value)}
        onBlur={() => { if (!latest.current.title.trim()) onTitle('無題のプロジェクト'); }} /></label>
      <button type="button" disabled={!initialized || busy || conflict || !document.title.trim()} onClick={() => void save()}>保存</button>
      <button type="button" disabled={!initialized || busy} onClick={() => void switchProject()}>新規</button>
      <label>保存済みプロジェクト<select value={typed} onChange={event => setTyped(event.currentTarget.value)}>
        <option value="">選択してください</option>
        {records.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select></label>
      <button type="button" disabled={!typed || busy} onClick={() => void switchProject(typed)}>開く</button>
      {conflict && <button type="button" disabled={busy} onClick={() => void save(true)}>別名で保存</button>}
    </div>
    {error ? <p role="alert" className="editor-inline-error">{error}</p> :
      <p role="status" className="filename">{busy ? '処理中…' : meaningful && key !== saved ? '未保存の変更' : status}</p>}
  </section>;
}
