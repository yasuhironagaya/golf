# coding: utf-8
"""
Golf Direction Recorder / Pythonista版

このファイルは、ゴルフのショット記録アプリのベース版です。

大まかな考え方:
    1. ラウンドを round_id で管理する
       - 同じ日に複数ラウンドを作っても混ざりにくくするためです。

    2. 各ホールにショット一覧を持たせる
       - ショットごとにクラブ、狙い、結果、GPS、メモを保存します。

    3. GPSは「そのショットを打つ地点」として記録する
       - 1打目の飛距離は「1打目地点 → 2打目地点」で計算します。
       - パットはGPS誤差の影響が大きいので、基本的にGPS OFFで記録します。

    4. GPSは一発採用しない
       - iPhoneは移動直後に古い位置を返すことがあるため、内部で2段階取得します。
       - 1回目はウォームアップ、2回目を本命として採用します。

    5. 保存は app_storage.py に任せる
       - data / export / backup フォルダを使い分けます。

GitHubで管理しやすいように、各処理ブロックにコメントを多めに入れています。
Pythonista専用の ui / dialogs / console / location を使うため、Windows上のVSCodeでは
import警告が出ることがありますが、Pythonista上では正常に使える前提です。
"""
import ui
import datetime
import dialogs
import copy
import uuid
import console
import math
import time
import os
import json
import webbrowser
import html

try:
    import location
except Exception:
    location = None

import app_storage


# =============================
# 設定
# =============================
DATA_FILE_NAME = 'golf_data.json'
COURSE_FILE_NAME = 'golf_courses.json'
EXPORT_FILE_NAME = 'golf_report.csv'

# 追加:
# 最後に操作していたラウンドIDを保存します。
# これにより、同じ日に複数ラウンドを作っても「日付だけ」で判定しません。
ACTIVE_ROUND_ID_FILE_NAME = 'golf_active_round_id.json'

# グリーンオン地点は、スコアに数えないGPS基準点として保存します。
# パットはGPS OFFで記録する運用にしたため、最後の通常ショットの
# 飛距離を計算するために、グリーン上のボール位置だけを別点として残します。
MARKER_TYPE_GREEN_ON = 'green_on'

CLUB_LIST = [
    'Driver', '3W', '5W', 'UT',
    '5I', '6I', '7I', '8I', '9I',
    'PW', 'AW', 'SW', 'LW',
    'Putter',
    'Other'
]

AIM_OPTIONS = [
    ('左', -2),
    ('やや左', -1),
    ('真ん中', 0),
    ('やや右', 1),
    ('右', 2),
]

ACTUAL_OPTIONS = [
    ('大きく左', -3),
    ('左', -2),
    ('やや左', -1),
    ('真ん中', 0),
    ('やや右', 1),
    ('右', 2),
    ('大きく右', 3),
]


# =============================
# UI スタイル
# =============================
COLOR_PRIMARY = '#2f6fed'
COLOR_PRIMARY_DARK = '#1f4fb8'
COLOR_SECONDARY = '#f2f6ff'
COLOR_BORDER = '#9db7f5'
COLOR_DANGER = '#fff1f1'
COLOR_DANGER_BORDER = '#e08a8a'
COLOR_LIGHT = '#f7f7f7'
COLOR_TEXT = '#222222'


def style_button(button, kind='normal'):
    """Pythonista の ui.Button を、文字だけでなくボタンらしく見せるための共通設定。"""
    button.font = ('<System-Bold>', 14)

    if kind == 'primary':
        button.background_color = COLOR_PRIMARY
        button.tint_color = 'white'
        button.border_color = COLOR_PRIMARY_DARK
    elif kind == 'danger':
        button.background_color = COLOR_DANGER
        button.tint_color = '#b00020'
        button.border_color = COLOR_DANGER_BORDER
    elif kind == 'selected':
        button.background_color = COLOR_PRIMARY
        button.tint_color = 'white'
        button.border_color = COLOR_PRIMARY_DARK
    else:
        button.background_color = COLOR_SECONDARY
        button.tint_color = COLOR_PRIMARY_DARK
        button.border_color = COLOR_BORDER

    button.border_width = 1
    try:
        button.corner_radius = 8
    except Exception:
        pass


def make_scoreboard_text(holes, current_index=None):
    """18ホールのスコアを、3列×6行で横幅に収まるように短く表示する。"""
    cells = []
    for i, hole in enumerate(holes):
        hole_no = hole.get('hole_no', i + 1)
        par = hole.get('par', 4)
        score = hole_score(hole)
        putts = hole.get('putts', 0)
        penalty = hole_penalty_count(hole)

        mark = '★' if current_index == i else ' '
        cells.append('{}{}H P{} S{} T{} E{}'.format(
            mark, hole_no, par, score, putts, penalty
        ))

    lines = []
    for row_start in range(0, 18, 3):
        row_cells = cells[row_start:row_start + 3]
        lines.append('  '.join(row_cells))

    return '\n'.join(lines)


# =============================
# round_id 関連
# =============================
def make_round_id():
    """ラウンドごとの一意IDを作る。

    日付だけではなく、時刻とUUIDの一部を含めます。
    例: R20260519_213045_ab12cd34
    """
    now = datetime.datetime.now()
    return 'R{}_{}'.format(
        now.strftime('%Y%m%d_%H%M%S'),
        uuid.uuid4().hex[:8]
    )


def get_round_id(round_data):
    """新旧データ互換用。

    新方式では round_id を正式なキーにします。
    旧データに id しかない場合は id を round_id として扱います。
    """
    if not isinstance(round_data, dict):
        return ''
    rid = str(round_data.get('round_id') or '').strip()
    if rid:
        return rid
    return str(round_data.get('id') or '').strip()


def round_id_short(round_data):
    rid = get_round_id(round_data)
    if not rid:
        return '未設定'
    if len(rid) <= 12:
        return rid
    return rid[:10] + '…' + rid[-4:]


def find_round_index_by_id(rounds, round_id):
    if not round_id:
        return None

    target = str(round_id).strip()
    for i, round_data in enumerate(rounds):
        if get_round_id(round_data) == target:
            return i
    return None


def find_latest_round_index(rounds):
    """最後に更新したラウンドを探す。

    ここは「日付」ではなく updated_at を優先します。
    同じ日に複数ラウンドがあっても、最新に操作したラウンドを再開できます。
    """
    if not rounds:
        return None

    candidates = []
    for i, round_data in enumerate(rounds):
        if not isinstance(round_data, dict):
            continue
        updated_at = str(round_data.get('updated_at') or '')
        created_at = str(round_data.get('created_at') or '')
        sort_key = updated_at or created_at or ''
        candidates.append((i, sort_key))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1])
    return candidates[-1][0]


def find_latest_today_round_index(rounds):
    """今日作成されたラウンドのうち、最後に更新したものを探す。"""
    today = today_str()
    candidates = []

    for i, round_data in enumerate(rounds):
        if round_data.get('date') == today:
            sort_key = str(round_data.get('updated_at') or round_data.get('created_at') or '')
            candidates.append((i, sort_key))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1])
    return candidates[-1][0]


def load_active_round_id():
    data = app_storage.load_json(ACTIVE_ROUND_ID_FILE_NAME, default={})
    if isinstance(data, dict):
        return str(data.get('round_id') or '').strip()
    if isinstance(data, str):
        return data.strip()
    return ''


def save_active_round_id(round_id):
    """最後に開いていたラウンドIDを保存する。

    このファイルは復帰用の小さな状態ファイルなので、通常のラウンド本体
    golf_data.json のように毎回バックアップを作らない。
    これにより backup フォルダが不要に増えにくくなる。
    """
    if not round_id:
        return

    data = {
        'round_id': round_id,
        'updated_at': now_iso()
    }

    if hasattr(app_storage, 'save_json'):
        app_storage.save_json(ACTIVE_ROUND_ID_FILE_NAME, data)
    else:
        app_storage.save_json_with_backup(ACTIVE_ROUND_ID_FILE_NAME, data)


# =============================
# 保存・読み込み
# =============================
def load_rounds():
    data = app_storage.load_json(DATA_FILE_NAME, default=[])
    if isinstance(data, list):
        return data
    return []


def save_rounds(rounds):
    app_storage.save_json_with_backup(DATA_FILE_NAME, rounds)
    export_rounds_csv(rounds)


def load_courses():
    data = app_storage.load_json(COURSE_FILE_NAME, default=[])
    if not isinstance(data, list):
        return []

    valid_courses = []
    for item in data:
        if not isinstance(item, dict):
            continue

        name = str(item.get('name', '')).strip()
        pars = item.get('pars', [])

        if not name:
            continue
        if not isinstance(pars, list) or len(pars) != 18:
            continue

        safe_pars = []
        ok = True
        for p in pars:
            try:
                par_val = int(p)
            except Exception:
                ok = False
                break
            if par_val not in (3, 4, 5):
                ok = False
                break
            safe_pars.append(par_val)

        if ok:
            valid_courses.append({
                'name': name,
                'pars': safe_pars
            })

    return valid_courses


def save_courses(courses):
    app_storage.save_json_with_backup(COURSE_FILE_NAME, courses)


def export_rounds_csv(rounds):
    rows = []

    for round_data in rounds:
        round_date = round_data.get('date', '')
        course_name = round_data.get('course_name', '')
        round_id = get_round_id(round_data)

        for hole in round_data.get('holes', []):
            hole_no = hole.get('hole_no', '')
            par = hole.get('par', '')
            putts = hole.get('putts', 0)
            penalty = hole_penalty_count(hole)
            score = hole_score(hole)
            shots = hole.get('shots', [])

            if not shots:
                rows.append({
                    'round_id': round_id,
                    'date': round_date,
                    'course_name': course_name,
                    'hole_no': hole_no,
                    'par': par,
                    'score': score,
                    'putts': putts,
                    'penalty_count': penalty,
                    'shot_no': '',
                    'is_marker': '',
                    'marker_type': '',
                    'is_putt': '',
                    'club': '',
                    'aim_label': '',
                    'aim_value': '',
                    'actual_label': '',
                    'actual_value': '',
                    'diff': '',
                    'latitude': '',
                    'longitude': '',
                    'gps_accuracy': '',
                    'gps_timestamp': '',
                    'gps_confirmed_at': '',
                    'gps_sample_count': '',
                    'gps_elapsed_sec': '',
                    'gps_stage': '',
                    'gps_stability_status': '',
                    'gps_stability_distance_m': '',
                    'gps_first_accuracy': '',
                    'gps_second_accuracy': '',
                    'elapsed_from_prev_gps_sec': '',
                    'distance_from_prev_yard': '',
                    'gps_warning': '',
                    'distance_to_next_yard': '',
                    'memo': ''
                })
            else:
                for shot in shots:
                    aim_val = shot.get('aim', 0)
                    actual_val = shot.get('actual', 0)
                    diff = actual_val - aim_val

                    rows.append({
                        'round_id': round_id,
                        'date': round_date,
                        'course_name': course_name,
                        'hole_no': hole_no,
                        'par': par,
                        'score': score,
                        'putts': putts,
                        'penalty_count': penalty,
                        'shot_no': shot.get('shot_no', ''),
                        'is_marker': shot.get('is_marker', False),
                        'marker_type': shot.get('marker_type', ''),
                        'is_putt': shot.get('is_putt', False),
                        'club': shot.get('club', ''),
                        'aim_label': aim_label(aim_val),
                        'aim_value': aim_val,
                        'actual_label': actual_label(actual_val),
                        'actual_value': actual_val,
                        'diff': diff,
                        'latitude': shot.get('latitude', ''),
                        'longitude': shot.get('longitude', ''),
                        'gps_accuracy': shot.get('gps_accuracy', ''),
                        'gps_timestamp': shot.get('gps_timestamp', ''),
                        'gps_confirmed_at': shot.get('gps_confirmed_at', ''),
                        'gps_sample_count': shot.get('gps_sample_count', ''),
                        'gps_elapsed_sec': shot.get('gps_elapsed_sec', ''),
                        'gps_stage': shot.get('gps_stage', ''),
                        'gps_stability_status': shot.get('gps_stability_status', ''),
                        'gps_stability_distance_m': shot.get('gps_stability_distance_m', ''),
                        'gps_first_accuracy': shot.get('gps_first_accuracy', ''),
                        'gps_second_accuracy': shot.get('gps_second_accuracy', ''),
                        'elapsed_from_prev_gps_sec': shot.get('elapsed_from_prev_gps_sec', ''),
                        'distance_from_prev_yard': shot.get('distance_from_prev_yard', ''),
                        'gps_warning': shot.get('gps_warning', ''),
                        'distance_to_next_yard': shot.get('distance_to_next_yard', ''),
                        'memo': shot.get('memo', '')
                    })

    fieldnames = [
        'round_id', 'date', 'course_name',
        'hole_no', 'par', 'score', 'putts', 'penalty_count',
        'shot_no', 'is_marker', 'marker_type', 'is_putt', 'club',
        'aim_label', 'aim_value',
        'actual_label', 'actual_value',
        'diff',
        'latitude', 'longitude', 'gps_accuracy', 'gps_timestamp',
        'gps_confirmed_at', 'gps_sample_count', 'gps_elapsed_sec',
        'gps_stage', 'gps_stability_status', 'gps_stability_distance_m',
        'gps_first_accuracy', 'gps_second_accuracy',
        'elapsed_from_prev_gps_sec', 'distance_from_prev_yard', 'gps_warning',
        'distance_to_next_yard',
        'memo'
    ]

    app_storage.save_csv(EXPORT_FILE_NAME, rows, fieldnames=fieldnames)


# =============================
# 共通関数
# =============================
def today_str():
    return datetime.date.today().isoformat()


def now_iso():
    return datetime.datetime.now().isoformat(timespec='seconds')


def safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def location_timestamp_seconds(loc):
    if not isinstance(loc, dict):
        return None

    for key in ('timestamp', 'time'):
        if key not in loc:
            continue
        value = loc.get(key)
        try:
            return float(value)
        except Exception:
            pass

    return None


# =============================
# GPS取得・GPS検証
# =============================
#
# iPhoneの位置情報は、移動直後に前地点のキャッシュを返すことがあります。
# このアプリでは、単純に「一番精度が良いGPS」を採用するのではなく、
#   - 数秒間サンプルを集める
#   - 後半に得られた位置を優先する
#   - 2段階取得で1回目と2回目の差を見る
#   - 前回ショット地点との時間・距離関係を見て保存前に警告する
# という方針で安定性を高めています。

