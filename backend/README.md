# Phase 1 同期CLI

Windows 11 x64 と Python 3.12 で、MP3・WAV・音声付きMP4から UTF-8 の歌詞を行単位で同期する。出力先に `sync.json` と `subtitles.srt` を作る。評価用の音声・歌詞・正解時刻はローカルに置く。

## セットアップ

FFmpegとffprobeがあるWindows x64ビルドを用意する。検証済み版は Gyan 9.1 Essentials。字幕焼き込みを検証する場合は `ass` と `subtitles` フィルター、H.264とAACを確認する。 [FFmpegの配布案内](https://ffmpeg.org/download.html)・[使用したビルド](https://github.com/GyanD/codexffmpeg/releases/tag/9.1)

リポジトリのルートからPowerShellで実行する。

~~~powershell
py -3.12 -m venv .venv
& .venv/Scripts/python.exe -m pip install -e backend
$env:HF_HOME = Join-Path (Get-Location) 'local-data/models'
$small = & .venv/Scripts/python.exe -c 'from faster_whisper.utils import download_model; print(download_model("small", revision="536b0662742c02347bc0e980a01041f333bce120"))'
$turbo = & .venv/Scripts/python.exe -c 'from faster_whisper.utils import download_model; print(download_model("large-v3-turbo", revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"))'
~~~

2番目のモデルは補正試行に使う。実行時は取得済みのモデルを使う。 [small](https://huggingface.co/Systran/faster-whisper-small)・[Turbo](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo)

## 原音による基準結果

~~~powershell
& .venv/Scripts/python.exe -m lyric_studio.cli '録音.mp4' '歌詞.txt' --small-model $small --output-dir local-data/baseline
~~~

FFmpegを直接指定する場合は `--ffmpeg C:\path\to\ffmpeg.exe` を付ける。出力先には新しいディレクトリ名を指定する。JSONは入力の行ID、時刻、要確認理由、未配置を含む。SRTには配置された行を原文で書き出し、未配置数を表示する。

## 補正を含む比較

~~~powershell
& .venv/Scripts/python.exe -m lyric_studio.cli '録音.mp4' '歌詞.txt' --small-model $small --turbo-model $turbo --vocals 'ボーカル.wav' --output-dir local-data/refined
~~~

`--vocals` は、時刻が元音声と一致する分離ボーカルを指定する。代わりに `--demucs-python` で htdemucs を用意した別環境のPythonを指定できる。検証時は CPU版 PyTorch 2.15.0、Demucs 4.1.0、NumPy 2.5.5、huggingface-hub 0.36.2を使用した。分離環境にも同じ `HF_HOME` を設定し、オンラインのセットアップ時に `from demucs.pretrained import get_model; get_model("htdemucs")` を実行する。検証時の htdemucs モデルリビジョンは `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003`。 [Demucs](https://github.com/adefossez/demucs)・[HTDemucsモデル](https://huggingface.co/adefossez/HTDemucs)

原音と分離ボーカルは同じsmall設定で認識する。補正は、前後の時刻がある未配置行の局所照合とボーカル音量、別認識結果との境界比較を使う。追加・変更された行は要確認とする。CPU int8、8スレッド、VAD無効が検証条件。方式の評価は別曲の固定評価セットで行う。

同じ時刻付き認識結果と音量データからは同じSRTを再構成できる。音声認識からやり直した実曲の確認では一部の時刻に差が出たため、比較時は各試行のJSONとSRTを保存する。

~~~powershell
& .venv/Scripts/python.exe -m unittest discover -s backend/tests -v
~~~

依存ライブラリと配布時の条件は [技術選定案](../docs/technology-selection.md) を参照する。

## NVIDIA GPUで音声認識を試す

RTX 4070 SUPER / Windows 11 / CTranslate2 4.9.1 の検証では、CUDA 12 用 cuBLAS 12.9.1.4 を作業フォルダに置き、CTranslate2のWindows wheelに含まれる cuDNN 9 のDLLと組み合わせて推論できた。NVIDIAドライバーは事前に用意する。次の手順は現在のPowerShellプロセスのPATHにだけ追加する。

~~~powershell
$runtime = Join-Path (Get-Location) 'local-data/cuda-windows'
& .venv/Scripts/python.exe -m pip install --target $runtime 'nvidia-cublas-cu12==12.9.1.4'
$env:PATH = "$(Join-Path $runtime 'nvidia/cublas/bin');$env:PATH"
& .venv/Scripts/python.exe -m lyric_studio.cli '録音.mp4' '歌詞.txt' --small-model $small --device cuda --output-dir local-data/cuda-baseline
~~~

GPU指定時は認識だけが CUDA の `int8_float16` で動く。ボーカル分離はCPU。既定のCPU認識は `int8` で、CPUとGPUの字幕候補は評価結果を分けて扱う。 [CTranslate2のインストール要件](https://opennmt.net/CTranslate2/installation.html)・[faster-whisperのGPU要件](https://github.com/SYSTRAN/faster-whisper#gpu)・[使用したcuBLASの配布版](https://pypi.org/project/nvidia-cublas-cu12/12.9.1.4/)

### モデルとライブラリの入手条件

- faster-whisperとCTranslate2はMIT。smallとTurboの重みは、セットアップ欄で指定したモデルカードとリビジョンを確認する。SudachiPyとSudachiDict-coreはApache-2.0。 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)・[CTranslate2](https://github.com/OpenNMT/CTranslate2)・[SudachiDict](https://github.com/WorksApplications/SudachiDict)
- DemucsのコードはMIT。htdemucsの重みは作者の配布元から取得する。検証したモデルカードで重みの個別ライセンス表記は確認できなかったため、重みの再配布を検討するときは条件を別途確認する。 [Demucs](https://github.com/adefossez/demucs)・[HTDemucsモデル](https://huggingface.co/adefossez/HTDemucs)
- cuBLASやcuDNNを使う場合はNVIDIAの利用条件が適用される。FFmpegは取得したビルドのライセンスと構成を確認する。 [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html)・[cuDNN利用条件](https://docs.nvidia.com/cudnn/reference/eula/)・[FFmpegのライセンス](https://ffmpeg.org/legal.html)
