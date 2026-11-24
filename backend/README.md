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

未配置が1行のときは、原音small・分離small・原音Turboの語句が前後の字幕の間で同じ区間を示し、同程度に一致する離れた候補がなく、ボーカル音量も裏付ける場合に候補を追加する。要確認行の終了は、分離ボーカルが続いたあとに短い無音があり、2種類の音量しきい値で同じ終了付近が得られた場合に限って延長する。延長は900ms以内で、次の字幕には重ねない。

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

## 固定評価

試聴シートは元音声と歌詞から生成し、同期候補の時刻を表示しない。同じ曲の生成違いは、調整用と固定評価用にまたがらないようにする。

~~~powershell
& .venv/Scripts/python.exe -m lyric_studio.evaluation prepare '録音.mp3' '歌詞.txt' local-data/reference.html
& .venv/Scripts/python.exe -m lyric_studio.cli '録音.mp3' '歌詞.txt' --small-model $small --device cpu --seed 0 --output-dir local-data/baseline
~~~

ブラウザでシートと対象素材を開いて全行に時刻・歌唱なし・判定保留のいずれかを記録する。歌われない構成指示が行として残っている場合は「歌唱なし」とする。注釈JSONは途中でも書き出し・再読込できる。入力のSHA-256が一致した全行の注釈を採点する。

歌詞ファイルに単独行の `Lyrics:` がある場合は、その次の行から同期・注釈の対象にする。元のファイルの行IDを保持するため、同じ素材の旧シートから書き出した注釈を再読み込みすると、本文の記録を引き継ぎ、ヘッダーの記録を除外する。

~~~powershell
& .venv/Scripts/python.exe -m lyric_studio.evaluation score local-data/baseline/sync.json local-data/reference.json local-data/score.json
& .venv/Scripts/python.exe -m lyric_studio.evaluation summarize local-data/cohort.json local-data/track-a-score.json local-data/track-b-score.json
~~~

両端が500ms以内の割合、未配置、歌唱なしの誤配置、警告の捕捉率・適合率、開始・終了の誤差を集計する。判定保留は件数を報告して精度の分母から除く。集計時はプロファイル・認識設定が等しいスコアを指定する。手直し時間の比較方法と移行基準案は [検証計画](../docs/sync-validation.md) を参照する。

原音の基準結果と補正込み結果を同じ注釈で比較する。どちらも同一の音声・歌詞・smallモデル・認識設定を使う。基準結果に `--seed 0` を使った場合、補正込みでも同じシードを指定する。

~~~powershell
& .venv/Scripts/python.exe -m lyric_studio.evaluation compare local-data/baseline/sync.json local-data/refined/sync.json local-data/reference.json local-data/comparison.json
~~~

注釈の途中確認には `--allow-partial` を付ける。記録済みの行だけを比較し、部分結果は固定評価セットの集計対象から除く。

### 正解注釈を作らない自動チェック

自動生成した同期JSONを、生成で使っていないローカル認識モデルとボーカルの音量で検査できる。元音声を別モデルで認識して前後の歌詞と照合し、候補との開始・終了の差、歌詞の対応率、分離ボーカルの音量を行ごとに評価する。両端の差が500ms以内、別モデルの文字対応率が65%以上、区間内のボーカル活動率が35%以上なら自動チェックで「裏付けあり」とする。元の時刻を前後2秒ずらした場合の一致数も併記する。

~~~powershell
$checkModel = & .venv/Scripts/python.exe -c 'from faster_whisper.utils import download_model; print(download_model("medium"))'
& .venv/Scripts/python.exe -m lyric_studio.auto_evaluation local-data/refined/sync.json '録音.mp3' '歌詞.txt' 'ボーカル.wav' $checkModel local-data/auto-audit.json --device cuda
~~~

自動チェックの一致指標は、音声認識間の一致と音量の裏付けを示す。正解時刻に対する精度を計測するときは、注釈を使った採点結果と区別する。 [確認用モデル](https://huggingface.co/Systran/faster-whisper-medium)