def get_current_gps_location(timeout=8.0, warmup=0.0, desired_accuracy=12.0,
                             max_age_seconds=15.0, min_sampling_seconds=2.0,
                             _already_started=False):
    """Pythonista の location モジュールから現在地を取得する。

    時短版:
    - warmup をデフォルト 0 にし、呼び出し元が start_updates() を維持する場合は
      _already_started=True を渡すことで start/stop の往復を省略できる。
    - 数秒間サンプルを集め、後半に取れた位置を優先する点は従来と同じ。
    - min_sampling_seconds を短くし、精度が出たらすぐ早期終了する。
    - timeout も短縮（デフォルト 8 秒 → 早期終了で多くの場合 2〜4 秒で完了）。
    """
    if location is None:
        return None

    def better_location(current, candidate):
        if candidate is None:
            return current
        if current is None:
            return candidate

        cur_acc = current.get('accuracy')
        cand_acc = candidate.get('accuracy')

        if cand_acc is not None and cur_acc is None:
            return candidate
        if cand_acc is None and cur_acc is not None:
            return current
        if cand_acc is not None and cur_acc is not None:
            if cand_acc < cur_acc:
                return candidate
            if cand_acc == cur_acc and candidate.get('received_at_sec', 0) > current.get('received_at_sec', 0):
                return candidate
            return current

        if candidate.get('received_at_sec', 0) > current.get('received_at_sec', 0):
            return candidate
        return current

    try:
        if not _already_started:
            location.start_updates()

        if warmup > 0:
            time.sleep(warmup)

        start_time = time.time()
        samples = []
        sample_index = 0

        while time.time() - start_time < timeout:
            try:
                loc = location.get_location()
            except Exception:
                loc = None

            now_sec = time.time()

            if loc:
                lat = safe_float(loc.get('latitude'))
                lon = safe_float(loc.get('longitude'))
                acc = safe_float(loc.get('horizontal_accuracy'), None)
                loc_ts = location_timestamp_seconds(loc)

                if lat is not None and lon is not None:
                    sample_index += 1

                    is_fresh = True
                    gps_age = None
                    if loc_ts is not None:
                        try:
                            gps_age = now_sec - loc_ts
                            if gps_age < 0 or gps_age > max_age_seconds:
                                is_fresh = False
                        except Exception:
                            is_fresh = True

                    item = {
                        'latitude': lat,
                        'longitude': lon,
                        'accuracy': acc,
                        'timestamp': now_iso(),
                        'gps_confirmed_at': now_iso(),
                        'raw_timestamp': loc_ts,
                        'gps_age_sec': gps_age,
                        'is_fresh': is_fresh,
                        'sample_index': sample_index,
                        'sample_count': sample_index,
                        'received_at_sec': now_sec,
                    }
                    samples.append(item)

                    elapsed = now_sec - start_time
                    if elapsed >= min_sampling_seconds and is_fresh and acc is not None and acc <= desired_accuracy:
                        item['gps_elapsed_sec'] = round(elapsed, 1)
                        item['selection_reason'] = '後半サンプル・精度良好'
                        item['sample_count'] = len(samples)
                        return item

            time.sleep(0.4)

        if not samples:
            return None

        # 後半に取得したサンプルを優先する。
        last_index = samples[-1].get('sample_index', len(samples))
        recent_border = max(1, int(last_index * 0.6))
        recent_samples = [s for s in samples if s.get('sample_index', 0) >= recent_border]
        fresh_recent = [s for s in recent_samples if s.get('is_fresh', True)]
        fresh_all = [s for s in samples if s.get('is_fresh', True)]

        chosen = None
        reason = ''
        for group, group_reason in [
            (fresh_recent, '後半の新しいGPSを優先'),
            (recent_samples, '後半GPSを優先'),
            (fresh_all, '新しいGPSを優先'),
            (samples, 'GPS取得結果を使用'),
        ]:
            for item in group:
                chosen = better_location(chosen, item)
            if chosen is not None:
                reason = group_reason
                break

        if chosen is not None:
            chosen = dict(chosen)
            chosen['sample_count'] = len(samples)
            chosen['gps_elapsed_sec'] = round(time.time() - start_time, 1)
            chosen['selection_reason'] = reason
        return chosen

    except Exception:
        return None

    finally:
        # _already_started=True の場合は呼び出し元が stop_updates() を担う。
        if not _already_started:
            try:
                location.stop_updates()
            except Exception:
                pass


def get_stable_gps_location(first_timeout=4.0, second_timeout=5.0, pause_seconds=0.8,
                            desired_accuracy=12.0, max_age_seconds=15.0,
                            stability_warning_meters=20.0):
    """現在地確定ボタン1回で、内部的に2段階GPS取得を行う。

    時短版の変更点:
    - start_updates() を最初の1回だけ呼び、2段階を通して GPS をウォームアップ状態に保つ。
      これにより2回目開始時の再ウォームアップ待ちがなくなる。
    - pause_seconds を 1.5 → 0.8 秒に短縮。
    - second_timeout を 6.0 → 5.0 秒に短縮。
    - 合計の最悪時間: 約 11 秒 → 約 7 秒、早期終了なら 3〜5 秒程度。

    目的（従来と同じ）:
    - 1回目はiPhone側のGPSを現在地へ追いつかせるウォームアップとして扱う
    - 2回目を本命として採用する
    - 1回目と2回目の差を記録し、GPSが動いていたかをあとから確認できるようにする
    """
    if location is None:
        return None

    try:
        # GPS を起動し、2段階通して維持する。
        location.start_updates()

        first = get_current_gps_location(
            timeout=first_timeout,
            warmup=0.0,
            desired_accuracy=desired_accuracy,
            max_age_seconds=max_age_seconds,
            min_sampling_seconds=1.5,
            _already_started=True   # stop_updates() を呼ばせない
        )

        try:
            time.sleep(pause_seconds)
        except Exception:
            pass

        second = get_current_gps_location(
            timeout=second_timeout,
            warmup=0.0,
            desired_accuracy=desired_accuracy,
            max_age_seconds=max_age_seconds,
            min_sampling_seconds=2.0,
            _already_started=True   # stop_updates() を呼ばせない
        )

    finally:
        try:
            location.stop_updates()
        except Exception:
            pass

    # 2回目が取れなければ、1回目を保険として返す。
    if second is None:
        if first is not None:
            first = dict(first)
            first['gps_stage'] = '1回目のみ採用'
            first['gps_stability_status'] = '2回目取得失敗のため1回目を採用'
            first['gps_stability_distance_m'] = None
            first['gps_first_accuracy'] = first.get('accuracy')
            first['gps_second_accuracy'] = None
        return first

    result = dict(second)
    result['gps_stage'] = '2段階取得・2回目採用'
    result['gps_first_accuracy'] = first.get('accuracy') if first else None
    result['gps_second_accuracy'] = second.get('accuracy')

    # sample_count は2回分の合計も残す。
    first_count = safe_float(first.get('sample_count') if first else None, 0) or 0
    second_count = safe_float(second.get('sample_count'), 0) or 0
    try:
        result['gps_sample_count'] = int(first_count + second_count)
        result['sample_count'] = int(first_count + second_count)
    except Exception:
        pass

    # 1回目と2回目の位置差を記録する。
    stability_distance = None
    if first and has_gps(first) and has_gps(second):
        try:
            stability_distance = haversine_meters(
                safe_float(first.get('latitude')),
                safe_float(first.get('longitude')),
                safe_float(second.get('latitude')),
                safe_float(second.get('longitude'))
            )
            result['gps_stability_distance_m'] = round(stability_distance, 1)
        except Exception:
            result['gps_stability_distance_m'] = None
    else:
        result['gps_stability_distance_m'] = None

    if stability_distance is None:
        result['gps_stability_status'] = '2回目を採用'
    elif stability_distance <= stability_warning_meters:
        result['gps_stability_status'] = '安定'
    else:
        # 差が大きい場合は、1回目が古い地点だった可能性が高いので、2回目を採用したことを明示する。
        result['gps_stability_status'] = '1回目と差あり・2回目を採用'

    # 選択理由にも2段階取得であることを残す。
    base_reason = str(result.get('selection_reason') or '')
    if base_reason:
        result['selection_reason'] = '2段階取得 / ' + base_reason
    else:
        result['selection_reason'] = '2段階取得'

    result['gps_confirmed_at'] = now_iso()
    result['timestamp'] = result.get('timestamp') or now_iso()
    return result

def gps_fields_from_location(loc):
    if not loc:
        return {}
    return {
        'latitude': loc.get('latitude'),
        'longitude': loc.get('longitude'),
        'gps_accuracy': loc.get('accuracy'),
        'gps_timestamp': loc.get('timestamp'),
        'gps_confirmed_at': loc.get('gps_confirmed_at') or loc.get('timestamp') or now_iso(),
        'gps_raw_timestamp': loc.get('raw_timestamp'),
        'gps_age_sec': loc.get('gps_age_sec'),
        'gps_sample_count': loc.get('sample_count'),
        'gps_elapsed_sec': loc.get('gps_elapsed_sec'),
        'gps_selection_reason': loc.get('selection_reason', ''),
        'gps_stage': loc.get('gps_stage', ''),
        'gps_stability_status': loc.get('gps_stability_status', ''),
        'gps_stability_distance_m': loc.get('gps_stability_distance_m'),
        'gps_first_accuracy': loc.get('gps_first_accuracy'),
        'gps_second_accuracy': loc.get('gps_second_accuracy'),
    }


def has_gps(shot):
    return (
        safe_float(shot.get('latitude')) is not None and
        safe_float(shot.get('longitude')) is not None
    )


def haversine_meters(lat1, lon1, lat2, lon2):
    """緯度経度2点間の直線距離をメートルで返す。"""
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2 +
        math.cos(phi1) * math.cos(phi2) *
        math.sin(d_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return radius * c


def meters_to_yards(meters):
    return meters * 1.0936133


def distance_yards_between_shots(shot1, shot2):
    if not has_gps(shot1) or not has_gps(shot2):
        return None

    lat1 = safe_float(shot1.get('latitude'))
    lon1 = safe_float(shot1.get('longitude'))
    lat2 = safe_float(shot2.get('latitude'))
    lon2 = safe_float(shot2.get('longitude'))

    meters = haversine_meters(lat1, lon1, lat2, lon2)
    return round(meters_to_yards(meters), 1)


def recalc_shot_distances(hole_data):
    """同一ホール内で、各ショット地点から次のGPS地点までの距離を再計算する。

    パットはGPS OFFにする運用のため、最後の通常ショットの次地点がなくなりがちです。
    そこで、グリーンオン地点を補助点として保存できるようにし、
    通常ショット → グリーンオン地点 の距離も飛距離として計算します。
    補助点自身には飛距離を表示しません。
    """
    shots = hole_data.get('shots', [])
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue

        if is_marker_item(shot):
            shot['distance_to_next_yard'] = None
            continue

        next_point = None
        for j in range(i + 1, len(shots)):
            candidate = shots[j]
            if isinstance(candidate, dict) and has_gps(candidate):
                next_point = candidate
                break

        if next_point is not None:
            shot['distance_to_next_yard'] = distance_yards_between_shots(shot, next_point)
        else:
            shot['distance_to_next_yard'] = None


def distance_text(shot):
    distance = safe_float(shot.get('distance_to_next_yard'))
    if distance is None:
        return ''
    return '飛距離: {:.1f}yd'.format(distance)


def gps_status_text(shot):
    if not has_gps(shot):
        return 'GPSなし'

    acc = safe_float(shot.get('gps_accuracy'))
    if acc is None:
        base = 'GPSあり'
    else:
        base = 'GPSあり / 精度 約{:.0f}m'.format(acc)

    sample_count = shot.get('gps_sample_count')
    elapsed = safe_float(shot.get('gps_elapsed_sec'))
    extras = []
    if sample_count:
        extras.append('取得{}回'.format(sample_count))
    if elapsed is not None:
        extras.append('約{:.1f}秒'.format(elapsed))
    stage = str(shot.get('gps_stage') or '').strip()
    if stage:
        extras.append('2段階')
    stability_distance = safe_float(shot.get('gps_stability_distance_m'))
    if stability_distance is not None:
        extras.append('安定差{:.1f}m'.format(stability_distance))

    if extras:
        return base + ' / ' + ' / '.join(extras)
    return base


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value))
    except Exception:
        return None


def seconds_between_iso(start_value, end_value):
    start_dt = parse_iso_datetime(start_value)
    end_dt = parse_iso_datetime(end_value)
    if not start_dt or not end_dt:
        return None
    try:
        return (end_dt - start_dt).total_seconds()
    except Exception:
        return None


def update_gps_time_checks(hole_data, short_yards=10.0, long_yards=350.0, elapsed_seconds=60.0):
    """GPSの時間差と距離差を使って、怪しい記録に警告情報を付ける。"""
    shots = hole_data.get('shots', [])
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue

        shot['elapsed_from_prev_gps_sec'] = None
        shot['distance_from_prev_yard'] = None
        shot['gps_warning'] = ''

        # グリーンオン地点は補助点なので、警告対象にはしません。
        if is_marker_item(shot):
            continue

        # 直前の「GPSを持つ点」を探します。
        # パットなどGPSなしの記録が間に入っても、距離判定が壊れにくくなります。
        prev = None
        for j in range(i - 1, -1, -1):
            candidate = shots[j]
            if isinstance(candidate, dict) and has_gps(candidate):
                prev = candidate
                break

        if prev is None:
            continue

        distance = distance_yards_between_shots(prev, shot)
        elapsed = seconds_between_iso(prev.get('gps_confirmed_at') or prev.get('gps_timestamp'),
                                      shot.get('gps_confirmed_at') or shot.get('gps_timestamp'))

        if elapsed is not None:
            shot['elapsed_from_prev_gps_sec'] = round(elapsed, 1)
        if distance is not None:
            shot['distance_from_prev_yard'] = distance

        warnings = []
        prev_is_putt = bool(prev.get('is_putt', False))
        prev_is_marker = is_marker_item(prev)

        if distance is not None and elapsed is not None and not prev_is_putt and not prev_is_marker:
            if elapsed >= elapsed_seconds and distance < short_yards:
                warnings.append('前回地点から近すぎます。古いGPSを拾った可能性があります。')
            if distance > long_yards:
                warnings.append('前回地点から遠すぎます。GPS地点を確認してください。')

        if warnings:
            shot['gps_warning'] = ' / '.join(warnings)

def elapsed_text(shot):
    elapsed = safe_float(shot.get('elapsed_from_prev_gps_sec'))
    if elapsed is None:
        return ''
    if elapsed < 60:
        return '前回から{:.0f}秒'.format(elapsed)
    return '前回から{:.1f}分'.format(elapsed / 60.0)


def gps_warning_text(shot):
    return str(shot.get('gps_warning') or '').strip()


def gps_warning_for_candidate_shot(hole_data, shots, candidate_shot, edit_index=None):
    """ショットを保存する前に、仮のショット一覧でGPS警告を判定する。

    実データは変更せず、保存前に「前回地点から近すぎる」などを確認するための関数。
    """
    try:
        temp_hole = copy.deepcopy(hole_data)
        temp_shots = copy.deepcopy(shots)
        temp_candidate = copy.deepcopy(candidate_shot)

        if edit_index is None:
            temp_shots.append(temp_candidate)
            target_index = len(temp_shots) - 1
        else:
            if not (0 <= edit_index < len(temp_shots)):
                return ''
            temp_shots[edit_index] = temp_candidate
            target_index = edit_index

        temp_hole['shots'] = temp_shots
        reorder_shot_numbers(temp_hole)

        checked_shots = temp_hole.get('shots', [])
        if 0 <= target_index < len(checked_shots):
            return gps_warning_text(checked_shots[target_index])
    except Exception as e:
        print('gps_warning_for_candidate_shot error:', e)

    return ''


