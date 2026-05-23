# Golf Direction Recorder / Pythonista

Pythonistaで動くゴルフショット記録アプリのベース版です。

## ファイル構成

- `app.py` : アプリ本体
- `app_storage.py` : JSON / CSV / バックアップ保存を担当する共通モジュール

## Pythonistaでの使い方

1. Pythonista内に任意のフォルダを作る
2. `app.py` と `app_storage.py` を同じフォルダに置く
3. `app.py` を実行する

## 基本操作

- 通常ショット: ボール地点で「現在地確定」→「ショット追加」
- パット: パットONにするとGPSは自動OFF
- 地図確認: ホール地図表示、またはラウンド地図表示

## 注意

WindowsのVSCodeでは `ui`, `dialogs`, `console`, `location` などのimport警告が出ることがあります。
これらはPythonista専用モジュールです。
