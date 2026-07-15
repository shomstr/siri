import requests
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

# ==================== КОНФИГУРАЦИЯ ====================
BASE_URL = "https://my.sirius.online/api/activity/v0/schedule/student"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:151.0) Gecko/20100101 Firefox/151.0',
    'Accept': 'application/json;charset=utf-8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Authorization': 'Bearer eyJhbGciOiJQUzUxMiJ9.eyJlbWFpbCI6InNjaGFtaWwuZ2FiQHlhbmRleC5ydSIsInJvbGVzIjpbIm5vb1JXIl0sImlkIjoiMTAwMTMwMDAwMDAwMDIwMTQ5IiwibGFzdE5hbWUiOiLQk9Cw0LHQtNGD0LvQu9C40L0iLCJtaWRkbGVOYW1lIjoi0JvQtdC90LDRgNC-0LLQuNGHICIsImZpcnN0TmFtZSI6ItCo0LDQvNC40LvRjCIsInBhcmVudENvZGUiOiJOS3B4ZzR6eTRWTDgiLCJleHAiOjEuNzg0MjcyNTg1NDMwMjc1Mzc1ZTksImlhdCI6MS43ODM2Njc3ODU0MzAyNzUzNzVlOX0.o0kFAUgwL15rlk9DiMdXbagEB0EF48P3F4X8O_NylcVg3zsUCCvJQeRXNds7MnlwQgenZe7gj3FVgfZJMjg46hECQ5TR9TouJ-4_pk0NMqTFZ1OClMqhMKXH34po4CqGqklVdbWe7TUKZu3fAy44pmYjexKSMI9okjmQvOrc5vD-YPkD_15Du_31LyDDFxoESXq78r_EAlqAuohMPWYCc_AXTFrwwZ-2abIsROPHPE0eY6w2_Z3Ll9w61F9DZdeB0PHQiAOjUInAwgQ_aeBlJuzGh9IRoXFJsKLR3y_15z-gu15NmEi1w-Y9Ffre3VfifKDoQ6-UGHTI1hfMPoDw9w',
    'Connection': 'keep-alive',
    'Referer': 'https://my.sirius.online/record-schedule',
    'Cookie': '_ym_uid=1761134508876631452; _ym_d=1779353456; _ym_isad=2',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'TE': 'trailers'
}

MSK_TZ = timezone(timedelta(hours=3))

# ==================== НАСТРОЙКИ СТРЕЛЬБЫ ====================
# За сколько минут до recordStart начинать стрелять
PRE_START_MINUTES = 2

# Интервал между запросами ДО открытия записи (в секундах)
PRE_SHOOT_INTERVAL_SECONDS = 10

# Количество запросов в 10 секунд ПОСЛЕ открытия записи
POST_SHOTS_PER_WINDOW = 3

# Сколько минут продолжать стрелять ПОСЛЕ recordStart (даже если нет ответа)
POST_DURATION_MINUTES = 8
POST_DURATION_SECONDS = POST_DURATION_MINUTES * 60

# ==================== ХРАНИЛИЩЕ ====================
class Store:
    def __init__(self):
        self.events = []
        self.last_update = None
        self.last_error = None
        self.auto_tasks = []
        self.lock = threading.Lock()

store = Store()

