# Windows検証と正式版の判定

検証日 2026-11-25（日本時間）。対象は [Issue #15](https://github.com/sutonomega/lyric-studio/issues/15) の候補ブランチです。搭載機能の確認には合成素材を使い、既存の実曲検証は集計のみ参照しました。

## Windowsでの再現手順

1. 候補ソースのZIPを展開し、別ディレクトリで [READMEの起動手順](../README.md#windowsで起動する) を実行します。検証ではPython 3.12.10の新規venv、Node.js 24.15.0、FFmpeg／ffprobe 9.1 Essentialsを使いました。
2. 取得済みのsmallモデルを明示して起動します。CPUで確認する場合はデバイス指定を省き、NVIDIA GPUで確認する場合は[CUDAの導入手順](../backend/README.md#nvidia-gpuで音声認識を試す)を実行したPowerShellで `--device cuda` を付けます。作業データを分けるには `--data-dir` を指定します。
3. WAVとPNG、UTF-8の日本語歌詞TXTを読み込み、［同期を開始］から候補を適用します。字幕を編集し、Undo/Redo、文字サイズ変更、保存を順に確認します。再読み込み後に元の素材を再リンクし、ASS・SRT・9:16のMP4を保存します。
4. 別の再生・解析ツールでMP4を開き、映像、音声と焼き込んだ字幕を確認します。以前の検証を含めて、縦長・横長と元動画・静止画の各組み合わせを確認しました。

## 要求別の結果

| 要求 | 結果 |
| --- | --- |
| R01 素材と歌詞の読み込み | Chrome／Edgeで音声付きMP4、MP3・WAV、PNG・JPEG、貼り付け、UTF-8・CP932 TXTを確認。音声なし、非対応コーデック、破損ファイルへの案内を確認。 [PR #19](https://github.com/sutonomega/lyric-studio/pull/19) |
| R02 行単位の自動同期 | CPUとCUDAで合成音声の日本語歌詞を同期。実曲の歌唱時刻への誤差と作業時間は保留。 [Issue #7の結果](https://github.com/sutonomega/lyric-studio/issues/7) |
| R03 歌詞の読み取り | `Lyrics:`、構成タグ、空行、同じ本文の繰り返しを確認。 [PR #19](https://github.com/sutonomega/lyric-studio/pull/19) |
| R04 字幕編集 | タイムラインの境界、追加・削除、分割・結合、時間変更、一括移動、Undo/Redoを確認。 [PR #21](https://github.com/sutonomega/lyric-studio/pull/21) [PR #22](https://github.com/sutonomega/lyric-studio/pull/22) |
| R05 プレビュー | 波形、シーク、時刻と字幕の連動、スタイル変更を確認。 [PR #21](https://github.com/sutonomega/lyric-studio/pull/21) [PR #23](https://github.com/sutonomega/lyric-studio/pull/23) |
| R06 字幕スタイル | フォント・色・縁取り・位置・改行・フェードを確認。フォントの解決と描画は別環境のFFmpeg/libassでも確認。 [PR #23](https://github.com/sutonomega/lyric-studio/pull/23) |
| R07 字幕と動画の保存 | SRT・ASSおよび元動画・静止画×縦長・横長のMP4を確認。 [PR #23](https://github.com/sutonomega/lyric-studio/pull/23) [PR #24](https://github.com/sutonomega/lyric-studio/pull/24) |
| R08 プロジェクト | 保存・自動復元・再編集・再リンク・競合保存・キャッシュを確認。今回、同期結果を取り込んだ後の自動保存が繰り返される問題を修正し、保存リビジョンが安定することを再検証。 [PR #25](https://github.com/sutonomega/lyric-studio/pull/25) |
| R09 Windowsローカル実行 | 新規環境でビルドと起動。CPU・CUDA、進捗・キャンセル、外部接続を制限した同期と出力を確認。 [PR #20](https://github.com/sutonomega/lyric-studio/pull/20) [PR #24](https://github.com/sutonomega/lyric-studio/pull/24) |

## 外部接続を制限した検証

初回インストールではパッケージ配布元に接続し、実行では取得済みモデルを使いました。検証用のブラウザではアプリのループバック接続だけを許可しました。Pythonのサーバーと同期子プロセスには、ループバック以外のソケット接続を拒否する設定をテスト時だけ入れました。Windowsのネットワーク設定と物理接続は維持しました。

## 今回の実測

- 合成音声の6行を、CPUの原音方式、CPUのボーカル分離・補正方式、RTX 4070 SUPER（ドライバー617.42）のCUDA原音方式で同期しました。各試行で5行に時刻が付き、1行が未配置でした。各試行で解析を実行し、結果キャッシュは使用していません。
- Chromeから同期結果を編集し、復元後に日本語名のWAV・PNGを再リンクしました。保存したSRTとASSに変更後の2行を確認し、MP4は1080×1920・36秒、H.264映像・AAC音声でした。再生位置2.56秒のフレームで2行の焼き込みを確認しました。
- 同期結果を適用した後、保存済み画面で1.6秒間の待機中に保存リビジョンが更新されないことを確認しました。これを補う字幕データの回帰テスト1件と、Pythonの自動テスト29件、本番ビルドが通過しました。
- 候補ソースZIPの展開、Python依存の導入、npmのロックファイルからの導入を確認しました。ZIPには素材、生成物、モデル、実行ファイル、依存の作業ディレクトリを含めていません。

## 正式公開の判定

**正式版の判断は保留。** 合成素材でアプリの一連の機能は確認できました。実曲4曲の既存の自動チェックでは269行中150行に裏付けがあり、119行が要確認でした。この集計は別モデルとの一致やボーカルの活動を示します。実際の歌唱時刻に対する誤差、見逃した要修正箇所、手直し時間は未計測です。

正式公開前に、実曲での運用結果、公開範囲と本体のライセンスをレビューし、ユーザーとリリース可否を確認します。 [依存ライセンスと配布方式](distribution-notes.md) [Issue #15](https://github.com/sutonomega/lyric-studio/issues/15)
