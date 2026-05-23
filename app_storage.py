# coding: utf-8
import os
import json
import time
import csv

try:
    import editor
except ImportError:
    editor = None


# =====================================
# 共通設定
# =====================================
STARTUP_WAIT_SECONDS = 0.2
RETRY_COUNT = 5
RETRY_WAIT_SECONDS = 0.2

DATA_DIR_NAME = 'data'
EXPORT_DIR_NAME = 'export'
BACKUP_DIR_NAME = 'backup'

MANAGED_FOLDER_NAMES = {
    DATA_DIR_NAME,
    EXPORT_DIR_NAME,
    BACKUP_DIR_NAME,
}

_APP_FOLDER_CACHE = None


# =====================================
# パス補助
# =====================================
def normalize_path(path):
    return os.path.normpath(os.path.abspath(path))


def is_existing_dir(path):
    return bool(path) and os.path.isdir(path)


def is_existing_file(path):
    return bool(path) and os.path.isfile(path)


def parent_dir(path):
    return os.path.dirname(normalize_path(path))


def basename(path):
    return os.path.basename(normalize_path(path))


def climb_if_managed_subfolder(path):
    """
    path が data / export / backup そのものなら親フォルダへ戻す。
    """
    path = normalize_path(path)
    name = basename(path)

    if name in MANAGED_FOLDER_NAMES:
        return parent_dir(path)

    return path


def path_contains_app_py(folder_path):
    """
    そのフォルダに app.py があるか確認する。
    """
    return is_existing_file(os.path.join(folder_path, 'app.py'))


def make_candidate_from_file(file_path):
    """
    ファイルパスから親フォルダ候補を作る。
    """
    if not file_path:
        return None

    try:
        file_path = normalize_path(file_path)
        if is_existing_file(file_path):
            return parent_dir(file_path)
    except Exception:
        pass

    return None


def make_candidate_from_dir(dir_path):
    """
    ディレクトリパスから候補を作る。
    data / export / backup の中なら親に戻す。
    """
    if not dir_path:
        return None

    try:
        dir_path = normalize_path(dir_path)
        if is_existing_dir(dir_path):
            return climb_if_managed_subfolder(dir_path)
    except Exception:
        pass

    return None


# =====================================
# アプリ基準フォルダの決定
# =====================================
def resolve_script_folder():
    """
    このアプリの基準フォルダを安全寄りに取得する。
    優先順位:
      1. __file__
      2. editor.get_path()
      3. cwd
      4. Documents

    さらに、
      - data/export/backup を指した場合は親へ戻す
      - app.py がある場所を優先する
    """
    candidates = []

    # 1. __file__
    try:
        file_path = os.path.abspath(__file__)
        candidate = make_candidate_from_file(file_path)
        if candidate:
            candidates.append(candidate)
    except Exception:
        pass

    # 2. editor.get_path()
    if editor is not None:
        try:
            editor_path = editor.get_path()
            candidate = make_candidate_from_file(editor_path)
            if candidate:
                candidates.append(candidate)
        except Exception:
            pass

    # 3. cwd
    try:
        cwd = os.getcwd()
        candidate = make_candidate_from_dir(cwd)
        if candidate:
            candidates.append(candidate)
    except Exception:
        pass

    # app.py がある候補を優先
    for candidate in candidates:
        if path_contains_app_py(candidate):
            return candidate

    # それ以外は最初の使える候補
    for candidate in candidates:
        if is_existing_dir(candidate):
            return candidate

    # 最後の保険
    return os.path.expanduser('~/Documents')


def initialize_app_folder():
    """
    起動時に1回だけアプリ基準フォルダを確定してキャッシュする。
    """
    global _APP_FOLDER_CACHE

    folder = resolve_script_folder()
    folder = normalize_path(folder)
    folder = climb_if_managed_subfolder(folder)

    _APP_FOLDER_CACHE = folder
    return _APP_FOLDER_CACHE


def app_folder_path():
    """
    アプリ全体の基準フォルダ。
    起動後はキャッシュを返すのでブレにくい。
    """
    global _APP_FOLDER_CACHE

    if _APP_FOLDER_CACHE is None:
        return initialize_app_folder()

    return _APP_FOLDER_CACHE


# =====================================
# サブフォルダ
# =====================================
def ensure_subfolder(name):
    path = os.path.join(app_folder_path(), name)
    path = normalize_path(path)
    os.makedirs(path, exist_ok=True)
    return path