def app_export_path(filename):
    """地図HTMLなど、ユーザーが確認するためのファイル保存先を返す。"""
    try:
        if hasattr(app_storage, 'export_file_path'):
            return app_storage.export_file_path(filename)
    except Exception:
        pass

    base_dir = None
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base_dir = os.getcwd()

    export_dir = os.path.join(base_dir, 'export')
    try:
        os.makedirs(export_dir, exist_ok=True)
    except Exception:
        export_dir = base_dir
    return os.path.join(export_dir, filename)


def shot_map_point_text(hole_no, shot):
    """地図マーカーに表示する説明文を作る。"""
    shot_no = shot.get('shot_no', '')
    club = 'Putter' if shot.get('is_putt') else shot.get('club', '')
    d_text = distance_text(shot)
    gps_text = gps_status_text(shot)
    memo = str(shot.get('memo', '')).strip()

    if is_green_on_marker(shot):
        parts = [
            '{}H グリーンオン地点'.format(hole_no),
            '種別: 距離計算用の補助点',
            gps_text,
        ]
    else:
        parts = [
            '{}H {}打目'.format(hole_no, shot_no),
            'クラブ: {}'.format(club),
            shot_result_text(shot),
            gps_text,
        ]
    if d_text:
        parts.append(d_text)
    e_text = elapsed_text(shot)
    if e_text:
        parts.append(e_text)
    confirmed_at = str(shot.get('gps_confirmed_at') or '').strip()
    if confirmed_at:
        parts.append('GPS確定: {}'.format(confirmed_at))
    w_text = gps_warning_text(shot)
    if w_text:
        parts.append('注意: {}'.format(w_text))
    if memo:
        parts.append('メモ: {}'.format(memo))
    return '\n'.join(parts)


def collect_hole_map_points(hole_data):
    points = []
    hole_no = hole_data.get('hole_no', '')
    for shot in hole_data.get('shots', []):
        if not has_gps(shot):
            continue
        lat = safe_float(shot.get('latitude'))
        lon = safe_float(shot.get('longitude'))
        if lat is None or lon is None:
            continue
        label = 'グリーンオン' if is_green_on_marker(shot) else '{}打目'.format(shot.get('shot_no', ''))
        title = '{}H {}'.format(hole_no, label)
        points.append({
            'lat': lat,
            'lon': lon,
            'label': label,
            'title': title,
            'popup': shot_map_point_text(hole_no, shot),
        })
    return points


def collect_round_map_groups(round_data):
    groups = []
    for hole in round_data.get('holes', []):
        points = collect_hole_map_points(hole)
        if points:
            groups.append({
                'name': '{}H'.format(hole.get('hole_no', '')),
                'points': points,
            })
    return groups


def build_shot_map_html(title, groups):
    """GPSショット地点をLeaflet地図HTMLとして作る実用表示版。"""
    all_points = []
    for group in groups:
        all_points.extend(group.get('points', []))

    if not all_points:
        return None

    center_lat = sum(p['lat'] for p in all_points) / len(all_points)
    center_lon = sum(p['lon'] for p in all_points) / len(all_points)

    safe_title = html.escape(title)
    data_json = json.dumps({
        'title': title,
        'center': {'lat': center_lat, 'lon': center_lon},
        'groups': groups,
    }, ensure_ascii=False)

    shot_rows = []
    for group in groups:
        group_name = html.escape(str(group.get('name', '')))
        shot_rows.append('<h3>{}</h3>'.format(group_name))
        shot_rows.append('<ol class="shot-list">')
        for p in group.get('points', []):
            popup_html = html.escape(str(p.get('popup', ''))).replace('\n', '<br>')
            shot_rows.append(
                '<li><div class="shot-title">{}</div><div class="shot-detail">{}</div></li>'.format(
                    html.escape(str(p.get('label', ''))),
                    popup_html
                )
            )
        shot_rows.append('</ol>')
    shot_list_html = '\n'.join(shot_rows)

    html_template = """<!doctype html>
<html lang=\"ja\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
<title>{title}</title>
<link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\">
<script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; font-family: -apple-system, BlinkMacSystemFont, \"Helvetica Neue\", sans-serif; background: #ffffff; color: #222222; }}
  #page {{ padding: 10px; box-sizing: border-box; }}
  .title {{ font-size: 18px; font-weight: 700; margin: 0 0 8px; }}
  #map {{ width: 100%; height: 72vh; min-height: 460px; border-radius: 12px; border: 1px solid #cccccc; background: #eeeeee; }}
  #map_message {{ display: none; margin-top: 8px; padding: 9px; border-radius: 8px; background: #fff8e5; color: #7a4b00; font-size: 13px; line-height: 1.45; }}
  .shot-label {{ background: #2f6fed; color: white; border-radius: 999px; padding: 4px 8px;
                 border: 2px solid white; box-shadow: 0 1px 5px rgba(0,0,0,0.35);
                 font-size: 12px; font-weight: 700; white-space: nowrap; }}
  .popup-text {{ white-space: pre-line; line-height: 1.5; font-size: 14px; }}
  .panel {{ background: #f7f9ff; border: 1px solid #b9c9f5; border-radius: 12px; padding: 10px; margin-top: 10px; }}
  .panel-title {{ font-size: 17px; font-weight: 700; margin-bottom: 6px; }}
  .shot-list {{ padding-left: 24px; margin: 6px 0 0; }}
  .shot-list li {{ margin-bottom: 10px; }}
  .shot-title {{ font-size: 15px; font-weight: 700; margin-bottom: 3px; }}
  .shot-detail {{ color: #444444; font-size: 13px; line-height: 1.45; }}
</style>
</head>
<body>
<div id=\"page\">
  <div class=\"title\">{title}</div>
  <div id=\"map\"></div>
  <div id=\"map_message\"></div>

  <div class=\"panel\" id=\"shots\">
    <div class=\"panel-title\">ショット一覧</div>
    {shot_list_html}
  </div>
</div>

<script>
const data = {data_json};

function showMessage(text) {{
    const el = document.getElementById('map_message');
    if (el) {{
        el.style.display = 'block';
        el.textContent = text;
    }}
}}

function escapeHtml(text) {{
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\"/g, '&quot;')
        .replace(/'/g, '&#039;');
}}

function drawMap() {{
    if (typeof L === 'undefined') {{
        showMessage('地図ライブラリを読み込めませんでした。通信環境を確認してください。下のショット一覧にはGPS記録内容を表示しています。');
        return;
    }}

    try {{
        // 背景地図は航空写真を初期表示にします。
        // ゴルフ場では標準地図よりも、フェアウェイ・池・林・グリーン周辺が見やすくなります。
        const satelliteMap = L.tileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
            {{
                maxZoom: 19,
                attribution: 'Tiles &copy; Esri'
            }}
        );

        // 航空写真で見づらい場合に備えて、従来の標準地図にも切り替えられるようにします。
        const standardMap = L.tileLayer(
            'https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
            {{
                maxZoom: 21,
                attribution: '&copy; OpenStreetMap contributors'
            }}
        );

        const map = L.map('map', {{
            center: [data.center.lat, data.center.lon],
            zoom: 17,
            layers: [satelliteMap]
        }});

        L.control.layers({{
            '航空写真': satelliteMap,
            '標準地図': standardMap
        }}).addTo(map);

        const bounds = [];
        for (const group of data.groups) {{
            const line = [];
            for (const p of group.points) {{
                const latlng = [p.lat, p.lon];
                line.push(latlng);
                bounds.push(latlng);

                const icon = L.divIcon({{
                    className: '',
                    html: '<div class=\"shot-label\">' + escapeHtml(p.label) + '</div>',
                    iconSize: [58, 28],
                    iconAnchor: [20, 14]
                }});

                L.marker(latlng, {{icon: icon, title: p.title}})
                    .addTo(map)
                    .bindPopup('<div class=\"popup-text\">' + escapeHtml(p.popup) + '</div>');
            }}
            if (line.length >= 2) {{
                L.polyline(line, {{weight: 4, opacity: 0.75}}).addTo(map).bindPopup(escapeHtml(group.name) + ' のショット軌跡');
            }}
        }}

        if (bounds.length >= 2) {{
            map.fitBounds(bounds, {{padding: [40, 40]}});
        }}

        setTimeout(function() {{ map.invalidateSize(); }}, 500);
    }} catch (e) {{
        showMessage('地図描画中にエラーが出ました: ' + e.message);
    }}
}}

setTimeout(drawMap, 700);
</script>
</body>
</html>
"""
    return html_template.format(
        title=safe_title,
        data_json=data_json,
        shot_list_html=shot_list_html
    )


def save_shot_map_html(title, groups, filename):
    """GPSショット地点をLeaflet地図としてHTML保存する。"""
    html_text = build_shot_map_html(title, groups)
    if not html_text:
        return None

    path = app_export_path(filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html_text)
    return path


def open_file_url(path):
    url = 'file://' + os.path.abspath(path)
    try:
        webbrowser.open(url)
        return True
    except Exception:
        return False


def make_empty_round():
    round_id = make_round_id()

    holes = []
    for i in range(1, 19):
        holes.append({
            'hole_no': i,
            'par': 4,
            'putts': 0,
            'penalty_count': 0,
            'shots': []
        })

    return {
        # 旧コードとの互換のため id も残します。
        # 正式な識別キーは round_id です。
        'id': round_id,
        'round_id': round_id,
        'date': today_str(),
        'course_name': '',
        'memo': '',
        'created_at': now_iso(),
        'updated_at': now_iso(),
        'ui_state': {
            'hole_page': 0,
            'last_hole_index': 0,
        },
        'holes': holes
    }


def ensure_round_has_id(round_data):
    """旧データも含めて round_id 方式にそろえる。"""
    if not isinstance(round_data, dict):
        round_data = make_empty_round()

    rid = get_round_id(round_data)
    if not rid:
        rid = make_round_id()

    round_data['round_id'] = rid
    # 互換用。既存コードやCSVで id を参照しても壊れないようにします。
    round_data['id'] = rid

    if not round_data.get('created_at'):
        round_data['created_at'] = now_iso()
    if not round_data.get('updated_at'):
        round_data['updated_at'] = now_iso()
    if 'course_name' not in round_data:
        round_data['course_name'] = ''
    if not round_data.get('date'):
        round_data['date'] = today_str()
    if 'memo' not in round_data:
        round_data['memo'] = ''

    if 'ui_state' not in round_data or not isinstance(round_data.get('ui_state'), dict):
        round_data['ui_state'] = {
            'hole_page': 0,
            'last_hole_index': 0,
        }
    round_data['ui_state'].setdefault('hole_page', 0)
    round_data['ui_state'].setdefault('last_hole_index', 0)

    holes = round_data.get('holes', [])
    if not isinstance(holes, list):
        holes = []
        round_data['holes'] = holes

    # 18ホール未満の旧データでも最低限動くように補完します。
    while len(holes) < 18:
        holes.append({
            'hole_no': len(holes) + 1,
            'par': 4,
            'putts': 0,
            'penalty_count': 0,
            'shots': []
        })

    for i, hole in enumerate(holes):
        if not isinstance(hole, dict):
            holes[i] = {
                'hole_no': i + 1,
                'par': 4,
                'putts': 0,
                'penalty_count': 0,
                'shots': []
            }
            hole = holes[i]

        hole.setdefault('hole_no', i + 1)
        hole.setdefault('par', 4)
        hole.setdefault('putts', 0)
        hole.setdefault('penalty_count', 0)
        hole.setdefault('shots', [])

        try:
            hole['penalty_count'] = int(hole.get('penalty_count', 0))
        except Exception:
            hole['penalty_count'] = 0

        shots = hole.get('shots', [])
        if not isinstance(shots, list):
            shots = []
            hole['shots'] = shots

        for shot in shots:
            if not isinstance(shot, dict):
                continue
            shot.setdefault('is_marker', False)
            shot.setdefault('marker_type', '')
            shot.setdefault('latitude', None)
            shot.setdefault('longitude', None)
            shot.setdefault('gps_accuracy', None)
            shot.setdefault('gps_timestamp', '')
            shot.setdefault('gps_confirmed_at', '')
            shot.setdefault('gps_raw_timestamp', None)
            shot.setdefault('gps_age_sec', None)
            shot.setdefault('gps_sample_count', None)
            shot.setdefault('gps_elapsed_sec', None)
            shot.setdefault('gps_selection_reason', '')
            shot.setdefault('gps_stage', '')
            shot.setdefault('gps_stability_status', '')
            shot.setdefault('gps_stability_distance_m', None)
            shot.setdefault('gps_first_accuracy', None)
            shot.setdefault('gps_second_accuracy', None)
            shot.setdefault('elapsed_from_prev_gps_sec', None)
            shot.setdefault('distance_from_prev_yard', None)
            shot.setdefault('gps_warning', '')
            shot.setdefault('distance_to_next_yard', None)
        recalc_shot_distances(hole)
        update_gps_time_checks(hole)

    return round_data


def clone_round(round_data):
    return copy.deepcopy(round_data)


def find_label_from_value(options, value):
    for label, v in options:
        if v == value:
            return label
    return ''


def aim_label(value):
    return find_label_from_value(AIM_OPTIONS, value)


def actual_label(value):
    return find_label_from_value(ACTUAL_OPTIONS, value)


def is_marker_item(shot):
    """ショット一覧内の補助地点かどうかを判定する。

    現在はグリーンオン地点を想定しています。
    補助地点は地図・距離計算には使いますが、スコアには数えません。
    """
    return bool(shot.get('is_marker'))


def is_green_on_marker(shot):
    return is_marker_item(shot) and shot.get('marker_type') == MARKER_TYPE_GREEN_ON


def is_score_shot(shot):
    """スコアに数える通常の打数かどうか。"""
    return isinstance(shot, dict) and not is_marker_item(shot)


def hole_penalty_count(hole_data):
    try:
        return int(hole_data.get('penalty_count', 0))
    except Exception:
        return 0


def hole_score(hole_data):
    # グリーンオン地点などの補助点はスコアに数えません。
    shot_count = 0
    for shot in hole_data.get('shots', []):
        if is_score_shot(shot):
            shot_count += 1
    penalty = hole_penalty_count(hole_data)
    return shot_count + penalty


def total_score(round_data):
    total = 0
    for hole in round_data.get('holes', []):
        total += hole_score(hole)
    return total


def total_penalty_count(round_data):
    total = 0
    for hole in round_data.get('holes', []):
        total += hole_penalty_count(hole)
    return total


