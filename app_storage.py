# coding: utf-8
"""
app_storage.py

Pythonistaアプリ用の保存補助モジュールです。

この golf アプリでは、次の3種類のフォルダを使います。

    data   : JSONなど、アプリ本体が使うデータ
    export : CSVやHTMLなど、あとから確認する出力ファイル
    backup : JSONを上書き保存する前のバックアップ

基本方針:
    - app.py と同じフォルダをアプリの基準フォルダにする
    - data / export / backup は自動作成する
    - JSON保存は一時ファイル経由で、できるだけ壊れにくくする
    - golf_active_round_id.json のような小さな状態ファイルは save_json()
      を使えばバックアップなしで保存できる
    - golf_data.json のような本体データは save_json_with_backup()
      を使えば、上書き前に backup へ退避できる
"""

import os
import json
import csv
import time
import datetime
import inspect
import shutil

try:
    import editor
except Exception:
    editor = None


DATA_DIR_NAME = 'data'
EXPORT_DIR_NAME = 'export'
BACKUP_DIR_NAME = 'backup'
MANAGED_FOLDER_NAMES = set([DATA_DIR_NAME, EXPORT_DIR_NAME, BACKUP_DIR_NAME])

_APP_FOLDER_CACHE = None


# =============================
# パス関連
# =============================
def normalize_path(path):
    """~ や相対パスを、比較しやすい絶対パスへそろえる。"""
    return os.path.abspath(os.path.expanduser(str(path)))


def _path_contains_app_py(folder):
    """指定フォルダに app.py があるかを確認する。"""
    try:
        return os.path.isfile(os.path.join(folder, 'app.py'))
    except Exception:
        return False


def _climb_if_managed_subfolder(folder):
    """現在地が data/export/backup の中なら、親フォルダを基準に戻す。"""
    folder = normalize_path(folder)
    base_name = os.path.basename(folder)
    if base_name in MANAGED_FOLDER_NAMES:
        return os.path.dirname(folder)
    return folder


def _caller_app_folder():
    """app_storage.py を呼び出した側の .py ファイルがあるフォルダを探す。

    app_storage.py を app.py と同じフォルダに置く場合はもちろん、
    将来 app_storage.py を共通モジュール置き場に置いた場合でも、
    呼び出し元の app.py 側を基準フォルダにしやすくするための処理です。
    """
    try:
        this_file = normalize_path(__file__)
    except Exception:
        this_file = ''

    try:
        for frame_info in inspect.stack():
            globals_dict = frame_info.frame.f_globals
            file_path = globals_dict.get('__file__')
            if not file_path:
                continue

            file_path = normalize_path(file_path)
            if this_file and file_path == this_file:
                continue

            folder = _climb_if_managed_subfolder(os.path.dirname(file_path))
            if folder:
                return folder
    except Exception:
        pass

    return None


def _editor_folder():
    """Pythonistaのエディタで開いているファイルのフォルダを取得する。"""
    if editor is None:
        return None

    try:
        path = editor.get_path()
        if path:
            return _climb_if_managed_subfolder(os.path.dirname(normalize_path(path)))
    except Exception:
        pass

    return None


def _module_folder():
    """app_storage.py 自身のフォルダを取得する。"""
    try:
        return _climb_if_managed_subfolder(os.path.dirname(normalize_path(__file__)))
    except Exception:
        return None


def _cwd_folder():
    """現在の作業フォルダを取得する。"""
    try:
        return _climb_if_managed_subfolder(os.getcwd())
    except Exception:
        return None


def _documents_folder():
    """最後の保険として Pythonista の Documents フォルダ相当を返す。"""
    return normalize_path('~/Documents')


def initialize_app_folder(force=False):
    """アプリの基準フォルダを確定する。

    優先順位:
        1. 呼び出し元 app.py のフォルダ
        2. Pythonista editor で開いているファイルのフォルダ
        3. app_storage.py 自身のフォルダ
        4. os.getcwd()
        5. ~/Documents

    一度決めたフォルダはキャッシュします。
    """
    global _APP_FOLDER_CACHE

    if _APP_FOLDER_CACHE and not force:
        return _APP_FOLDER_CACHE

    candidates = [
        _caller_app_folder(),
        _editor_folder(),
        _module_folder(),
        _cwd_folder(),
        _documents_folder(),
    ]

    # app.py があるフォルダを最優先にする。
    for folder in candidates:
        if folder and _path_contains_app_py(folder):
            _APP_FOLDER_CACHE = normalize_path(folder)
            ensure_storage_dirs()
            return _APP_FOLDER_CACHE

    # app.py が見つからない場合は、最初に有効だったフォルダを使う。
    for folder in candidates:
        if folder:
            _APP_FOLDER_CACHE = normalize_path(folder)
            ensure_storage_dirs()
            return _APP_FOLDER_CACHE

    _APP_FOLDER_CACHE = _documents_folder()
    ensure_storage_dirs()
    return _APP_FOLDER_CACHE


def stabilize_startup(wait=0.2):
    """Pythonista起動直後のパス揺れを避けるため、少し待ってから保存先を確定する。"""
    try:
        if wait and wait > 0:
            time.sleep(wait)
    except Exception:
        pass

    return initialize_app_folder(force=True)


def app_folder_path():
    return initialize_app_folder()


def data_folder_path():
    return os.path.join(app_folder_path(), DATA_DIR_NAME)


def export_folder_path():
    return os.path.join(app_folder_path(), EXPORT_DIR_NAME)


def backup_folder_path():
    return os.path.join(app_folder_path(), BACKUP_DIR_NAME)


