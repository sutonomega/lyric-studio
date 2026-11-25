# 依存ライセンスと配布方式

## Windowsで確認した方式

Gitで管理するソースから環境を作り、Pythonとnpmの依存をインストールしてブラウザ向けファイルをビルドします。利用者は FFmpeg・モデル・GPUランタイムを所定の配布元から取得します。動作確認にはソースのZIPを使いました。

## 依存ライセンスの参照先

| 対象 | 確認内容 |
| --- | --- |
| React / React DOM、Vite、TypeScript、WaveSurfer.js | React・ViteはMIT、TypeScriptはApache-2.0、WaveSurfer.jsはBSD-3-Clause。ブラウザ配布物に含める依存の告知を確認する。 [React](https://github.com/facebook/react/blob/main/LICENSE) [Vite](https://github.com/vitejs/vite/blob/main/LICENSE.md) [TypeScript](https://github.com/microsoft/TypeScript/blob/main/LICENSE.txt) [WaveSurfer.js](https://github.com/katspaugh/wavesurfer.js/blob/main/LICENSE) |
| FastAPI、Uvicorn、同期用ライブラリ | FastAPIはMIT、UvicornはBSD-3-Clause。同期・分離の依存とモデルの参照先は技術資料に記載。 [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE) [Uvicorn](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) [技術選定](technology-selection.md) |
| FFmpeg / libass / libx264 | FFmpegのライセンスはビルド構成によって変わる。使用したGyan EssentialsはGPL構成。実行ファイルは配布元から取得する。 [FFmpeg公式案内](https://ffmpeg.org/legal.html) [Gyan配布ページ](https://www.gyan.dev/ffmpeg/builds/) |
| Whisperおよび分離のモデル重み | 固定リビジョンは[同期CLIの手順](../backend/README.md)に記載。重みの同梱には、それぞれの配布条件を確認する。 [Whisper small](https://huggingface.co/Systran/faster-whisper-small) [Turbo](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo) [HTDemucs](https://huggingface.co/adefossez/HTDemucs) |
| CUDA DLL | NVIDIAの配布条件を確認する。検証のDLLはユーザー側の環境にインストールして使用する。 [CUDA配布条件](https://docs.nvidia.com/cuda/eula/index.html) [cuDNN配布条件](https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/license.html) |

## v1.0.0の公開範囲

Lyric Studio本体のソースをGitHubで公開します。本体には [MIT License](../LICENSE) を適用します。利用者は依存ライブラリ、FFmpeg、モデル、GPUランタイムをそれぞれの配布元から取得し、ブラウザ向けのファイルをローカルでビルドします。個人素材、歌詞、プロジェクト、結果キャッシュ、開発者のモデルキャッシュは配布物から除外します。