def data_folder_path():
    return ensure_subfolder(DATA_DIR_NAME)


def export_folder_path():
    return ensure_subfolder(EXPORT_DIR_NAME)


def backup_folder_path():
    return ensure_subfolder(BACKUP_DIR_NAME)


def data_file_path(file_name):
    return normalize_path(os.path.join(data_folder_path(), file_name))


def export_file_path(file_name):
    return normalize_path(os.path.join(export_folder_path(), file_name))


def backup_file_path(file_name):
    return normalize_path(os.path.join(backup_folder_path(), file_name))


# =====================================
# 安全確認
# =====================================
def is_path_inside_base(target_path, base_path):
    target_path = normalize_path(target_path)
    base_path = normalize_path(base_path)

    try:
        common = os.path.commonpath([target_path, base_path])
        return common == base_path
    except Exception:
        return False


def assert_safe_storage_path(path):
    """
    保存先が app_folder 配下にあることを確認する。
    """
    base = app_folder_path()

    if not is_path_inside_base(path, base):
        raise RuntimeError(
            "保存先が想定外です。\n"
            f"path: {path}\n"
            f"app_folder: {base}"
        )


# =====================================
# 起動安定化
# =====================================
def stabilize_startup(wait_seconds=STARTUP_WAIT_SECONDS):
    """
    起動直後の揺れを避けつつ、基準フォルダを先に確定する。
    """
    try:
        time.sleep(wait_seconds)
    except Exception:
        pass

    initialize_app_folder()
    ensure_subfolder(DATA_DIR_NAME)
    ensure_subfolder(EXPORT_DIR_NAME)
    ensure_subfolder(BACKUP_DIR_NAME)


# =====================================
# JSON
# =====================================
def load_json(file_name, default=None,
              retry_count=RETRY_COUNT,
              wait_seconds=RETRY_WAIT_SECONDS):
    path = data_file_path(file_name)

    for _ in range(retry_count):
        try:
            if not os.path.exists(path):
                return default

            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)

        except FileNotFoundError:
            return default

        except json.JSONDecodeError:
            return default

        except OSError:
            time.sleep(wait_seconds)

        except Exception:
            time.sleep(wait_seconds)

    return default


def save_json(file_name, data):
    path = data_file_path(file_name)
    assert_safe_storage_path(path)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return path


def backup_json(file_name, data):
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    name, ext = os.path.splitext(file_name)
    backup_name = f'{name}_{timestamp}{ext}'
    path = backup_file_path(backup_name)
    assert_safe_storage_path(path)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return path


def save_json_with_backup(file_name, data):
    old_data = load_json(file_name, default=None)
    if old_data is not None:
        backup_json(file_name, old_data)

    return save_json(file_name, data)


# =====================================
# CSV
# =====================================
def save_csv(file_name, rows, fieldnames=None):
    path = export_file_path(file_name)
    assert_safe_storage_path(path)

    if rows is None:
        rows = []

    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        if not rows:
            if fieldnames:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            return path

        first_row = rows[0]

        if isinstance(first_row, dict):
            if fieldnames is None:
                fieldnames = list(first_row.keys())

            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        else:
            writer = csv.writer(f)
            writer.writerows(rows)

    return path


def append_csv(file_name, row, fieldnames=None):
    path = export_file_path(file_name)
    assert_safe_storage_path(path)

    file_exists = os.path.exists(path)

    with open(path, 'a', newline='', encoding='utf-8-sig') as f:
        if isinstance(row, dict):
            if fieldnames is None:
                fieldnames = list(row.keys())

            writer = csv.DictWriter(f, fieldnames=fieldnames)

            if not file_exists or os.path.getsize(path) == 0:
                writer.writeheader()

            writer.writerow(row)
        else:
            writer = csv.writer(f)
            writer.writerow(row)

    return path


# =====================================
# 表示用補助
# =====================================
def storage_info_text(file_name=None):
    lines = []
    lines.append('app_folder: ' + app_folder_path())
    lines.append('data_folder: ' + data_folder_path())
    lines.append('export_folder: ' + export_folder_path())
    lines.append('backup_folder: ' + backup_folder_path())

    if file_name:
        lines.append('data_file: ' + data_file_path(file_name))
        lines.append('export_file: ' + export_file_path(file_name))

    return '\n'.join(lines)


def app_folder_display_text():
    return '保存先: ' + app_folder_path()