def shot_result_text(shot):
    if is_green_on_marker(shot):
        return 'グリーンオン地点 | 距離計算用の補助点'
    if is_marker_item(shot):
        return '補助地点 | 距離計算用'

    club = shot.get('club', '')
    aim = aim_label(shot.get('aim', 0))
    actual = actual_label(shot.get('actual', 0))
    diff = shot.get('actual', 0) - shot.get('aim', 0)

    if diff > 0:
        diff_text = '右へ{}'.format(diff)
    elif diff < 0:
        diff_text = '左へ{}'.format(abs(diff))
    else:
        diff_text = '狙い通り'

    kind = 'パット' if shot.get('is_putt') else club
    return '{} | 狙い:{} → 実際:{} | {}'.format(kind, aim, actual, diff_text)


def calc_analysis(round_data, putt_mode=None):
    total = 0
    diff_sum = 0
    left_count = 0
    center_count = 0
    right_count = 0
    club_stats = {}

    for hole in round_data.get('holes', []):
        for shot in hole.get('shots', []):
            if is_marker_item(shot):
                continue
            is_putt = shot.get('is_putt', False)

            if putt_mode is True and not is_putt:
                continue
            if putt_mode is False and is_putt:
                continue

            aim = shot.get('aim', 0)
            actual = shot.get('actual', 0)
            diff = actual - aim
            club = 'Putter' if is_putt else shot.get('club', 'Other')

            total += 1
            diff_sum += diff

            if actual < 0:
                left_count += 1
            elif actual == 0:
                center_count += 1
            else:
                right_count += 1

            if club not in club_stats:
                club_stats[club] = {
                    'count': 0,
                    'diff_sum': 0,
                    'distance_count': 0,
                    'distance_sum': 0.0,
                    'distance_max': None,
                }

            club_stats[club]['count'] += 1
            club_stats[club]['diff_sum'] += diff

            distance = safe_float(shot.get('distance_to_next_yard'))
            if distance is not None and distance > 0:
                club_stats[club]['distance_count'] += 1
                club_stats[club]['distance_sum'] += distance
                if club_stats[club]['distance_max'] is None or distance > club_stats[club]['distance_max']:
                    club_stats[club]['distance_max'] = distance

    avg_diff = 0
    if total > 0:
        avg_diff = diff_sum / total

    return {
        'total': total,
        'avg_diff': avg_diff,
        'left_count': left_count,
        'center_count': center_count,
        'right_count': right_count,
        'club_stats': club_stats
    }


def add_analysis_block(lines, title, analysis):
    lines.append(title)
    lines.append('記録数: {}'.format(analysis['total']))
    lines.append('左に出た回数: {}'.format(analysis['left_count']))
    lines.append('真ん中付近: {}'.format(analysis['center_count']))
    lines.append('右に出た回数: {}'.format(analysis['right_count']))
    lines.append('平均ズレ: {:.2f}'.format(analysis['avg_diff']))

    if analysis['total'] == 0:
        lines.append('まだ記録がありません。')
    elif analysis['avg_diff'] > 0.3:
        lines.append('傾向: 右へ出やすいです。')
    elif analysis['avg_diff'] < -0.3:
        lines.append('傾向: 左へ出やすいです。')
    else:
        lines.append('傾向: 大きな左右偏りは少なめです。')

    lines.append('')


def build_analysis_text(round_data):
    shot_analysis = calc_analysis(round_data, putt_mode=False)
    putt_analysis = calc_analysis(round_data, putt_mode=True)

    lines = []
    lines.append('【全体情報】')
    lines.append('ラウンドID: {}'.format(get_round_id(round_data)))
    lines.append('ラウンド日: {}'.format(round_data.get('date', '')))
    lines.append('ゴルフ場: {}'.format(round_data.get('course_name', '未設定')))
    lines.append('合計スコア: {}'.format(total_score(round_data)))
    lines.append('合計ペナルティ: {}'.format(total_penalty_count(round_data)))
    lines.append('')

    add_analysis_block(lines, '【通常ショット分析】', shot_analysis)
    add_analysis_block(lines, '【パット方向分析】', putt_analysis)

    lines.append('【通常ショットのクラブ別】')
    if not shot_analysis['club_stats']:
        lines.append('まだショット記録がありません。')
    else:
        for club in sorted(shot_analysis['club_stats'].keys()):
            stat = shot_analysis['club_stats'][club]
            avg = stat['diff_sum'] / stat['count']
            distance_count = stat.get('distance_count', 0)
            if distance_count > 0:
                avg_distance = stat.get('distance_sum', 0.0) / distance_count
                max_distance = stat.get('distance_max')
                lines.append(
                    '{} : {}球 / 平均ズレ {:.2f} / 平均飛距離 {:.1f}yd / 最長 {:.1f}yd'.format(
                        club, stat['count'], avg, avg_distance, max_distance
                    )
                )
            else:
                lines.append('{} : {}球 / 平均ズレ {:.2f} / 飛距離記録なし'.format(club, stat['count'], avg))

    lines.append('')
    lines.append('【ホール別スコア・パット数】')
    for hole in round_data.get('holes', []):
        score = hole_score(hole)
        penalty = hole_penalty_count(hole)
        lines.append('{}H : Par{} / スコア{} / {}パット / ペナ{}'.format(
            hole.get('hole_no', 0),
            hole.get('par', 4),
            score,
            hole.get('putts', 0),
            penalty
        ))

    return '\n'.join(lines)


def reorder_shot_numbers(hole_data):
    """ホール内のショット番号を振り直す。

    グリーンオン地点は、GPS距離計算用の補助点です。
    そのため、ショット番号を進めず、スコアにもパット数にも含めません。
    """
    shots = hole_data.get('shots', [])
    putts = 0
    shot_no = 1
    for shot in shots:
        if not isinstance(shot, dict):
            continue

        if is_marker_item(shot):
            shot['shot_no'] = ''
            shot['is_putt'] = False
            continue

        shot['shot_no'] = shot_no
        shot_no += 1
        if shot.get('is_putt'):
            putts += 1

    hole_data['putts'] = putts
    recalc_shot_distances(hole_data)
    update_gps_time_checks(hole_data)


def total_shot_count(round_data):
    """スコアに数えるショット数だけを数える。

    グリーンオン地点は距離計算用の補助点なので、ここでは数えません。
    """
    count = 0
    for hole in round_data.get('holes', []):
        for shot in hole.get('shots', []):
            if is_score_shot(shot):
                count += 1
    return count


def round_sort_key(round_data):
    """過去ラウンド一覧の並び順に使うキー。

    updated_at があればそれを優先し、なければ created_at / date を使います。
    文字列の ISO 日時はそのままソートしやすいため、この形式で十分です。
    """
    if not isinstance(round_data, dict):
        return ''
    return str(
        round_data.get('updated_at') or
        round_data.get('created_at') or
        round_data.get('date') or
        ''
    )


def round_list_title(round_data):
    """過去ラウンド一覧に表示する1行目の文字列を作る。"""
    date = str(round_data.get('date') or '')
    course_name = str(round_data.get('course_name') or '').strip()
    if not course_name:
        course_name = '未設定'

    score = total_score(round_data)
    shots = total_shot_count(round_data)
    return '{}  {}  S{} / {}shot'.format(date, course_name, score, shots)


def round_list_detail(round_data):
    """過去ラウンド一覧に表示する2行目の文字列を作る。"""
    total_par = sum(h.get('par', 4) for h in round_data.get('holes', []))
    putts = sum(h.get('putts', 0) for h in round_data.get('holes', []))
    penalty = total_penalty_count(round_data)
    updated_at = str(round_data.get('updated_at') or '')
    if len(updated_at) >= 16:
        updated_at = updated_at[:16].replace('T', ' ')
    rid = round_id_short(round_data)
    return 'Par{} / Pt{} / Pe{} / 更新:{} / ID:{}'.format(
        total_par, putts, penalty, updated_at or '-', rid
    )


def round_operation_message(round_data):
    """ラウンド選択時の確認メッセージを作る。"""
    lines = [
        '日付: {}'.format(round_data.get('date', '')),
        'ゴルフ場: {}'.format(round_data.get('course_name') or '未設定'),
        'スコア: {}'.format(total_score(round_data)),
        'ショット数: {}'.format(total_shot_count(round_data)),
        'パット数: {}'.format(sum(h.get('putts', 0) for h in round_data.get('holes', []))),
        'ペナルティ: {}'.format(total_penalty_count(round_data)),
        'round_id: {}'.format(get_round_id(round_data)),
    ]
    return '\n'.join(lines)


def round_delete_confirm_message(round_data):
    """削除前に表示する確認メッセージ。

    削除は golf_data.json から対象ラウンドを取り除く操作です。
    うっかり削除を避けるため、日付・ゴルフ場・スコアを明示します。
    """
    lines = [
        'このラウンドを削除しますか?',
        '',
        '日付: {}'.format(round_data.get('date', '')),
        'ゴルフ場: {}'.format(round_data.get('course_name') or '未設定'),
        'スコア: {}'.format(total_score(round_data)),
        'ショット数: {}'.format(total_shot_count(round_data)),
        '',
        '削除すると data/golf_data.json から消えます。',
        '必要なら backup フォルダのJSONから復元してください。',
    ]
    return '\n'.join(lines)


def apply_course_pars(round_data, course_item):
    if not isinstance(course_item, dict):
        return False

    name = str(course_item.get('name', '')).strip()
    pars = course_item.get('pars', [])

    if not name or not isinstance(pars, list) or len(pars) != 18:
        return False

    holes = round_data.get('holes', [])
    if len(holes) != 18:
        return False

    round_data['course_name'] = name
    for i, par_value in enumerate(pars):
        holes[i]['par'] = int(par_value)
    return True


# =============================
# 安全版タップビュー
# =============================
class TappableView(ui.View):
    def touch_began(self, touch):
        pass


# =============================
# 地図表示画面
# =============================
class MapHtmlView(ui.View):
    """Pythonista内のWebViewでショット地図HTMLを表示する画面。"""
    def __init__(self, html_text, title='ショット地図'):
        super().__init__()
        self.name = title
        self.background_color = 'white'
        self.html_text = html_text

        self.webview = ui.WebView()
        self.webview.flex = 'WH'
        self.add_subview(self.webview)

        ui.delay(self.load_map_html, 0.5)

    def layout(self):
        self.webview.frame = self.bounds

    def load_map_html(self):
        try:
            self.webview.load_html(self.html_text)
        except Exception as e:
            console.hud_alert('地図HTMLを読み込めませんでした', 'error', 1.2)
            print('MapHtmlView load_html error:', e)


def open_shot_map_view(title, groups):
    """保存ファイルを外部ブラウザで開かず、Pythonista内で地図を開く。"""
    html_text = build_shot_map_html(title, groups)
    if not html_text:
        return False

    try:
        v = MapHtmlView(html_text, title=title)
        v.present('fullscreen')
        return True
    except Exception as e:
        print('open_shot_map_view error:', e)
        return False


# =============================
# ゴルフ場編集画面
# =============================
class CourseEditView(ui.View):
    def __init__(self, course_data=None, on_save_callback=None):
        super().__init__()
        self.name = 'ゴルフ場編集'
        self.background_color = 'white'
        self.on_save_callback = on_save_callback

        if course_data:
            self.course_data = copy.deepcopy(course_data)
        else:
            self.course_data = {
                'name': '',
                'pars': [4] * 18
            }

        self.make_ui()
        self.update_list()

    def make_ui(self):
        self.lbl_title = ui.Label()
        self.lbl_title.text = 'ゴルフ場編集'
        self.lbl_title.alignment = ui.ALIGN_CENTER
        self.lbl_title.font = ('<System-Bold>', 20)
        self.add_subview(self.lbl_title)

        self.tf_name = ui.TextField()
        self.tf_name.border_style = 'rounded_rect'
        self.tf_name.placeholder = 'ゴルフ場名'
        self.tf_name.text = self.course_data.get('name', '')
        self.add_subview(self.tf_name)

        self.btn_save = ui.Button(title='保存')
        self.btn_save.action = self.save_course
        style_button(self.btn_save, kind='primary')
        self.add_subview(self.btn_save)

        self.btn_all_par4 = ui.Button(title='すべて Par4')
        self.btn_all_par4.action = self.set_all_par4
        style_button(self.btn_all_par4)
        self.add_subview(self.btn_all_par4)

        self.tv = ui.TableView()
        self.tv.data_source = self
        self.tv.delegate = self
        self.add_subview(self.tv)

    def layout(self):
        margin = 12
        gap = 8
        y = 10
        w = self.width - margin * 2

        self.lbl_title.frame = (margin, y, w, 30)
        y += 40

        self.tf_name.frame = (margin, y, w, 36)
        y += 44

        btn_w = (w - gap) / 2
        self.btn_save.frame = (margin, y, btn_w, 36)
        self.btn_all_par4.frame = (margin + btn_w + gap, y, btn_w, 36)
        y += 44

        self.tv.frame = (margin, y, w, self.height - y - 10)

    def update_list(self):
        self.tv.reload()

    def set_all_par4(self, sender):
        self.course_data['pars'] = [4] * 18
        self.update_list()
        console.hud_alert('すべて Par4 にしました', 'success', 1.0)

    def save_course(self, sender):
        name = self.tf_name.text.strip()
        if not name:
            console.hud_alert('ゴルフ場名を入力してください', 'error', 1.2)
            return

        self.course_data['name'] = name

        if self.on_save_callback:
            self.on_save_callback(copy.deepcopy(self.course_data))

        self.close()

    def tableview_number_of_rows(self, tableview, section):
        return 18

    def tableview_cell_for_row(self, tableview, section, row):
        par_val = self.course_data.get('pars', [4] * 18)[row]
        cell = ui.TableViewCell('value1')
        cell.text_label.text = '{}H'.format(row + 1)
        cell.detail_text_label.text = 'Par{}'.format(par_val)
        cell.accessory_type = 'disclosure_indicator'
        return cell

    def tableview_did_select(self, tableview, section, row):
        selected = dialogs.list_dialog(
            '{}H のパー数'.format(row + 1),
            ['3', '4', '5']
        )
        if selected:
            self.course_data['pars'][row] = int(selected)
            self.update_list()

        tableview.selected_row = (-1, -1)


