# Lyric Studio

Windows 11で、日本語の入力歌詞を楽曲に同期し、編集して字幕付きMVを出力するローカルアプリの開発計画です。現在は Phase 2 の素材・歌詞プレビューと自動同期を検証しています。

## 素材・歌詞プレビュー

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

### 波形と字幕タイムライン

プレビューの「タイムライン」から、音声波形、再生位置、字幕を同じ目盛りで確認できます。波形をクリックするとシークします。同期候補をプレビューに適用して字幕を選択し、両端のハンドルをドラッグすると開始・終了を調整できます。ハンドルにフォーカスして左右の矢印を押すと50msずつ、Shiftを押しながら操作すると500msずつ移動します。隣の字幕とは重ならない範囲で調整されます。

### 字幕を編集する

「字幕編集」で再生位置への追加、文字と時刻の変更、削除、分割・結合ができます。未配置の行は開始・終了を入力するか、再生位置から配置します。「一括移動（秒）」は配置済みの全字幕に適用され、素材の範囲を超える場合は時刻の変更を保留します。

分割位置は再生位置で指定し、字幕の文字は時間の比率に応じて振り分けます。結合した字幕の元の行間は改行で残ります。タイムラインのドラッグと同期候補の適用も、1操作として元に戻す・やり直すことができます。

### 字幕スタイルと字幕ファイル

「字幕スタイル」でフォント、サイズ、文字色、不透明度、縁取り、影、文字間隔、行間隔、表示位置、フェードを調整できます。「字幕見本」で停止中も確認できます。

「字幕ファイル」で編集済みの字幕を保存します。SRTはUTF-8で時刻を1ms単位で保持します。ASSは1920×1080を基準とし、スタイルを含めて時刻を10ms単位に丸めます。丸めると表示区間がなくなる行は、時刻を調整してから保存します。未配置・本文が空の行がある場合は、対象から除くことを確認してから保存します。

ASSのフォントは再生するPCに同じ書体をインストールしてください。ブラウザと字幕プレイヤーの描画方式により、字形や太さに差が出る場合があります。SRTの空行は字幕の区切りに使われるため省きます。記号列を装飾として解釈するSRTプレイヤーもあります。そのような記号を文字として表示したい場合は、ASSをlibass対応の環境で使えます。

## 設計資料

- [要求仕様書](docs/requirements.md)
- [基本設計書](docs/basic-design.md)
- [技術選定案](docs/technology-selection.md)
- [歌詞同期の技術検証計画](docs/sync-validation.md)

## 開発の進め方

1. [設計レビュー #1](https://github.com/sutonomega/lyric-studio/issues/1)で初期版範囲、検証構成、評価条件を確認する。
2. [Phase 1](https://github.com/sutonomega/lyric-studio/milestone/2)で実曲を使って、日本語の行同期の精度と手直し時間を比較する。
3. [移行判定 #7](https://github.com/sutonomega/lyric-studio/issues/7)で結果を確認してから、基本UIに進む。

Phase 1の再現手順は [同期CLI](backend/README.md) を参照する。

作業を [Issue](https://github.com/sutonomega/lyric-studio/issues) 単位で進める。ブランチとPRは関連Issueを参照し、完了条件に対応するテスト結果とWindowsでの確認結果を残す。重要な仕様・技術の採否は実装前に該当Issueで確認する。実曲、歌詞、認識全文、正解時刻と生成物はローカルに置く。[マイルストーン](https://github.com/sutonomega/lyric-studio/milestones)