# ==================== УТИЛИТЫ ====================
def utc_to_msk(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return dt.astimezone(MSK_TZ)
    except:
        return None

def get_user_status(event):
    if event.get('isRecorded'):
        return 'in_list', '✅ В списке', 'green'
    if event.get('isReserved'):
        return 'reserved', '⏳ В резерве', 'orange'
    if event.get('availability', {}).get('isAvailable'):
        return 'available', '🟢 Доступно', 'green'
    reasons = event.get('availability', {}).get('reason', [])
    types = [r.get('type') for r in reasons]
    if 'recordClosed' in types:
        return 'closed', '🔒 Запись закрыта', 'red'
    if 'noSpace' in types:
        return 'noSpace', '❌ Мест нет', 'red'
    if 'willOpen' in types:
        for r in reasons:
            if r.get('type') == 'willOpen' and r.get('atISO'):
                t = utc_to_msk(r['atISO'])
                if t:
                    return 'willOpen', f'⏰ Откроется {t.strftime("%d.%m %H:%M")}', 'orange'
        return 'willOpen', '⏰ Скоро откроется', 'orange'
    if 'conflictWithRecord' in types:
        return 'conflict', '⚠ Конфликт', 'purple'
    if 'eventRegistrationLimit' in types:
        return 'limit', '🚫 Лимит записей', 'red'
    if event.get('recordStatus') == 'disabled':
        return 'disabled', '⚫ Отключено', 'gray'
    return 'unknown', '❓', 'gray'

def fetch_events():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        r = session.get(f"{BASE_URL}/record", timeout=15)
        
        if r.status_code == 401:
            store.last_error = "❌ ОШИБКА 401: Токен истёк или невалидный. Обнови токен в настройках!"
            return False, store.last_error
        if r.status_code == 403:
            store.last_error = "❌ ОШИБКА 403: Доступ запрещён"
            return False, store.last_error
        if r.status_code >= 400:
            store.last_error = f"❌ HTTP {r.status_code}: {r.text[:200]}"
            return False, store.last_error
        
        try:
            data = r.json()
        except Exception as e:
            store.last_error = f"❌ Ответ не JSON: {r.text[:200]}"
            return False, store.last_error
        
        if 'success' not in data:
            store.last_error = f"❌ Нет поля 'success' в ответе. Ключи: {list(data.keys())}"
            return False, store.last_error
        
        if not data['success']:
            store.last_error = "⚠ Поле 'success' пустое — мероприятий нет"
            return False, store.last_error
        
        events = []
        for day in data['success']:
            day_iso = day.get('dayISO', '')
            for ev in day.get('events', []):
                ev['dayISO'] = day_iso
                ev['recordStart_msk'] = utc_to_msk(ev.get('recordStart'))
                ev['recordEnd_msk'] = utc_to_msk(ev.get('recordEnd'))
                ev['eventStart_msk'] = utc_to_msk(ev.get('eventStart'))
                ev['eventEnd_msk'] = utc_to_msk(ev.get('eventEnd'))
                status_key, status_text, status_color = get_user_status(ev)
                ev['_status_key'] = status_key
                ev['_status_text'] = status_text
                ev['_status_color'] = status_color
                events.append(ev)
        
        with store.lock:
            store.events = events
            store.last_update = datetime.now(MSK_TZ)
            store.last_error = None
        
        return True, f"✅ Загружено {len(events)} мероприятий"
    except requests.exceptions.Timeout:
        store.last_error = "❌ Таймаут соединения с сервером"
        return False, store.last_error
    except requests.exceptions.ConnectionError as e:
        store.last_error = f"❌ Ошибка соединения: {str(e)[:100]}"
        return False, store.last_error
    except Exception as e:
        store.last_error = f"❌ Неизвестная ошибка: {str(e)}"
        return False, str(e)

def subscribe_event(event_id):
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        r = session.post(
            f"{BASE_URL}/record/subscribe",
            json={"eventId": str(event_id)},
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        try:
            data = r.json()
            ok = data.get('success') or r.status_code == 200
            return ok, r.status_code, data
        except:
            return r.status_code == 200, r.status_code, r.text[:300]
    except Exception as e:
        return False, 0, str(e)

def auto_record_worker(task):
    """
    Рабочий поток для автозаписи.
    - За 2 минуты до recordStart: 1 запрос каждые 10 секунд.
    - После recordStart: 3 запроса каждые 10 секунд (пачкой).
    - Стоп через 8 минут после recordStart.
    """
    event_id = task['event_id']
    target = task['record_start']
    name = task['event_name']
    
    # Время начала стрельбы
    shoot_start = target - timedelta(minutes=PRE_START_MINUTES)
    # Время окончания стрельбы
    shoot_end = target + timedelta(seconds=POST_DURATION_SECONDS)
    
    with store.lock:
        for t in store.auto_tasks:
            if t['event_id'] == event_id and t['thread'] == threading.current_thread():
                t['status'] = 'waiting'
                t['shoot_start'] = shoot_start.strftime('%H:%M:%S')
                t['shoot_end'] = shoot_end.strftime('%H:%M:%S')
    
    # Ждём до начала стрельбы
    while True:
        now = datetime.now(MSK_TZ)
        if now >= shoot_start:
            break
        time.sleep(1)
    
    # Начинаем стрельбу
    with store.lock:
        for t in store.auto_tasks:
            if t['event_id'] == event_id and t['thread'] == threading.current_thread():
                t['status'] = 'shooting'
                t['shots'] = []
    
    shot_count = 0
    success = False
    last_resp = None
    
    # Стреляем циклом
    while True:
        now = datetime.now(MSK_TZ)
        if now > shoot_end:
            break
        
        if now < target:
            # === ФАЗА 1: ДО ОТКРЫТИЯ ЗАПИСИ ===
            # 1 запрос в 10 секунд
            shot_count += 1
            timestamp = now.strftime('%H:%M:%S.%f')[:-3]
            
            ok, code, resp = subscribe_event(event_id)
            last_resp = resp
            
            with store.lock:
                for t in store.auto_tasks:
                    if t['event_id'] == event_id and t['thread'] == threading.current_thread():
                        t['shots'].append({
                            'time': timestamp,
                            'ok': ok,
                            'code': code,
                        })
                        if ok:
                            success = True
            
            # Если успешно — останавливаемся
            if success:
                break
            
            # Ждём интервал перед следующим выстрелом
            time.sleep(PRE_SHOOT_INTERVAL_SECONDS)
        else:
            # === ФАЗА 2: ПОСЛЕ ОТКРЫТИЯ ЗАПИСИ ===
            # 3 запроса в 10 секунд (пачкой)
            window_start = time.time()
            
            for i in range(POST_SHOTS_PER_WINDOW):
                now = datetime.now(MSK_TZ)
                if now > shoot_end:
                    break
                
                shot_count += 1
                timestamp = now.strftime('%H:%M:%S.%f')[:-3]
                
                ok, code, resp = subscribe_event(event_id)
                last_resp = resp
                
                with store.lock:
                    for t in store.auto_tasks:
                        if t['event_id'] == event_id and t['thread'] == threading.current_thread():
                            t['shots'].append({
                                'time': timestamp,
                                'ok': ok,
                                'code': code,
                            })
                            if ok:
                                success = True
                
                # Если успешно — останавливаемся
                if success:
                    break
                
                # Небольшая задержка между запросами в пачке, чтобы не слать абсолютно одновременно
                if i < POST_SHOTS_PER_WINDOW - 1:
                    time.sleep(0.2)
            
            # Если успешно — останавливаемся
            if success:
                break
            
            # Ждём остаток 10-секундного окна
            elapsed = time.time() - window_start
            time.sleep(max(0, 10 - elapsed))
    
    # Финальный статус
    with store.lock:
        for t in store.auto_tasks:
            if t['event_id'] == event_id and t['thread'] == threading.current_thread():
                if not success:
                    t['status'] = 'failed'
                t['total_shots'] = shot_count
                t['result'] = {
                    'ok': success,
                    'shots': shot_count,
                    'last_response': last_resp if not success else None
                }

# ==================== API ====================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/events')
def api_events():
    date_filter = request.args.get('date', 'all')
    status_filter = request.args.get('status', 'all')
    only_bookable = request.args.get('bookable', 'false') == 'true'
    search = request.args.get('search', '').strip().lower()

    with store.lock:
        events = list(store.events)
        last_update = store.last_update
        last_error = store.last_error

    now = datetime.now(MSK_TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    filtered = []
    for ev in events:
        if date_filter == 'today':
            if not ev.get('eventStart_msk') or ev['eventStart_msk'].date() != today:
                continue
        elif date_filter == 'tomorrow':
            if not ev.get('eventStart_msk') or ev['eventStart_msk'].date() != tomorrow:
                continue
        elif date_filter not in ('all', ''):
            try:
                target_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
                if not ev.get('eventStart_msk') or ev['eventStart_msk'].date() != target_date:
                    continue
            except:
                pass

        if status_filter != 'all' and ev['_status_key'] != status_filter:
            continue

        if only_bookable and ev['_status_key'] not in ('available', 'reserved'):
            continue

        if search and search not in ev.get('eventName', '').lower():
            continue

        filtered.append(ev)

    filtered.sort(key=lambda e: e.get('eventStart_msk') or datetime.max.replace(tzinfo=MSK_TZ))

    result = []
    for ev in filtered:
        result.append({
            'eventId': ev.get('eventId'),
            'eventName': ev.get('eventName'),
            'dayISO': ev.get('dayISO'),
            'eventStart': ev['eventStart_msk'].strftime('%Y-%m-%d %H:%M') if ev.get('eventStart_msk') else None,
            'eventEnd': ev['eventEnd_msk'].strftime('%H:%M') if ev.get('eventEnd_msk') else None,
            'recordStart': ev['recordStart_msk'].strftime('%Y-%m-%d %H:%M:%S') if ev.get('recordStart_msk') else None,
            'recordEnd': ev['recordEnd_msk'].strftime('%Y-%m-%d %H:%M:%S') if ev.get('recordEnd_msk') else None,
            'peopleCurrent': ev.get('peopleCurrent', 0),
            'peopleMax': ev.get('peopleMax', 0),
            'location': ev.get('eventLocation', []),
            'tutors': ev.get('tutors', []),
            'status_key': ev['_status_key'],
            'status_text': ev['_status_text'],
            'status_color': ev['_status_color'],
            'isRecorded': ev.get('isRecorded', False),
            'isReserved': ev.get('isReserved', False),
        })

    return jsonify({
        'ok': True,
        'count': len(result),
        'last_update': last_update.strftime('%H:%M:%S') if last_update else None,
        'last_error': last_error,
        'server_time': now.strftime('%Y-%m-%d %H:%M:%S'),
        'events': result
    })

@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    ok, msg = fetch_events()
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/subscribe', methods=['POST'])
def api_subscribe():
    data = request.get_json() or {}
    event_id = data.get('eventId')
    if not event_id:
        return jsonify({'ok': False, 'error': 'no eventId'}), 400
    ok, code, resp = subscribe_event(event_id)
    return jsonify({'ok': ok, 'code': code, 'response': resp})

@app.route('/api/auto-record', methods=['POST'])
def api_auto_record():
    data = request.get_json() or {}
    event_ids = data.get('eventIds', [])
    if not event_ids:
        return jsonify({'ok': False, 'error': 'no eventIds'}), 400

    with store.lock:
        events_map = {str(e['eventId']): e for e in store.events}

    scheduled = []
    for eid in event_ids:
        eid = str(eid)
        ev = events_map.get(eid)
        if not ev:
            continue
        rs = ev.get('recordStart_msk')
        if not rs:
            continue

        task = {
            'event_id': eid,
            'event_name': ev.get('eventName'),
            'record_start': rs,
            'status': 'scheduled',
            'result': None,
            'fire_time': None,
            'thread': None,
            'shoot_start': None,
            'shoot_end': None,
            'shots': [],
            'total_shots': 0,
        }
        t = threading.Thread(target=auto_record_worker, args=(task,), daemon=True)
        task['thread'] = t
        with store.lock:
            store.auto_tasks.append(task)
        t.start()
        
        shoot_start = rs - timedelta(minutes=PRE_START_MINUTES)
        scheduled.append({
            'eventId': eid, 
            'name': ev.get('eventName'), 
            'recordStart': rs.strftime('%Y-%m-%d %H:%M:%S'),
            'shootStart': shoot_start.strftime('%Y-%m-%d %H:%M:%S')
        })

    return jsonify({'ok': True, 'scheduled': scheduled})

@app.route('/api/auto-record/status')
def api_auto_status():
    with store.lock:
        tasks = [{
            'event_id': t['event_id'],
            'event_name': t['event_name'],
            'record_start': t['record_start'].strftime('%Y-%m-%d %H:%M:%S'),
            'status': t['status'],
            'shoot_start': t.get('shoot_start'),
            'shoot_end': t.get('shoot_end'),
            'shots': t.get('shots', []),
            'total_shots': t.get('total_shots', 0),
            'result': t.get('result'),
        } for t in store.auto_tasks]
    return jsonify({'ok': True, 'tasks': tasks})

@app.route('/api/update-token', methods=['POST'])
def api_update_token():
    data = request.get_json() or {}
    new_token = data.get('token', '').strip()
    if not new_token:
        return jsonify({'ok': False, 'error': 'Пустой токен'}), 400
    
    if new_token.startswith('Bearer '):
        new_token = new_token[7:]
    
    HEADERS['Authorization'] = f'Bearer {new_token}'
    
    ok, msg = fetch_events()
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/status')
def api_status():
    with store.lock:
        return jsonify({
            'ok': True,
            'events_count': len(store.events),
            'last_update': store.last_update.strftime('%Y-%m-%d %H:%M:%S') if store.last_update else None,
            'last_error': store.last_error,
            'has_token': bool(HEADERS.get('Authorization', '').startswith('Bearer ')),
            'config': {
                'pre_start_minutes': PRE_START_MINUTES,
                'pre_shoot_interval_seconds': PRE_SHOOT_INTERVAL_SECONDS,
                'post_shots_per_window': POST_SHOTS_PER_WINDOW,
                'post_duration_seconds': POST_DURATION_SECONDS,
            }
        })

# ==================== HTML ====================
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Sirius Auto-Record</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0f1419; color: #e6e6e6; padding: 16px; }
  h1 { color: #4da6ff; margin-bottom: 12px; }
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 10px; }
  .meta { color: #888; font-size: 13px; }
  .status-bar { padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 13px; }
  .status-ok { background: rgba(46,160,67,0.15); color: #3fb950; border: 1px solid #2ea043; }
  .status-error { background: rgba(218,54,51,0.15); color: #f85149; border: 1px solid #da3633; }
  .status-warn { background: rgba(210,153,34,0.15); color: #e3b341; border: 1px solid #d29922; }
  .filters { background: #1a2029; padding: 14px; border-radius: 10px; margin-bottom: 16px; display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
  .filters label { display: block; font-size: 12px; color: #888; margin-bottom: 4px; }
  .filters input, .filters select { width: 100%; padding: 8px; background: #0f1419; color: #e6e6e6; border: 1px solid #2a3441; border-radius: 6px; font-size: 14px; }
  .filters input:focus, .filters select:focus { outline: none; border-color: #4da6ff; }
  .btn { padding: 8px 14px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 500; transition: 0.15s; }
  .btn-primary { background: #4da6ff; color: #000; }
  .btn-primary:hover { background: #7bc0ff; }
  .btn-success { background: #2ea043; color: #fff; }
  .btn-success:hover { background: #3fb950; }
  .btn-warn { background: #d29922; color: #000; }
  .btn-warn:hover { background: #e3b341; }
  .btn-danger { background: #da3633; color: #fff; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .actions { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  .stats { background: #1a2029; padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; }
  .stats .chip { padding: 3px 10px; border-radius: 12px; background: #2a3441; }
  table { width: 100%; border-collapse: collapse; background: #1a2029; border-radius: 10px; overflow: hidden; }
  th { background: #242d38; padding: 10px; text-align: left; font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; position: sticky; top: 0; }
  td { padding: 10px; border-top: 1px solid #2a3441; font-size: 13px; vertical-align: top; }
  tr:hover { background: #242d38; }
  .status-badge { display: inline-block; padding: 3px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .status-green { background: rgba(46,160,67,0.2); color: #3fb950; }
  .status-orange { background: rgba(210,153,34,0.2); color: #e3b341; }
  .status-red { background: rgba(218,54,51,0.2); color: #f85149; }
  .status-purple { background: rgba(163,113,247,0.2); color: #a371f7; }
  .status-gray { background: rgba(139,148,158,0.2); color: #8b949e; }
  .status-blue { background: rgba(77,166,255,0.2); color: #4da6ff; }
  .event-name { font-weight: 600; color: #e6e6e6; margin-bottom: 3px; }
  .event-meta { color: #888; font-size: 11px; }
  .fill-bar { display: inline-block; width: 60px; height: 6px; background: #2a3441; border-radius: 3px; overflow: hidden; vertical-align: middle; margin-right: 6px; }
  .fill-bar > div { height: 100%; }
  .checkbox { width: 16px; height: 16px; cursor: pointer; }
  .log { background: #0a0d12; border: 1px solid #2a3441; border-radius: 8px; padding: 10px; margin-top: 12px; max-height: 200px; overflow-y: auto; font-family: monospace; font-size: 12px; }
  .log-entry { padding: 3px 0; border-bottom: 1px dashed #2a3441; }
  .log-ok { color: #3fb950; }
  .log-err { color: #f85149; }
  .log-info { color: #888; }
  .tabs { display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 1px solid #2a3441; }
  .tab { padding: 8px 14px; cursor: pointer; border-bottom: 2px solid transparent; color: #888; font-size: 13px; }
  .tab.active { color: #4da6ff; border-bottom-color: #4da6ff; }
  .hidden { display: none; }
  .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 1000; justify-content: center; align-items: center; }
  .modal.show { display: flex; }
  .modal-content { background: #1a2029; padding: 24px; border-radius: 12px; max-width: 600px; width: 90%; }
  .modal-content h2 { color: #4da6ff; margin-bottom: 16px; }
  .modal-content textarea { width: 100%; min-height: 120px; padding: 10px; background: #0f1419; color: #e6e6e6; border: 1px solid #2a3441; border-radius: 6px; font-family: monospace; font-size: 12px; resize: vertical; }
  .modal-actions { display: flex; gap: 8px; margin-top: 16px; justify-content: flex-end; }
  .shot-list { margin-top: 8px; font-size: 11px; color: #888; }
  .shot-item { padding: 2px 0; }
  .shot-ok { color: #3fb950; }
  .shot-fail { color: #f85149; }
  .config-info { background: #1a2029; padding: 10px; border-radius: 6px; margin: 10px 0; font-size: 12px; color: #888; }
</style>
</head>
<body>

<div class="header">
  <h1>🎯 Sirius Auto-Record</h1>
  <div style="display:flex;gap:8px;align-items:center;">
    <button class="btn btn-warn" onclick="showTokenModal()">🔑 Токен</button>
    <div class="meta" id="serverTime">—</div>
  </div>
</div>

<div id="statusBar" class="status-bar status-warn">⏳ Загрузка статуса...</div>

<div class="tabs">
  <div class="tab active" data-tab="events">Мероприятия</div>
  <div class="tab" data-tab="auto">Автозаписи <span id="autoCount" style="background:#4da6ff;color:#000;padding:1px 7px;border-radius:10px;font-size:11px;margin-left:4px;">0</span></div>
</div>

<!-- ВКЛАДКА МЕРОПРИЯТИЙ -->
<div id="tab-events">
  <div class="filters">
    <div>
      <label>🔍 Поиск</label>
      <input type="text" id="search" placeholder="Название...">
    </div>
    <div>
      <label>📅 Дата</label>
      <select id="dateFilter">
        <option value="all">Все дни</option>
        <option value="today">Сегодня</option>
        <option value="tomorrow">Завтра</option>
      </select>
    </div>
    <div>
      <label>🎯 Статус</label>
      <select id="statusFilter">
        <option value="all">Все</option>
        <option value="available">🟢 Доступно</option>
        <option value="in_list">✅ В списке</option>
        <option value="reserved">⏳ В резерве</option>
        <option value="willOpen">⏰ Скоро откроется</option>
        <option value="noSpace">❌ Мест нет</option>
        <option value="conflict">⚠ Конфликт</option>
        <option value="closed">🔒 Закрыто</option>
      </select>
    </div>
    <div>
      <label>⚙ Дополнительно</label>
      <label style="display:flex;align-items:center;gap:6px;color:#e6e6e6;font-size:13px;margin-top:4px;">
        <input type="checkbox" id="bookableOnly" style="width:auto;"> Только доступные для записи
      </label>
    </div>
  </div>

  <div class="actions">
    <button class="btn btn-primary" onclick="refreshEvents()">🔄 Обновить</button>
    <button class="btn btn-success" onclick="subscribeSelected()">⚡ Записать выбранные сейчас</button>
    <button class="btn btn-warn" onclick="autoRecordSelected()">⏰ Запланировать автозапись</button>
    <button class="btn" style="background:#2a3441;color:#e6e6e6;" onclick="toggleAll()">☑ Выделить все</button>
  </div>

  <div class="stats" id="stats"></div>

  <table>
    <thead>
      <tr>
        <th style="width:30px;"></th>
        <th>ID</th>
        <th>Мероприятие</th>
        <th>Начало</th>
        <th>Запись</th>
        <th>Места</th>
        <th>Статус</th>
        <th>Действия</th>
      </tr>
    </thead>
    <tbody id="eventsBody"></tbody>
  </table>
</div>

<!-- ВКЛАДКА АВТОЗАПИСЕЙ -->
<div id="tab-auto" class="hidden">
  <div class="config-info">
    <strong>⚙ Настройки стрельбы:</strong> 
    За <span id="cfgPreStart">2</span> мин до записи: 1 запрос в <span id="cfgPreInterval">10</span> сек. 
    После открытия: <span id="cfgPostShots">3</span> запроса в 10 сек. 
    Стоп через <span id="cfgPostDuration">8</span> мин.
  </div>
  <div class="actions">
    <button class="btn btn-primary" onclick="loadAutoTasks()">🔄 Обновить</button>
  </div>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Мероприятие</th>
        <th>Запись в</th>
        <th>Стрельба</th>
        <th>Статус</th>
        <th>Выстрелы</th>
        <th>Результат</th>
      </tr>
    </thead>
    <tbody id="autoBody"></tbody>
  </table>
</div>

<div class="log" id="log"></div>

<!-- МОДАЛКА ТОКЕНА -->
<div id="tokenModal" class="modal">
  <div class="modal-content">
    <h2>🔑 Обновить токен</h2>
    <p style="color:#888;font-size:13px;margin-bottom:12px;">
      Вставь новый Bearer токен из DevTools (Network → любой запрос к API → Headers → Authorization).
      Можно вставлять как с "Bearer ", так и без.
    </p>
    <textarea id="tokenInput" placeholder="eyJhbGciOiJQUzUxMiJ9..."></textarea>
    <div class="modal-actions">
      <button class="btn" style="background:#2a3441;color:#e6e6e6;" onclick="hideTokenModal()">Отмена</button>
      <button class="btn btn-success" onclick="updateToken()">💾 Сохранить и проверить</button>
    </div>
    <div id="tokenResult" style="margin-top:12px;font-size:13px;"></div>
  </div>
</div>

<script>
const selected = new Set();
let allEvents = [];

function log(msg, type='info') {
  const el = document.getElementById('log');
  const time = new Date().toLocaleTimeString('ru-RU');
  const entry = document.createElement('div');
  entry.className = 'log-entry log-' + type;
  entry.textContent = `[${time}] ${msg}`;
  el.insertBefore(entry, el.firstChild);
  if (el.children.length > 100) el.removeChild(el.lastChild);
}

async function checkStatus() {
  try {
    const r = await fetch('/api/status');
    const data = await r.json();
    const bar = document.getElementById('statusBar');
    
    if (data.last_error) {
      bar.className = 'status-bar status-error';
      bar.textContent = data.last_error;
    } else if (data.events_count > 0) {
      bar.className = 'status-bar status-ok';
      bar.textContent = `✅ Подключено. Загружено мероприятий: ${data.events_count}. Обновлено: ${data.last_update || '—'}`;
    } else {
      bar.className = 'status-bar status-warn';
      bar.textContent = '⚠ Список мероприятий пуст. Нажми "Обновить".';
    }
    
    // Обновляем конфиг
    if (data.config) {
      document.getElementById('cfgPreStart').textContent = data.config.pre_start_minutes;
      document.getElementById('cfgPreInterval').textContent = data.config.pre_shoot_interval_seconds;
      document.getElementById('cfgPostShots').textContent = data.config.post_shots_per_window;
      document.getElementById('cfgPostDuration').textContent = data.config.post_duration_seconds / 60;
    }
  } catch(e) {
    log('Ошибка проверки статуса: ' + e, 'err');
  }
}

async function refreshEvents() {
  log('Обновление списка...');
  const r = await fetch('/api/refresh', {method:'POST'});
  const data = await r.json();
  log(data.message, data.ok ? 'ok' : 'err');
  await checkStatus();
  await loadEvents();
}

async function loadEvents() {
  const params = new URLSearchParams({
    search: document.getElementById('search').value,
    date: document.getElementById('dateFilter').value,
    status: document.getElementById('statusFilter').value,
    bookable: document.getElementById('bookableOnly').checked,
  });
  const r = await fetch('/api/events?' + params);
  const data = await r.json();
  document.getElementById('serverTime').textContent = `Сервер: ${data.server_time}`;
  allEvents = data.events;
  renderEvents(data);
}

function renderEvents(data) {
  const body = document.getElementById('eventsBody');
  body.innerHTML = '';
  const stats = document.getElementById('stats');

  const counts = {};
  data.events.forEach(e => counts[e.status_key] = (counts[e.status_key]||0)+1);
  stats.innerHTML = `
    <span>Всего: <b>${data.count}</b></span>
    <span class="chip" style="color:#3fb950;">🟢 ${counts.available||0}</span>
    <span class="chip" style="color:#3fb950;">✅ В списке: ${counts.in_list||0}</span>
    <span class="chip" style="color:#e3b341;">⏳ Резерв: ${counts.reserved||0}</span>
    <span class="chip" style="color:#e3b341;">⏰ Скоро: ${counts.willOpen||0}</span>
    <span class="chip" style="color:#f85149;">❌ Мест нет: ${counts.noSpace||0}</span>
    <span class="chip" style="color:#a371f7;">⚠ Конфликт: ${counts.conflict||0}</span>
  `;

  if (data.events.length === 0) {
    body.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#888;padding:30px;">Нет мероприятий по заданным фильтрам</td></tr>';
    return;
  }

  data.events.forEach(ev => {
    const tr = document.createElement('tr');
    const pct = ev.peopleMax ? Math.round(ev.peopleCurrent/ev.peopleMax*100) : 0;
    const barColor = pct >= 90 ? '#f85149' : pct >= 50 ? '#e3b341' : '#3fb950';
    const statusClass = 'status-' + ev.status_color;

    tr.innerHTML = `
      <td><input type="checkbox" class="checkbox" data-id="${ev.eventId}" ${selected.has(ev.eventId)?'checked':''}></td>
      <td style="color:#888;font-family:monospace;">${ev.eventId}</td>
      <td>
        <div class="event-name">${ev.eventName}</div>
        <div class="event-meta">📍 ${ev.location.join(', ') || '—'}${ev.tutors.length?' • 👨‍🏫 '+ev.tutors.join(', '):''}</div>
        <div class="event-meta">📅 ${ev.dayISO}</div>
      </td>
      <td>${ev.eventStart || '—'}</td>
      <td style="font-size:11px;color:#888;">
        ${ev.recordStart ? 'с '+ev.recordStart : '—'}<br>
        ${ev.recordEnd ? 'до '+ev.recordEnd : ''}
      </td>
      <td>
        <span class="fill-bar"><div style="width:${pct}%;background:${barColor};"></div></span>
        ${ev.peopleCurrent}/${ev.peopleMax} <span style="color:#888;">(${pct}%)</span>
      </td>
      <td><span class="status-badge ${statusClass}">${ev.status_text}</span></td>
      <td>
        <button class="btn btn-success" style="padding:4px 8px;font-size:11px;" onclick="subscribeOne('${ev.eventId}')">Записать</button>
        <button class="btn btn-warn" style="padding:4px 8px;font-size:11px;" onclick="autoOne('${ev.eventId}')">Авто</button>
      </td>
    `;
    body.appendChild(tr);
  });

  body.querySelectorAll('.checkbox').forEach(cb => {
    cb.addEventListener('change', e => {
      const id = e.target.dataset.id;
      if (e.target.checked) selected.add(id); else selected.delete(id);
    });
  });
}

function toggleAll() {
  const boxes = document.querySelectorAll('#eventsBody .checkbox');
  const allChecked = [...boxes].every(b => b.checked);
  boxes.forEach(b => {
    b.checked = !allChecked;
    if (b.checked) selected.add(b.dataset.id); else selected.delete(b.dataset.id);
  });
}

async function subscribeOne(id) {
  log(`Запись на ${id}...`);
  const r = await fetch('/api/subscribe', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({eventId:id})});
  const data = await r.json();
  log(`[${id}] ${data.ok?'✅ Успешно':'❌ Ошибка'} (HTTP ${data.code})`, data.ok?'ok':'err');
  if (data.ok) setTimeout(loadEvents, 500);
}

async function autoOne(id) {
  await fetch('/api/auto-record', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({eventIds:[id]})});
  log(`⏰ Автозапись запланирована для ${id}`, 'info');
  updateAutoCount();
}

async function subscribeSelected() {
  if (!selected.size) { alert('Выберите мероприятия'); return; }
  if (!confirm(`Записаться на ${selected.size} мероприятий СЕЙЧАС?`)) return;
  for (const id of selected) {
    await subscribeOne(id);
  }
}

async function autoRecordSelected() {
  if (!selected.size) { alert('Выберите мероприятия'); return; }
  const ids = [...selected];
  await fetch('/api/auto-record', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({eventIds:ids})});
  log(`⏰ Запланировано автозаписей: ${ids.length}`, 'ok');
  updateAutoCount();
}

async function loadAutoTasks() {
  const r = await fetch('/api/auto-record/status');
  const data = await r.json();
  const body = document.getElementById('autoBody');
  body.innerHTML = '';
  if (data.tasks.length === 0) {
    body.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888;padding:30px;">Нет запланированных автозаписей</td></tr>';
  }
  data.tasks.forEach(t => {
    const statusColor = {
      'scheduled':'#888',
      'waiting':'#e3b341',
      'shooting':'#4da6ff',
      'success':'#3fb950',
      'failed':'#f85149'
    }[t.status] || '#888';
    
    const statusText = {
      'scheduled':'⏳ Запланировано',
      'waiting':'⏰ Ожидание',
      'shooting':'🔫 Стрельба',
      'success':'✅ Успех',
      'failed':'❌ Неудача'
    }[t.status] || t.status;
    
    let result = '';
    if (t.result) {
      result = t.result.ok ? '<span style="color:#3fb950;">✅ Успешно</span>' : `<span style="color:#f85149;">❌ Неудача</span>`;
    }
    
    let shotsHtml = '';
    if (t.shots && t.shots.length > 0) {
      shotsHtml = '<div class="shot-list">';
      t.shots.slice(-5).forEach(s => {
        shotsHtml += `<div class="shot-item ${s.ok?'shot-ok':'shot-fail'}">${s.time} - ${s.ok?'✅':'❌'} (${s.code})</div>`;
      });
      if (t.shots.length > 5) {
        shotsHtml += `<div class="shot-item" style="color:#888;">...и ещё ${t.shots.length - 5}</div>`;
      }
      shotsHtml += '</div>';
    }
    
    body.innerHTML += `
      <tr>
        <td style="font-family:monospace;color:#888;">${t.event_id}</td>
        <td>${t.event_name}</td>
        <td>${t.record_start}</td>
        <td style="font-size:11px;color:#888;">
          ${t.shoot_start ? 'с '+t.shoot_start : '—'}<br>
          ${t.shoot_end ? 'до '+t.shoot_end : ''}
        </td>
        <td><span style="color:${statusColor};font-weight:600;">${statusText}</span></td>
        <td>
          <div style="font-size:12px;">Всего: <b>${t.total_shots || 0}</b></div>
          ${shotsHtml}
        </td>
        <td>${result}</td>
      </tr>
    `;
  });
  document.getElementById('autoCount').textContent = data.tasks.length;
}

async function updateAutoCount() {
  const r = await fetch('/api/auto-record/status');
  const data = await r.json();
  document.getElementById('autoCount').textContent = data.tasks.length;
}

// МОДАЛКА ТОКЕНА
function showTokenModal() {
  document.getElementById('tokenModal').classList.add('show');
  document.getElementById('tokenResult').textContent = '';
  fetch('/api/status').then(r=>r.json()).then(d=>{
    document.getElementById('tokenInput').value = '';
    document.getElementById('tokenInput').focus();
  });
}

function hideTokenModal() {
  document.getElementById('tokenModal').classList.remove('show');
}

async function updateToken() {
  const token = document.getElementById('tokenInput').value.trim();
  const result = document.getElementById('tokenResult');
  if (!token) {
    result.innerHTML = '<span style="color:#f85149;">❌ Вставь токен</span>';
    return;
  }
  result.innerHTML = '<span style="color:#e3b341;">⏳ Проверяю...</span>';
  const r = await fetch('/api/update-token', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({token: token})
  });
  const data = await r.json();
  if (data.ok) {
    result.innerHTML = `<span style="color:#3fb950;">✅ ${data.message}</span>`;
    setTimeout(() => {
      hideTokenModal();
      checkStatus();
      loadEvents();
    }, 1000);
  } else {
    result.innerHTML = `<span style="color:#f85149;">❌ ${data.message}</span>`;
  }
}

// Табы
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-events').classList.toggle('hidden', tab.dataset.tab !== 'events');
    document.getElementById('tab-auto').classList.toggle('hidden', tab.dataset.tab !== 'auto');
    if (tab.dataset.tab === 'auto') loadAutoTasks();
  });
});

['search','dateFilter','statusFilter','bookableOnly'].forEach(id => {
  document.getElementById(id).addEventListener('input', loadEvents);
  document.getElementById(id).addEventListener('change', loadEvents);
});

setInterval(loadEvents, 15000);
setInterval(updateAutoCount, 3000);
setInterval(checkStatus, 10000);

// Старт
checkStatus();
loadEvents();
updateAutoCount();
log('Интерфейс загружен', 'ok');
</script>

</body>
</html>
"""

# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Sirius Auto-Record Web")
    print("=" * 60)
    print(f"⚙ Настройки стрельбы:")
    print(f"   - За {PRE_START_MINUTES} мин до записи: 1 запрос в {PRE_SHOOT_INTERVAL_SECONDS} сек")
    print(f"   - После открытия: {POST_SHOTS_PER_WINDOW} запроса в 10 сек (пачкой)")
    print(f"   - Стоп через {POST_DURATION_MINUTES} мин после открытия")
    print()
    print("Загружаю мероприятия...")
    ok, msg = fetch_events()
    print(f"  {msg}")
    print()
    print("🌐 Открой в браузере:")
    print(f"     http://localhost:6689")
    print(f"     http://<твой_IP>:6689  (для доступа с других устройств)")
    print("=" * 60)
    app.run(host='0.0.0.0', port=6689, debug=False, threaded=True)