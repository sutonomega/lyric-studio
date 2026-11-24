# Lyric Studio

Windows 11で、日本語の入力歌詞を楽曲に同期し、編集して字幕付きMVを出力するローカルアプリの開発計画です。現在は Phase 0 の設計レビュー段階です。

## 設計資料

- [要求仕様書](docs/requirements.md)
- [基本設計書](docs/basic-design.md)
- [技術選定案](docs/technology-selection.md)
- [歌詞同期の技術検証計画](docs/sync-validation.md)

## 開発の進め方

1. [設計レビュー #1](https://github.com/sutonomega/lyric-studio/issues/1)で初期版範囲、検証構成、評価条件を確認する。
2. [Phase 1](https://github.com/sutonomega/lyric-studio/milestone/2)で実曲を使って、日本語の行同期の精度と手直し時間を比較する。
3. [移行判定 #7](https://github.com/sutonomega/lyric-studio/issues/7)で結果を確認してから、基本UIに進む。

作業を [Issue](https://github.com/sutonomega/lyric-studio/issues) 単位で進める。ブランチとPRは関連Issueを参照し、完了条件に対応するテスト結果とWindowsでの確認結果を残す。重要な仕様・技術の採否は実装前に該当Issueで確認する。実曲、歌詞、認識全文、正解時刻と生成物はローカルに置く。[マイルストーン](https://github.com/sutonomega/lyric-studio/milestones)
