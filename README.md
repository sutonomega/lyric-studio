# Lyric Studio

Windows 11で、日本語の入力歌詞を楽曲に同期し、編集して字幕付きMVを出力するローカルアプリです。現在は初期正式版に向けたWindows検証を進めています。

## Windowsで起動する

Windows PowerShellで、リポジトリのルートから実行します。Python 3.12、Node.js 22.12以降、波形と同期には FFmpeg と ffprobe が必要です。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e './backend[preview]'
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python.exe -m lyric_studio.preview --dist frontend/dist
```

ffprobe が PATH にない場合は、起動コマンドに `--ffprobe 'C:/path/to/ffprobe.exe'` を追加します。
Edge または Chrome で http://127.0.0.1:8769/ を開きます。

- 音声付き MP4、または MP3・WAV と PNG・JPEG を読み込み、再生・一時停止・シークができます。
- テキストを貼り付けるか、UTF-8 または Shift_JIS（CP932）の TXT を読み込みます。字幕対象の行が表示されます。Suno のメタデータ付きテキストは `Lyrics:` より後を使います。
- 音声・動画は形式確認のためにPC内の一時ファイルにコピーされ、確認後に削除されます。再生はブラウザで行います。

### 画面から自動同期する

[同期CLIのセットアップ](backend/README.md#セットアップ)でローカルモデルを取得してから、起動時に small モデルのディレクトリを指定します。

```powershell
.\.venv\Scripts\python.exe -m lyric_studio.preview --dist frontend/dist --small-model $small
```

- small 単独では原音を解析します。ボーカル分離と補正を使う場合は、セットアップ済みの分離環境に加え `--turbo-model $turbo --demucs-python 'C:/path/to/demucs-python.exe'` を指定します。分離モデルを取得した先と同じ `HF_HOME` を使用してください。
- CPU が既定です。GPU 環境では [CUDA の準備](backend/README.md#nvidia-gpuで音声認識を試す)の後に `--device cuda` を指定できます。ffmpeg は ffprobe と同じフォルダから使い、別配置なら `--ffmpeg` で指定します。
- 「同期を開始」で解析工程が表示され、処理中はキャンセルできます。完了後に候補時刻と要確認理由を確認し、「候補をプレビューに適用」で再生画面に字幕を表示できます。同期開始後に素材や歌詞を変更した場合は、現在の入力で再実行します。
- 同期中の入力と中間ファイルはPC内の一時フォルダに置き、終了後に削除します。候補は開いている画面に保持されます。

編集・書き出し・再開の手順は[操作マニュアル](docs/user-guide.md)を参照してください。

## 設計資料

- [要求仕様書](docs/requirements.md)
- [基本設計書](docs/basic-design.md)
- [技術選定案](docs/technology-selection.md)
- [歌詞同期の技術検証計画](docs/sync-validation.md)

## リリース準備

- [Windows検証結果](docs/windows-validation.md)
- [依存ライセンスと配布方式](docs/distribution-notes.md)
- [初期版リリースノート案](docs/release-notes.md)

## 開発の進め方

1. [設計レビュー #1](https://github.com/sutonomega/lyric-studio/issues/1)で初期版範囲、検証構成、評価条件を確認する。
2. [Phase 1](https://github.com/sutonomega/lyric-studio/milestone/2)で実曲を使って、日本語の行同期の精度と手直し時間を比較する。
3. [移行判定 #7](https://github.com/sutonomega/lyric-studio/issues/7)で結果を確認してから、基本UIに進む。

Phase 1の再現手順は [同期CLI](backend/README.md) を参照する。

作業を [Issue](https://github.com/sutonomega/lyric-studio/issues) 単位で進める。ブランチとPRは関連Issueを参照し、完了条件に対応するテスト結果とWindowsでの確認結果を残す。重要な仕様・技術の採否は実装前に該当Issueで確認する。実曲、歌詞、認識全文、正解時刻と生成物はローカルに置く。[マイルストーン](https://github.com/sutonomega/lyric-studio/milestones)