def ensure_dir(path):
    path = normalize_path(path)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def ensure_storage_dirs():
    """data / export / backup フォルダを作成する。"""
    base = _APP_FOLDER_CACHE or _documents_folder()
    for name in (DATA_DIR_NAME, EXPORT_DIR_NAME, BACKUP_DIR_NAME):
        try:
            ensure_dir(os.path.join(base, name))
        except Exception:
            pass


def is_path_inside_base(path, base):
    """path が base の内側にあるかを確認する。"""
    try:
        path = normalize_path(path)
        base = normalize_path(base)
        common = os.path.commonpath([path, base])
        return common == base
    except Exception:
        return False


def assert_safe_storage_path(path):
    """アプリ基準フォルダの外へ誤保存しないための安全確認。"""
    base = app_folder_path()
    if not is_path_inside_base(path, base):
        raise ValueError('保存先がアプリフォルダ外です: {}'.format(path))


def data_file_path(filename):
    path = os.path.join(data_folder_path(), filename)
    assert_safe_storage_path(path)
    ensure_dir(os.path.dirname(path))
    return path


def export_file_path(filename):
    path = os.path.join(export_folder_path(), filename)
    assert_safe_storage_path(path)
    ensure_dir(os.path.dirname(path))
    return path


def backup_file_path(filename):
    path = os.path.join(backup_folder_path(), filename)
    assert_safe_storage_path(path)
    ensure_dir(os.path.dirname(path))
    return path


# =============================
# JSON
# =============================
def load_json(filename, default=None, retry=2, wait=0.1):
    """dataフォルダからJSONを読み込む。

    読み込みに失敗した場合は default を返します。
    iCloud同期直後などの一時的な読み込み失敗に備えて、軽くリトライします。
    """
    path = data_file_path(filename)

    if not os.path.exists(path):
        return default

    last_error = None
    for attempt in range(retry + 1):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            last_error = e
            try:
                time.sleep(wait)
            except Exception:
                pass

    print('load_json error:', filename, last_error)
    return default


def _atomic_write_text(path, text):
    """一時ファイルへ書いてから置き換える簡易的な安全保存。"""
    path = normalize_path(path)
    assert_safe_storage_path(path)
    ensure_dir(os.path.dirname(path))

    tmp_path = path + '.tmp'

    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(text)

    try:
        os.replace(tmp_path, path)
    except Exception:
        # Pythonista/iCloud環境で os.replace がうまくいかない場合の保険。
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def save_json(filename, data):
    """dataフォルダへJSONを保存する。バックアップは作らない。"""
    path = data_file_path(filename)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    _atomic_write_text(path, text)
    return path


def backup_json(filename):
    """既存のJSONファイルを backup フォルダへコピーする。"""
    src_path = data_file_path(filename)
    if not os.path.exists(src_path):
        return None

    root, ext = os.path.splitext(filename)
    if not ext:
        ext = '.json'

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = '{}_{}{}'.format(root, timestamp, ext)
    dst_path = backup_file_path(backup_name)

    try:
        shutil.copy2(src_path, dst_path)
        return dst_path
    except Exception as e:
        print('backup_json error:', filename, e)
        return None


def save_json_with_backup(filename, data):
    """既存JSONをバックアップしてから保存する。"""
    try:
        backup_json(filename)
    except Exception as e:
        print('backup before save error:', filename, e)

    return save_json(filename, data)


# =============================
# CSV
# =============================
def save_csv(filename, rows, fieldnames=None, encoding='utf-8-sig'):
    """exportフォルダへCSVを保存する。

    fieldnames を指定すると、列順を固定できます。
    rows に存在しない列は空欄になり、余分な列は無視します。
    """
    path = export_file_path(filename)

    if rows is None:
        rows = []

    rows = list(rows)

    if fieldnames is None:
        fieldnames = []
        for row in rows:
            if isinstance(row, dict):
                for key in row.keys():
                    if key not in fieldnames:
                        fieldnames.append(key)

    with open(path, 'w', newline='', encoding=encoding) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction='ignore'
        )
        writer.writeheader()

        for row in rows:
            if isinstance(row, dict):
                writer.writerow(row)

    return path


def append_csv(filename, row, fieldnames=None, encoding='utf-8-sig'):
    """exportフォルダのCSVへ1行追加する。なければヘッダーも作る。"""
    path = export_file_path(filename)

    if fieldnames is None:
        if isinstance(row, dict):
            fieldnames = list(row.keys())
        else:
            fieldnames = []

    file_exists = os.path.exists(path)
    needs_header = not file_exists or os.path.getsize(path) == 0

    with open(path, 'a', newline='', encoding=encoding) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction='ignore'
        )
        if needs_header:
            writer.writeheader()
        if isinstance(row, dict):
            writer.writerow(row)

    return path


# =============================
# 表示用
# =============================
def app_folder_display_text():
    """画面下部などに出す短い保存先表示。"""
    return '保存先: {}'.format(app_folder_path())


def storage_info_text():
    """デバッグ用に保存先情報をまとめて返す。"""
    lines = [
        'app: {}'.format(app_folder_path()),
        'data: {}'.format(data_folder_path()),
        'export: {}'.format(export_folder_path()),
        'backup: {}'.format(backup_folder_path()),
    ]

    try:
        data_files = os.listdir(data_folder_path())
        lines.append('data files: {}'.format(', '.join(data_files) if data_files else 'なし'))
    except Exception:
        pass

    try:
        export_files = os.listdir(export_folder_path())
        lines.append('export files: {}'.format(', '.join(export_files) if export_files else 'なし'))
    except Exception:
        pass

    return '\n'.join(lines)