# =============================
# ゴルフ場管理画面
# =============================
class CourseManagerView(ui.View):
    def __init__(self, courses, on_save_callback=None):
        super().__init__()
        self.name = 'ゴルフ場管理'
        self.background_color = 'white'
        self.courses = copy.deepcopy(courses)
        self.on_save_callback = on_save_callback

        self.make_ui()
        self.update_list()

    def make_ui(self):
        self.lbl_title = ui.Label()
        self.lbl_title.text = 'ゴルフ場管理'
        self.lbl_title.alignment = ui.ALIGN_CENTER
        self.lbl_title.font = ('<System-Bold>', 20)
        self.add_subview(self.lbl_title)

        self.btn_add = ui.Button(title='新規追加')
        self.btn_add.action = self.add_course
        style_button(self.btn_add, kind='primary')
        self.add_subview(self.btn_add)

        self.tv = ui.TableView()
        self.tv.data_source = self
        self.tv.delegate = self
        self.add_subview(self.tv)

    def layout(self):
        margin = 12
        y = 10
        w = self.width - margin * 2

        self.lbl_title.frame = (margin, y, w, 30)
        y += 40

        self.btn_add.frame = (margin, y, w, 36)
        y += 44

        self.tv.frame = (margin, y, w, self.height - y - 10)

    def update_list(self):
        self.tv.reload()

    def commit_courses(self):
        self.courses.sort(key=lambda x: x.get('name', ''))
        if self.on_save_callback:
            self.on_save_callback(copy.deepcopy(self.courses))

    def add_course(self, sender):
        v = CourseEditView(on_save_callback=self.on_course_added)
        v.present('fullscreen')

    def on_course_added(self, course_data):
        name = course_data.get('name', '').strip()
        if not name:
            return

        for item in self.courses:
            if item.get('name', '').strip() == name:
                console.hud_alert('同じ名前のゴルフ場があります', 'error', 1.2)
                return

        self.courses.append(course_data)
        self.commit_courses()
        self.update_list()
        console.hud_alert('追加しました', 'success', 1.0)

    def on_course_edited(self, index, course_data):
        new_name = course_data.get('name', '').strip()
        if not new_name:
            return

        for i, item in enumerate(self.courses):
            if i != index and item.get('name', '').strip() == new_name:
                console.hud_alert('同じ名前のゴルフ場があります', 'error', 1.2)
                return

        self.courses[index] = course_data
        self.commit_courses()
        self.update_list()
        console.hud_alert('更新しました', 'success', 1.0)

    def delete_course(self, index):
        if index < 0 or index >= len(self.courses):
            return

        name = self.courses[index].get('name', '')
        choice = console.alert(
            '削除確認',
            '{} を削除しますか?'.format(name),
            '削除する',
            'やめる',
            hide_cancel_button=True
        )
        if choice == 1:
            del self.courses[index]
            self.commit_courses()
            self.update_list()
            console.hud_alert('削除しました', 'success', 1.0)

    def tableview_number_of_rows(self, tableview, section):
        return len(self.courses)

    def tableview_cell_for_row(self, tableview, section, row):
        course = self.courses[row]
        cell = ui.TableViewCell('subtitle')
        cell.text_label.text = course.get('name', '')
        total_par = sum(course.get('pars', [4] * 18))
        cell.detail_text_label.text = '合計Par {}'.format(total_par)
        cell.accessory_type = 'disclosure_indicator'
        return cell

    def tableview_did_select(self, tableview, section, row):
        if row < 0 or row >= len(self.courses):
            return

        course = self.courses[row]
        choice = console.alert(
            'ゴルフ場操作',
            course.get('name', ''),
            '編集',
            '削除',
            '閉じる',
            hide_cancel_button=True
        )

        if choice == 1:
            def _save_edited(course_data, idx=row):
                self.on_course_edited(idx, course_data)

            v = CourseEditView(course_data=course, on_save_callback=_save_edited)
            v.present('fullscreen')
        elif choice == 2:
            self.delete_course(row)

        tableview.selected_row = (-1, -1)


# =============================
# パー設定画面
# =============================
class ParSettingView(ui.View):
    def __init__(self, round_data, courses, on_save_callback=None):
        super().__init__()
        self.name = 'パー設定'
        self.background_color = 'white'
        self.round_data = round_data
        self.courses = copy.deepcopy(courses)
        self.on_save_callback = on_save_callback

        self.make_ui()
        self.update_course_label()
        self.update_list()

    def make_ui(self):
        self.lbl_title = ui.Label()
        self.lbl_title.text = '18ホール パー設定'
        self.lbl_title.alignment = ui.ALIGN_CENTER
        self.lbl_title.font = ('<System-Bold>', 20)
        self.add_subview(self.lbl_title)

        self.lbl_course = ui.Label()
        self.lbl_course.alignment = ui.ALIGN_CENTER
        self.lbl_course.font = ('<System>', 14)
        self.lbl_course.number_of_lines = 2
        self.add_subview(self.lbl_course)

        self.btn_select_course = ui.Button(title='ゴルフ場選択')
        self.btn_select_course.action = self.select_course
        style_button(self.btn_select_course, kind='primary')
        self.add_subview(self.btn_select_course)

        self.btn_all_par4 = ui.Button(title='すべて Par4')
        self.btn_all_par4.action = self.set_all_par4
        style_button(self.btn_all_par4)
        self.add_subview(self.btn_all_par4)

        self.tv = ui.TableView()
        self.tv.data_source = self
        self.tv.delegate = self
        self.add_subview(self.tv)

    def layout(self):
        margin = 12
        gap = 8
        y = 10
        w = self.width - margin * 2

        self.lbl_title.frame = (margin, y, w, 30)
        y += 36

        self.lbl_course.frame = (margin, y, w, 40)
        y += 46

        btn_w = (w - gap) / 2
        self.btn_select_course.frame = (margin, y, btn_w, 36)
        self.btn_all_par4.frame = (margin + btn_w + gap, y, btn_w, 36)
        y += 44

        self.tv.frame = (margin, y, w, self.height - y - 10)

    def update_list(self):
        self.tv.reload()

    def update_course_label(self):
        course_name = self.round_data.get('course_name', '').strip()
        rid = round_id_short(self.round_data)
        if course_name:
            self.lbl_course.text = '選択中: {}\nID: {}'.format(course_name, rid)
        else:
            self.lbl_course.text = '選択中: 未設定\nID: {}'.format(rid)

    def notify_saved(self):
        if self.on_save_callback:
            self.on_save_callback()

    def select_course(self, sender):
        if not self.courses:
            console.hud_alert('登録済みゴルフ場がありません', 'error', 1.2)
            return

        names = [x.get('name', '') for x in self.courses]
        selected_name = dialogs.list_dialog('ゴルフ場選択', names)
        if not selected_name:
            return

        selected_course = None
        for item in self.courses:
            if item.get('name', '') == selected_name:
                selected_course = item
                break

        if not selected_course:
            console.hud_alert('ゴルフ場が見つかりません', 'error', 1.2)
            return

        # ショット記録済みのラウンドでコースを変更すると、
        # スコアそのものは壊れませんが、後から見たときに違和感が出る可能性があります。
        # そのため確認を入れています。
        if total_shot_count(self.round_data) > 0:
            choice = console.alert(
                '確認',
                'このラウンドには既にショット記録があります。\nコース名とPar設定を変更しますか?',
                '変更する',
                'やめる',
                hide_cancel_button=True
            )
            if choice != 1:
                return

        ok = apply_course_pars(self.round_data, selected_course)
        if not ok:
            console.hud_alert('コース設定に失敗しました', 'error', 1.2)
            return

        self.notify_saved()
        self.update_course_label()
        self.update_list()
        console.hud_alert('{} を反映しました'.format(selected_name), 'success', 1.0)

    def set_all_par4(self, sender):
        if total_shot_count(self.round_data) > 0:
            choice = console.alert(
                '確認',
                'このラウンドには既にショット記録があります。\nすべて Par4 に変更しますか?',
                '変更する',
                'やめる',
                hide_cancel_button=True
            )
            if choice != 1:
                return

        for hole in self.round_data.get('holes', []):
            hole['par'] = 4
        self.round_data['course_name'] = ''
        self.notify_saved()
        self.update_course_label()
        self.update_list()
        console.hud_alert('すべて Par4 にしました', 'success', 1.0)

    def tableview_number_of_rows(self, tableview, section):
        return len(self.round_data.get('holes', []))

    def tableview_cell_for_row(self, tableview, section, row):
        hole = self.round_data.get('holes', [])[row]
        cell = ui.TableViewCell('value1')
        cell.text_label.text = '{}H'.format(hole.get('hole_no', 0))
        cell.detail_text_label.text = 'Par{}'.format(hole.get('par', 4))
        cell.accessory_type = 'disclosure_indicator'
        return cell

    def tableview_did_select(self, tableview, section, row):
        holes = self.round_data.get('holes', [])
        if row < 0 or row >= len(holes):
            return

        current_par = holes[row].get('par', 4)
        selected = dialogs.list_dialog(
            '{}H のパー数'.format(holes[row].get('hole_no', row + 1)),
            ['3', '4', '5']
        )
        if selected:
            new_par = int(selected)
            if new_par != current_par:
                holes[row]['par'] = new_par
                self.notify_saved()
                self.update_list()
                console.hud_alert('{}H を Par{} にしました'.format(row + 1, new_par), 'success', 1.0)

        tableview.selected_row = (-1, -1)


# =============================
# スコアボード画面
# =============================
class ScoreboardView(ui.View):
    def __init__(self, holes, current_index=0):
        super().__init__()
        self.name = 'スコア表示'
        self.background_color = 'white'
        self.holes = holes
        self.current_index = current_index
        self.make_ui()
        self.update_summary()

    def make_ui(self):
        self.lbl_title = ui.Label()
        self.lbl_title.text = '18ホール スコア'
        self.lbl_title.alignment = ui.ALIGN_CENTER
        self.lbl_title.font = ('<System-Bold>', 22)
        self.add_subview(self.lbl_title)

        self.lbl_summary = ui.Label()
        self.lbl_summary.number_of_lines = 0
        self.lbl_summary.alignment = ui.ALIGN_CENTER
        self.lbl_summary.font = ('<System-Bold>', 17)
        self.lbl_summary.text_color = COLOR_TEXT
        self.add_subview(self.lbl_summary)

        self.tv = ui.TableView()
        self.tv.data_source = self
        self.tv.delegate = self
        self.add_subview(self.tv)

    def layout(self):
        margin = 12
        y = 12
        w = self.width - margin * 2

        self.lbl_title.frame = (margin, y, w, 34)
        y += 42

        self.lbl_summary.frame = (margin, y, w, 58)
        y += 66

        self.tv.frame = (margin, y, w, self.height - y - 12)

    def update_summary(self):
        total_par = sum(h.get('par', 4) for h in self.holes)
        score = 0
        putts = 0
        penalties = 0
        for hole in self.holes:
            score += hole_score(hole)
            putts += hole.get('putts', 0)
            penalties += hole_penalty_count(hole)

        diff = score - total_par
        if diff > 0:
            diff_text = '+{}'.format(diff)
        elif diff < 0:
            diff_text = str(diff)
        else:
            diff_text = '±0'

        self.lbl_summary.text = '合計スコア {}  /  Par {}  /  {}\n合計パット {}  /  合計ペナ {}'.format(
            score, total_par, diff_text, putts, penalties
        )

    def score_diff_text(self, score, par):
        diff = score - par
        if diff > 0:
            return '+{}'.format(diff)
        if diff < 0:
            return str(diff)
        return '±0'

    def tableview_number_of_rows(self, tableview, section):
        return len(self.holes)

    def tableview_cell_for_row(self, tableview, section, row):
        hole = self.holes[row]
        hole_no = hole.get('hole_no', row + 1)
        par = hole.get('par', 4)
        score = hole_score(hole)
        putts = hole.get('putts', 0)
        penalty = hole_penalty_count(hole)
        shots = sum(1 for s in hole.get('shots', []) if is_score_shot(s))

        mark = '★ ' if row == self.current_index else ''
        cell = ui.TableViewCell('subtitle')
        cell.text_label.text = '{}{}H   Par{}   スコア{}   ({})'.format(
            mark,
            hole_no,
            par,
            score,
            self.score_diff_text(score, par)
        )
        cell.text_label.font = ('<System-Bold>', 18)
        cell.detail_text_label.text = 'ショット{}   パット{}   ペナルティ{}'.format(
            shots, putts, penalty
        )
        cell.detail_text_label.font = ('<System>', 15)
        if row == self.current_index:
            cell.background_color = COLOR_SECONDARY
        return cell

    def tableview_did_select(self, tableview, section, row):
        tableview.selected_row = (-1, -1)




# =============================
# 過去ラウンド一覧画面
# =============================
class RoundHistoryView(ui.View):
    """保存済みラウンドを一覧表示し、選択したラウンドを開く・分析・削除する画面。

    これまでのアプリは「現在のラウンド」を中心に扱っていました。
    実用で使うには、過去のテスト記録や本番ラウンドを後から確認できることが重要です。
    この画面では golf_data.json に保存されている複数ラウンドを round_id で選び直します。
    """
    def __init__(self, rounds, on_open_callback=None, on_delete_callback=None, on_analysis_callback=None):
        super().__init__()
        self.name = '過去ラウンド'
        self.background_color = 'white'
        self.on_open_callback = on_open_callback
        self.on_delete_callback = on_delete_callback
        self.on_analysis_callback = on_analysis_callback

        self.rounds = []
        for round_data in rounds:
            if isinstance(round_data, dict):
                self.rounds.append(ensure_round_has_id(clone_round(round_data)))

        # 新しいラウンドほど上に表示します。
        self.rounds.sort(key=round_sort_key, reverse=True)

        self.make_ui()
        self.update_count_label()

    def make_ui(self):
        self.lbl_title = ui.Label()
        self.lbl_title.text = '保存済みラウンド一覧'
        self.lbl_title.alignment = ui.ALIGN_CENTER
        self.lbl_title.font = ('<System-Bold>', 20)
        self.add_subview(self.lbl_title)

        self.lbl_count = ui.Label()
        self.lbl_count.alignment = ui.ALIGN_CENTER
        self.lbl_count.font = ('<System>', 13)
        self.lbl_count.text_color = 'gray'
        self.add_subview(self.lbl_count)

        self.lbl_hint = ui.Label()
        self.lbl_hint.text = 'ラウンドをタップすると「開く・分析・削除」を選べます'
        self.lbl_hint.alignment = ui.ALIGN_CENTER
        self.lbl_hint.font = ('<System>', 12)
        self.lbl_hint.text_color = '#666666'
        self.add_subview(self.lbl_hint)

        self.tv = ui.TableView()
        self.tv.data_source = self
        self.tv.delegate = self
        self.add_subview(self.tv)

    def layout(self):
        margin = 12
        y = 10
        w = self.width - margin * 2

        self.lbl_title.frame = (margin, y, w, 30)
        y += 34

        self.lbl_count.frame = (margin, y, w, 24)
        y += 26

        self.lbl_hint.frame = (margin, y, w, 22)
        y += 28

        self.tv.frame = (margin, y, w, self.height - y - 10)

    def update_count_label(self):
        self.lbl_count.text = '{}件のラウンドが保存されています'.format(len(self.rounds))

    def update_list(self):
        self.rounds.sort(key=round_sort_key, reverse=True)
        self.update_count_label()
        self.tv.reload()

    def tableview_number_of_rows(self, tableview, section):
        return len(self.rounds)

    def tableview_cell_for_row(self, tableview, section, row):
        round_data = self.rounds[row]
        cell = ui.TableViewCell('subtitle')
        cell.text_label.text = round_list_title(round_data)
        cell.text_label.font = ('<System-Bold>', 15)
        cell.detail_text_label.text = round_list_detail(round_data)
        cell.detail_text_label.font = ('<System>', 12)
        cell.accessory_type = 'disclosure_indicator'
        return cell

    def tableview_did_select(self, tableview, section, row):
        if row < 0 or row >= len(self.rounds):
            return

        round_data = self.rounds[row]
        rid = get_round_id(round_data)

        choice = console.alert(
            'ラウンド操作',
            round_operation_message(round_data),
            '開く',
            '分析',
            '削除',
            hide_cancel_button=False
        )

        if choice == 1:
            if self.on_open_callback:
                self.on_open_callback(rid)
            self.close()

        elif choice == 2:
            if self.on_analysis_callback:
                self.on_analysis_callback(rid)

        elif choice == 3:
            confirm = console.alert(
                '削除確認',
                round_delete_confirm_message(round_data),
                '削除する',
                'やめる',
                hide_cancel_button=True
            )
            if confirm == 1:
                if self.on_delete_callback:
                    self.on_delete_callback(rid)
                self.rounds = [r for r in self.rounds if get_round_id(r) != rid]
                self.update_list()

        tableview.selected_row = (-1, -1)


