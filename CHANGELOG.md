# Changelog

このプロジェクトの変更履歴。[Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) 形式、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従う。

## [0.3.0] - 2026-08-02

### Added
- ruff による静的解析を導入し、CI に lint ジョブを追加
- PR / Issue テンプレートを追加
- 設計文書を追加(docs/architecture.md・docs/data_flow.md)、開発規約を CONTRIBUTING.md に明文化
- AI 書き出しに認証情報が含まれないことを保証する回帰テストと画面表記を追加

### Changed
- UI・文書の絵文字を撤去し、表記をプレーンに統一

### Fixed
- 企業一覧の志望度ソートが文字列順(A→B→C→S)になっており、志望度Sが末尾に表示される問題を修正
- 選考ファネルのステップが辞書順で描画される問題を修正(Altair で選考順に固定)
- README のテスト件数の記載が古くなる問題(件数を書かず CI を参照する形に変更)

## [0.2.0] - 2026-08-01

### Added
- 企業研究リンクの自動生成(公式・採用・事業内容・IR・クチコミ・選考体験記・ニュース)
- AI分析ページ: 選考データを分析依頼プロンプトつき Markdown に書き出し(アプリ自体は AI と通信しない)
- デモデータ生成スクリプト `scripts/demo_data.py`(架空企業8社)
- README に実画面スクリーンショット4枚を掲載

### Fixed
- 選考ファネルの配色を直感に合わせ修正(通過=緑 / 落選=赤)
- サーバーを 127.0.0.1 のみにバインドし、同一LAN内への意図しない公開を防止

## [0.1.0] - 2026-08-01

### Added
- 初版: ダッシュボード(締切アラート)・企業管理・ES設問ライブラリ・通過率分析
- SQLite ローカル保存(パスワード非保存方針)、pytest によるテスト、GitHub Actions CI
