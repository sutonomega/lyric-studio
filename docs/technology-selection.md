# Lyric Studio 技術選定案

2026-11-24 / [承認・変更管理 #1](https://github.com/sutonomega/lyric-studio/issues/1)

## Phase 1 で比較する構成

Windows 11 x64、Python 3.12を基準に、FFmpegで入力をそろえ、faster-whisperで日本語の時刻付き認識結果を作る。日本語の表記揺れはSudachiPyと文字列正規化で吸収し、単調な動的計画法で入力歌詞に照合する。分離なしと htdemucs を同じ曲で比較し、必要に応じてAudio Separatorや日本語CTC強制アライメントを追加する。

| 用途 | 候補と判断 | 互換性・ライセンス・保守状況 |
| --- | --- | --- |
| 音声認識 | faster-whisper 1.2.1 / CTranslate2 4.9.1 を初期検証候補。多言語 `small` で開始し、精度・時間を見てモデルサイズを比較する。 | いずれもMIT。Python 3.12 Windows x64向けCTranslate2 wheelあり。CPU int8を基準にする。CUDA 12系のcuDNN要件について双方の文書に差があるため、採用版のDLLと実際の推論で確認する。リポジトリは確認時点で非アーカイブ。 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) [CTranslate2](https://opennmt.net/CTranslate2/installation.html) [配布ファイル](https://pypi.org/project/ctranslate2/#files) [モデルカード](https://huggingface.co/Systran/faster-whisper-small) |
| 音声認識の代替 | whisper.cpp。Python側の実行環境や対応GPUが制約になった場合にCLIアダプターを比較する。 | MIT。CPUやCUDAを含むバックエンドの選択肢あり。Windows配布物と日本語タイムスタンプを実機で確認する。 [公式リポジトリ](https://github.com/ggml-org/whisper.cpp) |
| ボーカル分離 | Demucs 4.1.0 / htdemucs を比較基準にする。ASRとは分離できる環境・プロセスで起動する。 | MIT。Meta版はアーカイブ。作者のフォークは重要な修正を中心とし、PyPIに2026年7月版がある。WindowsでFFmpeg・PyTorchに依存し、CPU/CUDA、モデルの入手元と利用・再配布条件は個別に確認する。 [作者フォーク](https://github.com/adefossez/demucs) [PyPI](https://pypi.org/project/demucs/) [PyTorch](https://pytorch.org/get-started/locally/) |
| 分離の代替 | Audio Separator 0.48.0。候補モデルを1つ固定して追加比較する。 | ラッパーはMIT、2026年10月にPyPIリリース。WindowsのCPU実行と使用する推論ランタイムを確認する。モデルごとの条件、取得元、クレジットを確認する。 [公式リポジトリ](https://github.com/nomadkaraoke/python-audio-separator) [PyPI](https://pypi.org/project/audio-separator/) |
| 強制アライメント | WhisperX 3.8.6相当の日本語CTCを候補にする。暫定位置で切った区間に歌詞を与え、境界を再推定する。 | WhisperXはBSD-2-Clause、日本語モデルカードはApache-2.0。Python 3.12は配布メタデータ上の対象。Torch系依存は別環境で試す。文字の語彙外率と日本語の長音・伸ばし歌唱を実曲で確認する。 [WhisperX](https://github.com/m-bain/whisperX) [日本語モデルの指定](https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py) [モデルカード](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-japanese) [PyPI](https://pypi.org/project/whisperx/) |
| 日本語正規化 | SudachiPy 0.7.0、SudachiDict-core 20261015。漢字の読みと、元表記側の比較キーを併用する。 | Apache-2.0。新世代のSudachiPyはsudachi.rsが配布。Python 3.12で利用可能なWindows x64 ABI3 wheelあり。辞書の同梱条件も確認する。 [Sudachi](https://github.com/WorksApplications/sudachi.rs) [辞書](https://github.com/WorksApplications/SudachiDict) [配布ファイル](https://pypi.org/project/SudachiPy/#files) |
| メディア | FFmpeg・ffprobeの外部実行ファイル。入力調査、音声変換、波形、プロキシと動画出力に共用する。 | Windows向け配布物で `subtitles`/`ass` フィルター、libass、H.264、AACを照合する。FFmpegはビルド構成により LGPL/GPL 等の条件が変わり、libx264を使うビルドはGPLの扱いを確認する。libassはISC。初期案は利用者によるFFmpeg導入とし、同梱は配布形態のレビュー後に判断する。 [FFmpeg ダウンロード](https://ffmpeg.org/download.html) [ライセンス](https://ffmpeg.org/legal.html) [libass](https://github.com/libass/libass) |
| UI・API | React、TypeScript、Vite、FastAPI/Uvicorn。波形にはWaveSurfer.jsとサーバー生成ピークを使う。 | React/Vite/FastAPIはMIT、TypeScriptはApache-2.0、Uvicorn/WaveSurfer.jsはBSD-3-Clause。非アーカイブを確認。WindowsでのNodeビルド、ブラウザのメディアシークと長尺素材は Phase 2〜3で確認する。重い処理は別プロセスに渡す。 [React](https://github.com/react/react) [TypeScript](https://github.com/microsoft/TypeScript) [Vite](https://vite.dev/guide/) [FastAPI](https://fastapi.tiangolo.com/tutorial/background-tasks/) [Uvicorn](https://github.com/Kludex/uvicorn) [WaveSurfer.js](https://wavesurfer.xyz/docs/) |

## Windows の準備状況

このPCで Windows 11 x64、Python 3.12 x64、Node.js v24.15.0、uv 0.11.17、RTX 4070 SUPER（12282 MiB、ドライバー 617.42）を確認した。FFmpegは現在のPATHから起動できない。GPUの検出と、モデルのGPU推論は別々に判定する。

作業フォルダの分離環境で Python 3.12.10 に faster-whisper、CTranslate2、SudachiPy と辞書を導入した。faster-whisperをインポートし、CPU int8対応、CTranslate2のCUDAデバイス1台、Sudachiの読み「カシ／ドウキ」を確認した。モデルのロード・推論、ボーカル分離、実曲精度は Phase 1 の確認項目とする。

FFmpeg 9.1 Essentialsの7zをSHA-256照合後に作業フォルダへ展開した。ASS・subtitlesフィルターの存在、合成素材への日本語SRTの焼き込み、游ゴシックの選択、字幕区間の画素変化を確認した。ffprobeでH.264/AACのMP4出力を検査した。 [Gyan Windowsビルド 9.1](https://github.com/GyanD/codexffmpeg/releases/tag/9.1)

クリーン環境では、CPUでの起動と短い推論、GPUでの同じ入力の処理、FFmpegによる日本語字幕付きファイルの生成を順に確認する。ASRの高速化はNVIDIAを初期対象とし、その他のGPUは候補ランタイムの実測後に対応範囲を決める。

採用レビューでは、解決した依存バージョン、対応するWindowsビルドやDLL、モデル・辞書の取得元と版、必要なライセンス表記を記録する。Phase 1で使用するライブラリとモデルを合意し、製品への採用は精度・修正時間・負荷の結果で判断する。