# =============================
# ショット入力画面
# =============================
# =============================
# ショット入力画面
# =============================
#
# この画面がアプリの中心です。
# 操作の基本は、
#   1. ボール地点に立つ
#   2. 現在地確定を押す
#   3. クラブ・狙い・実際を選ぶ
#   4. ショット追加を押す
# です。
# GPSは「これから打つ地点」として保存します。

class ShotEditView(ui.View):
    def __init__(self, holes, hole_index, on_save_callback=None):
        super().__init__()
        self.name = 'ショット入力'
        self.background_color = 'white'
        self.holes = holes
        self.hole_index = hole_index
        self.on_save_callback = on_save_callback

        self.selected_club = 'Driver'
        self.selected_aim = 0
        self.selected_actual = 0
        self.edit_index = None
        self.confirmed_gps_location = None

        self.make_ui()
        self.update_current_hole_ui()

    @property
    def hole_data(self):
        return self.holes[self.hole_index]

    def make_ui(self):
        self.lbl_title = ui.Label()
        self.lbl_title.alignment = ui.ALIGN_CENTER
        self.lbl_title.font = ('<System-Bold>', 20)
        self.add_subview(self.lbl_title)

        self.btn_prev_hole = ui.Button(title='前のホール')
        self.btn_prev_hole.action = self.go_prev_hole
        style_button(self.btn_prev_hole)
        self.add_subview(self.btn_prev_hole)

        self.btn_next_hole = ui.Button(title='次のホール')
        self.btn_next_hole.action = self.go_next_hole
        style_button(self.btn_next_hole)
        self.add_subview(self.btn_next_hole)

        self.btn_club = ui.Button(title='クラブ')
        self.btn_club.action = self.pick_club
        style_button(self.btn_club)
        self.add_subview(self.btn_club)

        self.btn_aim = ui.Button(title='狙い')
        self.btn_aim.action = self.pick_aim
        style_button(self.btn_aim)
        self.add_subview(self.btn_aim)

        self.btn_actual = ui.Button(title='実際')
        self.btn_actual.action = self.pick_actual
        style_button(self.btn_actual)
        self.add_subview(self.btn_actual)

        self.sw_putt = ui.Switch()
        self.sw_putt.value = False
        self.sw_putt.action = self.on_putt_changed
        self.add_subview(self.sw_putt)

        self.lbl_putt = ui.Label()
        self.lbl_putt.text = 'これはパット'
        self.add_subview(self.lbl_putt)

        self.lbl_score = ui.Label()
        self.lbl_score.alignment = ui.ALIGN_CENTER
        self.lbl_score.font = ('<System-Bold>', 15)
        self.add_subview(self.lbl_score)

        self.sw_gps = ui.Switch()
        self.sw_gps.value = True
        self.add_subview(self.sw_gps)

        self.lbl_gps = ui.Label()
        self.lbl_gps.text = 'GPS記録'
        self.lbl_gps.font = ('<System>', 14)
        self.add_subview(self.lbl_gps)

        self.btn_gps_check = ui.Button(title='現在地確定')
        self.btn_gps_check.action = self.check_current_gps
        style_button(self.btn_gps_check)
        self.add_subview(self.btn_gps_check)

        self.lbl_gps_status = ui.Label()
        self.lbl_gps_status.text = '先に現在地確定を押す'
        self.lbl_gps_status.font = ('<System>', 12)
        self.lbl_gps_status.text_color = 'gray'
        self.add_subview(self.lbl_gps_status)

        self.btn_penalty_plus = ui.Button(title='ペナルティ +1')
        self.btn_penalty_plus.action = self.add_penalty
        style_button(self.btn_penalty_plus, kind='danger')
        self.add_subview(self.btn_penalty_plus)

        self.btn_penalty_minus = ui.Button(title='ペナルティ -1')
        self.btn_penalty_minus.action = self.remove_penalty
        style_button(self.btn_penalty_minus)
        self.add_subview(self.btn_penalty_minus)

        self.tf_memo = ui.TextField()
        self.tf_memo.placeholder = 'メモ'
        self.tf_memo.border_style = 'rounded_rect'
        self.add_subview(self.tf_memo)

        self.btn_add = ui.Button(title='ショット追加')
        self.btn_add.action = self.add_or_update_shot
        style_button(self.btn_add, kind='primary')
        self.add_subview(self.btn_add)

        self.btn_cancel_edit = ui.Button(title='編集取消')
        self.btn_cancel_edit.action = self.cancel_edit
        style_button(self.btn_cancel_edit)
        self.add_subview(self.btn_cancel_edit)

        self.btn_scoreboard = ui.Button(title='18ホール スコア表示')
        self.btn_scoreboard.action = self.open_scoreboard
        style_button(self.btn_scoreboard)
        self.add_subview(self.btn_scoreboard)

        self.btn_hole_map = ui.Button(title='このホールを地図表示')
        self.btn_hole_map.action = self.open_hole_map
        style_button(self.btn_hole_map, kind='primary')
        self.add_subview(self.btn_hole_map)

        self.btn_green_on = ui.Button(title='グリーンオン記録')
        self.btn_green_on.action = self.add_green_on_point
        style_button(self.btn_green_on)
        self.add_subview(self.btn_green_on)

        self.tv = ui.TableView()
        self.tv.data_source = self
        self.tv.delegate = self
        self.add_subview(self.tv)

    def layout(self):
        margin = 12
        gap = 8
        y = 10
        w = self.width - margin * 2

        self.lbl_title.frame = (margin, y, w, 30)
        y += 40

        nav_w = (w - gap) / 2
        self.btn_prev_hole.frame = (margin, y, nav_w, 36)
        self.btn_next_hole.frame = (margin + nav_w + gap, y, nav_w, 36)
        y += 44

        three_w = (w - gap * 2) / 3
        self.btn_club.frame = (margin, y, three_w, 40)
        self.btn_aim.frame = (margin + three_w + gap, y, three_w, 40)
        self.btn_actual.frame = (margin + (three_w + gap) * 2, y, three_w, 40)
        y += 48

        self.sw_putt.frame = (margin, y, 60, 36)
        self.lbl_putt.frame = (margin + 70, y, 100, 36)
        self.lbl_score.frame = (margin + 170, y, w - 170, 36)
        y += 44

        self.sw_gps.frame = (margin, y, 60, 36)
        self.lbl_gps.frame = (margin + 70, y, 90, 36)
        self.btn_gps_check.frame = (margin + 160, y, 110, 36)
        self.lbl_gps_status.frame = (margin + 278, y, w - 278, 36)
        y += 44

        btn_w = (w - gap) / 2
        self.btn_penalty_plus.frame = (margin, y, btn_w, 36)
        self.btn_penalty_minus.frame = (margin + btn_w + gap, y, btn_w, 36)
        y += 44

        self.tf_memo.frame = (margin, y, w, 36)
        y += 44

        self.btn_add.frame = (margin, y, btn_w, 36)
        self.btn_cancel_edit.frame = (margin + btn_w + gap, y, btn_w, 36)
        y += 44

        self.btn_scoreboard.frame = (margin, y, btn_w, 40)
        self.btn_hole_map.frame = (margin + btn_w + gap, y, btn_w, 40)
        y += 48

        self.btn_green_on.frame = (margin, y, w, 38)
        y += 46

        self.tv.frame = (margin, y, w, self.height - y - 10)

    def update_nav_buttons(self):
        self.btn_prev_hole.enabled = (self.hole_index > 0)
        self.btn_next_hole.enabled = (self.hole_index < len(self.holes) - 1)

    def update_current_hole_ui(self):
        self.lbl_title.text = '{}H ショット入力  (Par{})'.format(
            self.hole_data.get('hole_no', 0),
            self.hole_data.get('par', 4)
        )
        self.update_nav_buttons()
        self.update_list()
        self.update_score_label()
        self.reset_input_fields()

    def update_add_button_title(self):
        if self.edit_index is None:
            self.btn_add.title = 'ショット追加'
            self.btn_cancel_edit.enabled = False
        else:
            self.btn_add.title = 'ショット更新'
            self.btn_cancel_edit.enabled = True

    def reset_input_fields(self):
        self.selected_club = 'Driver'
        self.selected_aim = 0
        self.selected_actual = 0
        self.sw_putt.value = False
        self.sw_gps.value = True
        self.confirmed_gps_location = None
        self.lbl_gps_status.text = '先に現在地確定を押す'
        self.tf_memo.text = ''
        self.edit_index = None

        self.btn_club.title = 'クラブ / {}'.format(self.selected_club)
        self.btn_aim.title = '狙い / {}'.format(aim_label(self.selected_aim))
        self.btn_actual.title = '実際 / {}'.format(actual_label(self.selected_actual))
        self.update_add_button_title()

    def load_shot_to_inputs(self, shot, index):
        self.edit_index = index

        # 編集開始時は、直前に別地点で確定したGPSを必ず破棄する。
        # GPSを更新したい場合は、編集画面でGPS記録をONにしてから
        # あらためて「現在地確定」を押す運用にする。
        self.confirmed_gps_location = None

        self.selected_club = shot.get('club', 'Driver')
        self.selected_aim = shot.get('aim', 0)
        self.selected_actual = shot.get('actual', 0)
        self.sw_putt.value = bool(shot.get('is_putt', False))
        self.sw_gps.value = False
        self.lbl_gps_status.text = '編集中: GPSを更新する場合だけON'
        self.tf_memo.text = shot.get('memo', '')

        self.btn_club.title = 'クラブ / {}'.format(self.selected_club)
        self.btn_aim.title = '狙い / {}'.format(aim_label(self.selected_aim))
        self.btn_actual.title = '実際 / {}'.format(actual_label(self.selected_actual))
        self.update_add_button_title()

    def cancel_edit(self, sender):
        self.reset_input_fields()

    def go_prev_hole(self, sender):
        if self.hole_index > 0:
            self.hole_index -= 1
            self.update_current_hole_ui()
            if self.on_save_callback:
                self.on_save_callback(last_hole_index=self.hole_index)

    def go_next_hole(self, sender):
        if self.hole_index < len(self.holes) - 1:
            self.hole_index += 1
            self.update_current_hole_ui()
            if self.on_save_callback:
                self.on_save_callback(last_hole_index=self.hole_index)

    def pick_club(self, sender):
        selected = dialogs.list_dialog('クラブ選択', CLUB_LIST)
        if selected:
            self.selected_club = selected
            self.btn_club.title = 'クラブ / {}'.format(self.selected_club)

    def pick_aim(self, sender):
        labels = [x[0] for x in AIM_OPTIONS]
        selected = dialogs.list_dialog('狙い方向', labels)
        if selected:
            for label, val in AIM_OPTIONS:
                if label == selected:
                    self.selected_aim = val
                    break
            self.btn_aim.title = '狙い / {}'.format(aim_label(self.selected_aim))

    def pick_actual(self, sender):
        labels = [x[0] for x in ACTUAL_OPTIONS]
        selected = dialogs.list_dialog('実際の方向', labels)
        if selected:
            for label, val in ACTUAL_OPTIONS:
                if label == selected:
                    self.selected_actual = val
                    break
            self.btn_actual.title = '実際 / {}'.format(actual_label(self.selected_actual))

    def on_putt_changed(self, sender):
        """パットON/OFFとGPS記録を連動させる。

        パットは数m単位の記録になるため、iPhoneのGPS誤差の方が大きくなりやすいです。
        そのため、パットONではGPSを自動でOFFにします。

        逆にパットOFFに戻した場合は通常ショットとして扱うため、GPSをONに戻します。
        確定済みGPSは古い地点を誤って使わないようにクリアします。
        """
        if self.sw_putt.value:
            # パットON: クラブはPutter、GPSはOFF。
            self.selected_club = 'Putter'
            self.btn_club.title = 'クラブ / {}'.format(self.selected_club)

            self.sw_gps.value = False
            self.confirmed_gps_location = None
            self.lbl_gps_status.text = 'パットのためGPS記録OFF'
        else:
            # パットOFF: 通常ショットに戻す。
            # Putterのまま通常ショット扱いになると混乱しやすいのでDriverへ戻します。
            if self.selected_club == 'Putter':
                self.selected_club = 'Driver'
                self.btn_club.title = 'クラブ / {}'.format(self.selected_club)

            self.sw_gps.value = True
            self.confirmed_gps_location = None
            self.lbl_gps_status.text = '先に現在地確定を押す'

    def check_current_gps(self, sender):
        """次に追加・更新するショットに使うGPS位置を、先に確定する。"""
        if not bool(self.sw_gps.value):
            self.lbl_gps_status.text = 'GPS記録OFF'
            console.hud_alert('GPS記録がOFFです', 'error', 1.0)
            return

        self.confirmed_gps_location = None
        self.lbl_gps_status.text = '地点確定中...'
        loc = get_stable_gps_location()
        if not loc:
            self.lbl_gps_status.text = 'GPS取得失敗'
            console.hud_alert('GPSを取得できませんでした', 'error', 1.2)
            return

        self.confirmed_gps_location = loc
        acc = loc.get('accuracy')
        sample_count = loc.get('sample_count')
        elapsed = safe_float(loc.get('gps_elapsed_sec'))

        parts = ['地点確定済み']
        if acc is not None:
            parts.append('約{:.0f}m'.format(acc))
        if sample_count:
            parts.append('取得{}回'.format(sample_count))
        if elapsed is not None:
            parts.append('{:.1f}秒'.format(elapsed))
        stage = str(loc.get('gps_stage') or '').strip()
        if stage:
            parts.append('2段階')
        stability_distance = safe_float(loc.get('gps_stability_distance_m'))
        if stability_distance is not None:
            parts.append('差{:.1f}m'.format(stability_distance))
        stability_status = str(loc.get('gps_stability_status') or '').strip()
        if stability_status == '安定':
            parts.append('安定')
        elif stability_status:
            parts.append('2回目採用')
        self.lbl_gps_status.text = ' / '.join(parts)
        console.hud_alert('次ショット地点を確定しました', 'success', 1.0)

    def update_score_label(self):
        score = hole_score(self.hole_data)
        putts = self.hole_data.get('putts', 0)
        penalty = hole_penalty_count(self.hole_data)
        par = self.hole_data.get('par', 4)

        self.lbl_score.text = 'S{} / P{} / Pt{} / Pe{}'.format(
            score, par, putts, penalty
        )

    def open_scoreboard(self, sender):
        v = ScoreboardView(self.holes, current_index=self.hole_index)
        v.present('fullscreen')

    def open_hole_map(self, sender):
        points = collect_hole_map_points(self.hole_data)
        if not points:
            console.hud_alert('GPS付きショットがありません', 'error', 1.2)
            return

        hole_no = self.hole_data.get('hole_no', self.hole_index + 1)
        title = '{}H ショットマップ'.format(hole_no)
        groups = [{'name': '{}H'.format(hole_no), 'points': points}]
        if open_shot_map_view(title, groups):
            console.hud_alert('地図を開きました', 'success', 1.0)
        else:
            console.hud_alert('地図を開けませんでした', 'error', 1.2)

    def add_penalty(self, sender):
        penalty = hole_penalty_count(self.hole_data)
        self.hole_data['penalty_count'] = penalty + 1

        if self.on_save_callback:
            self.on_save_callback(last_hole_index=self.hole_index)

        self.update_score_label()
        self.update_list()
        console.hud_alert('ペナルティを +1 しました', 'success', 1.0)

    def remove_penalty(self, sender):
        penalty = hole_penalty_count(self.hole_data)
        if penalty <= 0:
            console.hud_alert('ペナルティは 0 です', 'error', 1.0)
            return

        self.hole_data['penalty_count'] = penalty - 1

        if self.on_save_callback:
            self.on_save_callback(last_hole_index=self.hole_index)

        self.update_score_label()
        self.update_list()
        console.hud_alert('ペナルティを -1 しました', 'success', 1.0)

    def make_green_on_marker(self):
        """グリーンオン地点を、スコアに数えない補助点として作る。"""
        marker = {
            'shot_no': '',
            'is_marker': True,
            'marker_type': MARKER_TYPE_GREEN_ON,
            'is_putt': False,
            'club': 'GreenOn',
            'aim': 0,
            'actual': 0,
            'latitude': None,
            'longitude': None,
            'gps_accuracy': None,
            'gps_timestamp': '',
            'gps_confirmed_at': '',
            'gps_raw_timestamp': None,
            'gps_age_sec': None,
            'gps_sample_count': None,
            'gps_elapsed_sec': None,
            'gps_selection_reason': '',
            'gps_stage': '',
            'gps_stability_status': '',
            'gps_stability_distance_m': None,
            'gps_first_accuracy': None,
            'gps_second_accuracy': None,
            'elapsed_from_prev_gps_sec': None,
            'distance_from_prev_yard': None,
            'gps_warning': '',
            'distance_to_next_yard': None,
            'memo': 'グリーンオン'
        }
        return marker

    def set_putt_mode_after_green_on(self):
        """グリーンオン後は、そのままパット入力へ移りやすい状態にする。"""
        self.selected_club = 'Putter'
        self.selected_aim = 0
        self.selected_actual = 0
        self.sw_putt.value = True
        self.sw_gps.value = False
        self.confirmed_gps_location = None
        self.tf_memo.text = ''
        self.edit_index = None
        self.btn_club.title = 'クラブ / {}'.format(self.selected_club)
        self.btn_aim.title = '狙い / {}'.format(aim_label(self.selected_aim))
        self.btn_actual.title = '実際 / {}'.format(actual_label(self.selected_actual))
        self.lbl_gps_status.text = 'グリーンオン保存。パットはGPS記録OFF'
        self.update_add_button_title()

    def add_green_on_point(self, sender):
        """グリーンオン地点を記録する。

        パットをGPS OFFで記録すると、最後のアプローチショットの「次地点」がなくなり、
        飛距離が計算できなくなります。そこで、グリーンに乗ったボール位置だけを
        GPS付きの補助点として保存します。この点はスコアには数えません。

        GPS妥当性チェックは通常ショットと同じ処理を行います。
        怪しいGPSの場合は保存前に確認ダイアログを出し、再取得を促します。
        """
        loc = self.confirmed_gps_location
        if not loc:
            self.lbl_gps_status.text = '地点未確定'
            console.alert(
                'グリーンオン記録',
                '先にグリーン上のボール位置で「現在地確定」を押してください。\n\nその後、この「グリーンオン記録」を押すと、最後の通常ショットの飛距離計算に使えます。',
                'OK',
                hide_cancel_button=True
            )
            return

        marker = self.make_green_on_marker()
        marker.update(gps_fields_from_location(loc))

        shots = list(self.hole_data.get('shots', []))

        # 同じホールで既にグリーンオン地点がある場合は、重複登録を避けるため確認します。
        existing_index = None
        for i, shot in enumerate(shots):
            if isinstance(shot, dict) and is_green_on_marker(shot):
                existing_index = i
                break

        # --- 通常ショットと同じGPS妥当性チェック ---
        # 仮の edit_index を決める（置き換えなら既存インデックス、新規なら None）。
        check_edit_index = existing_index  # None なら末尾追加として判定される

        warning = gps_warning_for_candidate_shot(
            self.hole_data,
            shots,
            marker,
            edit_index=check_edit_index
        )

        if warning:
            choice = console.alert(
                'GPS確認',
                warning + '\n\nこのグリーンオン地点はまだ保存していません。\nGPSを取り直す場合は「再取得」を選んでください。\n短いアプローチなど理由がある場合は、このまま保存できます。',
                '再取得',
                'このまま保存',
                hide_cancel_button=True
            )
            if choice == 1:
                self.confirmed_gps_location = None
                self.lbl_gps_status.text = 'GPS要再取得'
                return

        # GPS精度をラベルに反映する（通常ショットと同じ処理）。
        acc = loc.get('accuracy')
        if acc is None:
            self.lbl_gps_status.text = '確定地点を保存'
        else:
            self.lbl_gps_status.text = '確定地点を保存 / 約{:.0f}m'.format(acc)

        if existing_index is not None:
            shots[existing_index] = marker
        else:
            shots.append(marker)

        self.hole_data['shots'] = shots
        reorder_shot_numbers(self.hole_data)

        if self.on_save_callback:
            self.on_save_callback(last_hole_index=self.hole_index)

        self.update_list()
        self.update_score_label()
        self.set_putt_mode_after_green_on()
        console.hud_alert('グリーンオン地点を保存しました', 'success', 1.0)

    def add_or_update_shot(self, sender):
        is_putt = bool(self.sw_putt.value)

        club_name = self.selected_club
        if is_putt:
            club_name = 'Putter'

        shots = list(self.hole_data.get('shots', []))

        if self.edit_index is None:
            shot = {
                'shot_no': 0,
                'is_marker': False,
                'marker_type': '',
                'is_putt': is_putt,
                'club': club_name,
                'aim': self.selected_aim,
                'actual': self.selected_actual,
                'latitude': None,
                'longitude': None,
                'gps_accuracy': None,
                'gps_timestamp': '',
                'gps_confirmed_at': '',
                'gps_raw_timestamp': None,
                'gps_age_sec': None,
                'gps_sample_count': None,
                'gps_elapsed_sec': None,
                'gps_selection_reason': '',
                'gps_stage': '',
                'gps_stability_status': '',
                'gps_stability_distance_m': None,
                'gps_first_accuracy': None,
                'gps_second_accuracy': None,
                'elapsed_from_prev_gps_sec': None,
                'distance_from_prev_yard': None,
                'gps_warning': '',
                'distance_to_next_yard': None,
                'memo': self.tf_memo.text.strip()
            }
        else:
            if 0 <= self.edit_index < len(shots):
                shot = copy.deepcopy(shots[self.edit_index])
            else:
                return

            shot['is_marker'] = False
            shot['marker_type'] = ''
            shot['is_putt'] = is_putt
            shot['club'] = club_name
            shot['aim'] = self.selected_aim
            shot['actual'] = self.selected_actual
            shot['memo'] = self.tf_memo.text.strip()

        if bool(self.sw_gps.value):
            loc = self.confirmed_gps_location
            if not loc:
                self.lbl_gps_status.text = '地点未確定'
                console.hud_alert('先に「現在地確定」を押してください', 'error', 1.4)
                return

            shot.update(gps_fields_from_location(loc))
            acc = loc.get('accuracy')
            if acc is None:
                self.lbl_gps_status.text = '確定地点を保存'
            else:
                self.lbl_gps_status.text = '確定地点を保存 / 約{:.0f}m'.format(acc)

        # 重要:
        # ここで、実際にショットを保存する前にGPSの妥当性を確認します。
        # 以前は保存後に警告していたため、修正するには一度ショットを削除する必要がありました。
        # この版では、怪しいGPSなら保存せず、再度「現在地確定」を促します。
        if bool(self.sw_gps.value):
            warning = gps_warning_for_candidate_shot(
                self.hole_data,
                shots,
                shot,
                edit_index=self.edit_index
            )

            if warning:
                choice = console.alert(
                    'GPS確認',
                    warning + '\n\nこのショットはまだ保存していません。\nGPSを取り直す場合は「再取得」を選んでください。\n短いショットなど理由がある場合は、このまま保存できます。',
                    '再取得',
                    'このまま保存',
                    hide_cancel_button=True
                )
                if choice == 1:
                    self.confirmed_gps_location = None
                    self.lbl_gps_status.text = 'GPS要再取得'
                    return

        if self.edit_index is None:
            shots.append(shot)
        else:
            shots[self.edit_index] = shot

        self.hole_data['shots'] = shots
        reorder_shot_numbers(self.hole_data)

        if self.on_save_callback:
            self.on_save_callback(last_hole_index=self.hole_index)

        self.update_list()
        self.update_score_label()
        self.reset_input_fields()

    def delete_shot_at_row(self, row):
        shots = list(self.hole_data.get('shots', []))
        if row < 0 or row >= len(shots):
            return

        del shots[row]
        self.hole_data['shots'] = shots
        reorder_shot_numbers(self.hole_data)

        if self.on_save_callback:
            self.on_save_callback(last_hole_index=self.hole_index)

        self.update_list()
        self.update_score_label()
        self.reset_input_fields()
        console.hud_alert('ショットを削除しました', 'success', 1.0)

    def update_list(self):
        self.tv.reload()

    def tableview_number_of_rows(self, tableview, section):
        return len(self.hole_data.get('shots', []))

    def tableview_cell_for_row(self, tableview, section, row):
        shots = self.hole_data.get('shots', [])
        shot = shots[row]

        cell = ui.TableViewCell('subtitle')
        if is_green_on_marker(shot):
            cell.text_label.text = 'グリーンオン: {}'.format(shot_result_text(shot))
        else:
            cell.text_label.text = '{}打目: {}'.format(
                shot.get('shot_no', 0),
                shot_result_text(shot)
            )
        detail_parts = []
        d_text = distance_text(shot)
        if d_text:
            detail_parts.append(d_text)
        e_text = elapsed_text(shot)
        if e_text:
            detail_parts.append(e_text)
        detail_parts.append(gps_status_text(shot))
        w_text = gps_warning_text(shot)
        if w_text:
            detail_parts.append('注意: {}'.format(w_text))

        memo = shot.get('memo', '').strip()
        if memo:
            detail_parts.append('メモ: {}'.format(memo))

        cell.detail_text_label.text = ' / '.join(detail_parts)
        cell.accessory_type = 'disclosure_indicator'
        return cell

    def tableview_did_select(self, tableview, section, row):
        shots = self.hole_data.get('shots', [])
        if row < 0 or row >= len(shots):
            return

        shot = shots[row]
        if is_green_on_marker(shot):
            message = 'グリーンオン地点\n{}'.format(shot_result_text(shot))
        else:
            message = '{}打目\n{}'.format(
                shot.get('shot_no', 0),
                shot_result_text(shot)
            )

        d_text = distance_text(shot)
        if d_text:
            message += '\n{}'.format(d_text)
        e_text = elapsed_text(shot)
        if e_text:
            message += '\n{}'.format(e_text)
        message += '\n{}'.format(gps_status_text(shot))
        w_text = gps_warning_text(shot)
        if w_text:
            message += '\n注意: {}'.format(w_text)

        memo = shot.get('memo', '').strip()
        if memo:
            message += '\nメモ: {}'.format(memo)

        if is_marker_item(shot):
            choice = console.alert(
                '補助地点操作',
                message,
                '削除',
                '閉じる',
                hide_cancel_button=True
            )
            if choice == 1:
                self.delete_shot_at_row(row)
        else:
            choice = console.alert(
                'ショット操作',
                message,
                '編集',
                '削除',
                '閉じる',
                hide_cancel_button=True
            )

            if choice == 1:
                self.load_shot_to_inputs(shot, row)
            elif choice == 2:
                self.delete_shot_at_row(row)

        tableview.selected_row = (-1, -1)


# =============================
# メイン画面
# =============================
# =============================
# メイン画面
# =============================
#
# 起動時に保存済みラウンドを読み込み、active_round_idから前回のラウンドを復元します。
# 日付ではなくround_idを使うことで、同じ日に複数ラウンドを作っても混ざりにくくしています。

class GolfDirectionApp(TappableView):
    def __init__(self):
        super().__init__()
        self.name = 'ゴルフ方向分析'
        self.background_color = 'white'

        self.rounds = load_rounds()
        self.rounds = [ensure_round_has_id(r) for r in self.rounds]
        self.courses = load_courses()

        # 重要:
        # 以前は「今日の日付の最新ラウンド」を使っていました。
        # この版では、まず active_round_id でラウンドを特定します。
        active_round_id = load_active_round_id()
        self.current_round_index = find_round_index_by_id(self.rounds, active_round_id)

        # active_round_id が見つからない場合だけ、今日の最新ラウンド、さらに全体の最新ラウンドへフォールバックします。
        if self.current_round_index is None:
            self.current_round_index = find_latest_today_round_index(self.rounds)
        if self.current_round_index is None:
            self.current_round_index = find_latest_round_index(self.rounds)

        if self.current_round_index is not None:
            self.current_round = clone_round(self.rounds[self.current_round_index])
            self.current_round = ensure_round_has_id(self.current_round)
        else:
            self.current_round = make_empty_round()
            self.current_round_index = None

        ui_state = self.current_round.get('ui_state', {})
        self.hole_page = ui_state.get('hole_page', 0)
        self.last_hole_index = ui_state.get('last_hole_index', 0)

        if self.hole_page not in (0, 1):
            self.hole_page = 0
        if self.last_hole_index < 0:
            self.last_hole_index = 0
        if self.last_hole_index > 17:
            self.last_hole_index = 17

        # 旧データの round_id 補完を保存に反映します。
        self.autosave_current_round(initial_save=True)

        self.make_ui()
        self.refresh_round_info()
        self.update_hole_list()

    def make_ui(self):
        self.sv = ui.ScrollView(frame=self.bounds, flex='WH')
        self.add_subview(self.sv)

        self.content = ui.View(frame=self.bounds)
        self.sv.add_subview(self.content)

        self.btn_new = ui.Button(title='新規ラウンド')
        self.btn_new.action = self.new_round
        style_button(self.btn_new, kind='primary')
        self.content.add_subview(self.btn_new)

        self.btn_resume = ui.Button(title='続きから')
        self.btn_resume.action = self.resume_last_hole
        style_button(self.btn_resume, kind='primary')
        self.content.add_subview(self.btn_resume)

        self.btn_setting = ui.Button(title='パー設定')
        self.btn_setting.action = self.open_par_setting
        style_button(self.btn_setting)
        self.content.add_subview(self.btn_setting)

        self.btn_course_manage = ui.Button(title='ゴルフ場管理')
        self.btn_course_manage.action = self.open_course_manager
        style_button(self.btn_course_manage)
        self.content.add_subview(self.btn_course_manage)

        self.btn_analysis = ui.Button(title='分析')
        self.btn_analysis.action = self.show_analysis
        style_button(self.btn_analysis)
        self.content.add_subview(self.btn_analysis)

        self.btn_round_map = ui.Button(title='ラウンド地図表示')
        self.btn_round_map.action = self.open_round_map
        style_button(self.btn_round_map, kind='primary')
        self.content.add_subview(self.btn_round_map)

        self.btn_history = ui.Button(title='過去ラウンド')
        self.btn_history.action = self.open_round_history
        style_button(self.btn_history)
        self.content.add_subview(self.btn_history)

        self.lbl_mode = ui.Label()
        self.lbl_mode.font = ('<System>', 14)
        self.lbl_mode.text_color = 'blue'
        self.content.add_subview(self.lbl_mode)

        self.lbl_info = ui.Label()
        self.lbl_info.number_of_lines = 0
        self.lbl_info.font = ('<System>', 14)
        self.content.add_subview(self.lbl_info)

        self.path_label = ui.Label()
        self.path_label.number_of_lines = 2
        self.path_label.font = ('<System>', 11)
        self.path_label.text_color = 'gray'
        self.path_label.text = app_storage.app_folder_display_text()
        self.content.add_subview(self.path_label)

        self.btn_front9 = ui.Button(title='前半 1〜9H')
        self.btn_front9.action = self.show_front9
        style_button(self.btn_front9)
        self.btn_front9.hidden = True
        self.content.add_subview(self.btn_front9)

        self.btn_back9 = ui.Button(title='後半 10〜18H')
        self.btn_back9.action = self.show_back9
        style_button(self.btn_back9)
        self.btn_back9.hidden = True
        self.content.add_subview(self.btn_back9)

        self.lbl_holes = ui.Label()
        self.lbl_holes.font = ('<System-Bold>', 16)
        self.content.add_subview(self.lbl_holes)

        self.hole_ds = ui.ListDataSource([])
        self.hole_ds.action = self.select_hole

        self.tv_holes = ui.TableView()
        self.tv_holes.data_source = self.hole_ds
        self.tv_holes.delegate = self.hole_ds
        self.content.add_subview(self.tv_holes)

    def layout(self):
        self.sv.frame = self.bounds

        margin = 12
        gap = 8
        y = 10
        w = self.width - margin * 2

        half_w = (w - gap) / 2

        self.btn_new.frame = (margin, y, half_w, 36)
        self.btn_resume.frame = (margin + half_w + gap, y, half_w, 36)
        y += 44

        self.btn_setting.frame = (margin, y, half_w, 36)
        self.btn_course_manage.frame = (margin + half_w + gap, y, half_w, 36)
        y += 44

        self.btn_analysis.frame = (margin, y, half_w, 36)
        self.btn_round_map.frame = (margin + half_w + gap, y, half_w, 36)
        y += 44

        self.btn_history.frame = (margin, y, w, 36)
        y += 44

        self.lbl_mode.frame = (margin, y, w, 24)
        y += 28

        self.lbl_info.frame = (margin, y, w, 146)
        y += 152

        self.path_label.frame = (margin, y, w, 28)
        y += 34

        self.btn_front9.frame = (margin, y, half_w, 0)
        self.btn_back9.frame = (margin + half_w + gap, y, half_w, 0)

        self.lbl_holes.frame = (margin, y, w, 24)
        y += 28

        holes_h = 18 * 44
        self.tv_holes.frame = (margin, y, w, holes_h)
        y += holes_h + 20

        content_h = max(self.height + 1, y)
        self.content.frame = (0, 0, self.width, content_h)
        self.sv.content_size = (self.width, content_h)

    def update_mode_label(self):
        self.lbl_mode.text = 'モード: round_id で継続中'

    def update_half_buttons(self):
        self.lbl_holes.text = '18ホール スコア一覧'

    def refresh_round_info(self):
        # 重要:
        # ここで date を today_str() に上書きしません。
        # ラウンドを作った日の記録として保持します。
        if not self.current_round.get('date'):
            self.current_round['date'] = today_str()

        total_par = sum(h.get('par', 4) for h in self.current_round.get('holes', []))
        total_putts = sum(h.get('putts', 0) for h in self.current_round.get('holes', []))
        total_shots = total_shot_count(self.current_round)
        total_penalties = total_penalty_count(self.current_round)
        score_total = total_score(self.current_round)

        last_hole_no = self.last_hole_index + 1
        course_name = self.current_round.get('course_name', '').strip()
        if not course_name:
            course_name = '未設定'

        self.lbl_info.text = (
            '日付: {}\n'
            'ゴルフ場: {}\n'
            'round_id: {}\n'
            '合計パー: {}\n'
            '合計スコア: {}   合計ペナ: {}\n'
            '合計ショット: {}   合計パット: {}\n'
            '前回位置: {}H'
        ).format(
            self.current_round.get('date', ''),
            course_name,
            round_id_short(self.current_round),
            total_par,
            score_total,
            total_penalties,
            total_shots,
            total_putts,
            last_hole_no
        )
        self.update_mode_label()

    def update_hole_list(self):
        holes = self.current_round.get('holes', [])
        visible_holes = holes[:18]

        items = []
        for hole in visible_holes:
            hole_no = hole.get('hole_no', 0)
            par = hole.get('par', 4)
            putts = hole.get('putts', 0)
            penalty = hole_penalty_count(hole)
            score = hole_score(hole)

            marker = ''
            if (hole_no - 1) == self.last_hole_index:
                marker = '★ '

            items.append(
                '{}{}H  Par{}  スコア:{}  パット:{}  ペナ:{}'.format(
                    marker, hole_no, par, score, putts, penalty
                )
            )

        self.hole_ds.items = items
        self.tv_holes.reload()
        self.update_half_buttons()
        self.refresh_round_info()
        self.layout()

    def apply_fields_to_current_round(self):
        if not self.current_round.get('date'):
            self.current_round['date'] = today_str()

        self.current_round['updated_at'] = now_iso()
        self.current_round['ui_state'] = {
            'hole_page': getattr(self, 'hole_page', 0),
            'last_hole_index': getattr(self, 'last_hole_index', 0),
        }
        self.current_round = ensure_round_has_id(self.current_round)

    def sync_current_round_state(self):
        self.apply_fields_to_current_round()

        rid = get_round_id(self.current_round)
        index = find_round_index_by_id(self.rounds, rid)

        if index is None:
            self.rounds.append(clone_round(self.current_round))
            self.current_round_index = len(self.rounds) - 1
        else:
            self.rounds[index] = clone_round(self.current_round)
            self.current_round_index = index

        save_active_round_id(rid)

    def autosave_current_round(self, initial_save=False):
        self.sync_current_round_state()
        save_rounds(self.rounds)

        if not initial_save:
            self.refresh_round_info()

    def update_courses(self, courses):
        self.courses = copy.deepcopy(courses)
        save_courses(self.courses)

    def new_round(self, sender):
        choice = console.alert(
            '新規ラウンド',
            '新しい round_id のラウンドを開始します。\n現在のラウンドは保存されたまま残ります。',
            '開始する',
            'やめる',
            hide_cancel_button=True
        )
        if choice != 1:
            return

        self.current_round = make_empty_round()
        self.current_round_index = None
        self.hole_page = 0
        self.last_hole_index = 0
        self.autosave_current_round()
        self.update_hole_list()
        console.hud_alert('新しいラウンドを開始しました', 'success', 1.0)

    def resume_last_hole(self, sender):
        actual_index = self.last_hole_index
        if actual_index < 0:
            actual_index = 0
        if actual_index > 17:
            actual_index = 17

        self.last_hole_index = actual_index
        self.hole_page = 0 if actual_index <= 8 else 1
        self.autosave_current_round()
        self.update_hole_list()

        v = ShotEditView(
            self.current_round['holes'],
            actual_index,
            on_save_callback=self.on_child_saved
        )
        v.present('fullscreen')


    def open_round_history(self, sender):
        """過去ラウンド一覧を開く。

        一覧を開く前に現在ラウンドを保存しておくことで、
        直前の入力内容が一覧側にも反映されます。
        """
        self.autosave_current_round()

        v = RoundHistoryView(
            self.rounds,
            on_open_callback=self.open_saved_round_by_id,
            on_delete_callback=self.delete_saved_round_by_id,
            on_analysis_callback=self.show_analysis_by_round_id
        )
        v.present('fullscreen')

    def load_current_round_ui_state(self):
        """current_round に保存されている前回位置を画面状態へ反映する。"""
        ui_state = self.current_round.get('ui_state', {})
        self.hole_page = ui_state.get('hole_page', 0)
        self.last_hole_index = ui_state.get('last_hole_index', 0)

        if self.hole_page not in (0, 1):
            self.hole_page = 0
        if self.last_hole_index < 0:
            self.last_hole_index = 0
        if self.last_hole_index > 17:
            self.last_hole_index = 17

    def open_saved_round_by_id(self, round_id):
        """過去ラウンド一覧で選んだラウンドを、現在ラウンドとして開く。"""
        if not round_id:
            return

        # まず今開いているラウンドを保存してから切り替えます。
        self.autosave_current_round()

        index = find_round_index_by_id(self.rounds, round_id)
        if index is None:
            console.hud_alert('ラウンドが見つかりません', 'error', 1.2)
            return

        self.current_round_index = index
        self.current_round = ensure_round_has_id(clone_round(self.rounds[index]))
        self.load_current_round_ui_state()

        save_active_round_id(get_round_id(self.current_round))
        self.update_hole_list()
        console.hud_alert('選択したラウンドを開きました', 'success', 1.0)

    def delete_saved_round_by_id(self, round_id):
        """保存済みラウンドを削除する。

        現在開いているラウンドを削除した場合は、残っている最新ラウンドを開きます。
        残りがなければ新しい空ラウンドを作ります。
        """
        index = find_round_index_by_id(self.rounds, round_id)
        if index is None:
            console.hud_alert('削除対象が見つかりません', 'error', 1.2)
            return

        current_id = get_round_id(self.current_round)
        del self.rounds[index]

        if current_id == round_id:
            latest_index = find_latest_round_index(self.rounds)
            if latest_index is not None:
                self.current_round_index = latest_index
                self.current_round = ensure_round_has_id(clone_round(self.rounds[latest_index]))
                self.load_current_round_ui_state()
                save_active_round_id(get_round_id(self.current_round))
            else:
                self.current_round = make_empty_round()
                self.current_round_index = None
                self.hole_page = 0
                self.last_hole_index = 0
                self.sync_current_round_state()

        save_rounds(self.rounds)
        self.update_hole_list()
        console.hud_alert('ラウンドを削除しました', 'success', 1.0)

    def show_analysis_by_round_id(self, round_id):
        """過去ラウンド一覧から分析だけ確認する。"""
        index = find_round_index_by_id(self.rounds, round_id)
        if index is None:
            console.hud_alert('ラウンドが見つかりません', 'error', 1.2)
            return

        round_data = ensure_round_has_id(clone_round(self.rounds[index]))
        text = build_analysis_text(round_data)

        v = ui.View(background_color='white')
        v.name = '分析結果'

        tv = ui.TextView(frame=v.bounds, flex='WH')
        tv.editable = False
        tv.font = ('<System>', 16)
        tv.text = text
        v.add_subview(tv)

        v.present('fullscreen')

    def open_par_setting(self, sender):
        v = ParSettingView(
            self.current_round,
            self.courses,
            on_save_callback=self.on_child_saved
        )
        v.present('fullscreen')

    def open_course_manager(self, sender):
        v = CourseManagerView(
            self.courses,
            on_save_callback=self.on_courses_changed
        )
        v.present('fullscreen')

    def on_courses_changed(self, courses):
        self.update_courses(courses)
        console.hud_alert('ゴルフ場マスターを更新しました', 'success', 1.0)

    def show_front9(self, sender):
        self.hole_page = 0
        self.autosave_current_round()
        self.update_hole_list()

    def show_back9(self, sender):
        self.hole_page = 1
        self.autosave_current_round()
        self.update_hole_list()

    def select_hole(self, sender):
        row = sender.selected_row
        if row < 0:
            return

        actual_index = row
        self.last_hole_index = actual_index
        self.autosave_current_round()

        v = ShotEditView(
            self.current_round['holes'],
            actual_index,
            on_save_callback=self.on_child_saved
        )
        v.present('fullscreen')

    def on_child_saved(self, last_hole_index=None):
        if last_hole_index is not None:
            self.last_hole_index = last_hole_index
            self.hole_page = 0 if self.last_hole_index <= 8 else 1

        self.autosave_current_round()
        self.update_hole_list()

    def open_round_map(self, sender):
        self.apply_fields_to_current_round()
        groups = collect_round_map_groups(self.current_round)
        if not groups:
            console.hud_alert('GPS付きショットがありません', 'error', 1.2)
            return

        title = '{} {} ラウンドショットマップ'.format(
            self.current_round.get('date', today_str()),
            self.current_round.get('course_name', '')
        ).strip()

        if open_shot_map_view(title, groups):
            console.hud_alert('地図を開きました', 'success', 1.0)
        else:
            console.hud_alert('地図を開けませんでした', 'error', 1.2)

    def show_analysis(self, sender):
        self.apply_fields_to_current_round()
        text = build_analysis_text(self.current_round)

        v = ui.View(background_color='white')
        v.name = '分析結果'

        tv = ui.TextView(frame=v.bounds, flex='WH')
        tv.editable = False
        tv.font = ('<System>', 16)
        tv.text = text
        v.add_subview(tv)

        v.present('fullscreen')


# =============================
# 実行
# =============================
if __name__ == '__main__':
    app_storage.stabilize_startup()

    app = GolfDirectionApp()
    app.present('fullscreen')