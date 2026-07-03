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
    'Authorization': 'Bearer eyJhbGciOiJQUzUxMiJ9.eyJlbWFpbCI6InNjaGFtaWwuZ2FiQHlhbmRleC5ydSIsInJvbGVzIjpbIm5vb1JXIl0sImlkIjoiMTAwMTMwMDAwMDAwMDIwMTQ5IiwibGFzdE5hbWUiOiLQk9Cw0LHQtNGD0LvQu9C40L0iLCJtaWRkbGVOYW1lIjoi0JvQtdC90LDRgNC-0LLQuNGHICIsImZpcnN0TmFtZSI6ItCo0LDQvNC40LvRjCIsInBhcmVudENvZGUiOiJOS3B4ZzR6eTRWTDgiLCJleHAiOjEuNzgzMzUwNTYwMzA4NzA4NzgyZTksImlhdCI6MS43ODI3NDU3NjAzMDg3MDg3ODJlOX0.krYUpeZab1d1WYS8cJ3zpOgoifhzAHR7bgSuaeelqyy4dz_RwmqaTWwyQuWLv2Cq5wKhu_N2WHm8wmmZS5jS5QjKrqyKGMtWE-BvzO71BQ3j-oTmEC3j6SjHQ0VdANficpBz4Ioz_PT7KDWDdiAcm_eyr2FvsXyvQkNMfNcVRfBuprQ2FVOsA1CxgSEJe12xGy7PC1ArTdaBT1U4skJvyxD0lHY5CswXt4mjZ-A3eym38Baycu674L_m5pYO_T0V4YjIiVt0iuPnDdAkdlBrckFhPVB5zlbcTrXPOL4HRYZSO0c4qLWAnOKJlfQSKzvZk4u-Mc33H_CDJFqBhCdo-Q',
    'Connection': 'keep-alive',
    'Referer': 'https://my.sirius.online/record-schedule',
    'Cookie': '_ym_uid=1761134508876631452; _ym_d=1779353456; _ym_isad=2',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'TE': 'trailers'
}

MSK_TZ = timezone(timedelta(hours=3))

# ==================== ХРАНИЛИЩЕ ====================
class Store:
    def __init__(self):
        self.events = []
        self.last_update = None
        self.auto_tasks = []  # {event_id, event_name, record_start, status, result, thread}
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
        r.raise_for_status()
        data = r.json()
        if not data.get('success'):
            return False, "API error"
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
        return True, f"Загружено {len(events)} мероприятий"
    except Exception as e:
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
    event_id = task['event_id']
    target = task['record_start']
    name = task['event_name']

    with store.lock:
        for t in store.auto_tasks:
            if t['event_id'] == event_id and t['thread'] == threading.current_thread():
                t['status'] = 'waiting'

    # Ждём до 1 сек до цели
    while True:
        now = datetime.now(MSK_TZ)
        delta = (target - now).total_seconds()
        if delta > 1.0:
            time.sleep(0.5)
        else:
            break

    # Busy-wait последние 50мс
    while datetime.now(MSK_TZ) < target:
        pass

    with store.lock:
        for t in store.auto_tasks:
            if t['event_id'] == event_id and t['thread'] == threading.current_thread():
                t['status'] = 'firing'
                t['fire_time'] = datetime.now(MSK_TZ).strftime('%H:%M:%S.%f')

    ok, code, resp = subscribe_event(event_id)

    # Retry
    if not ok:
        for retry in range(2):
            time.sleep(0.1)
            ok, code, resp = subscribe_event(event_id)
            if ok:
                break

    with store.lock:
        for t in store.auto_tasks:
            if t['event_id'] == event_id and t['thread'] == threading.current_thread():
                t['status'] = 'done' if ok else 'failed'
                t['result'] = {'ok': ok, 'code': code, 'response': resp}

# ==================== API ====================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/events')
def api_events():
    # Фильтры
    date_filter = request.args.get('date', 'all')  # today / tomorrow / all / YYYY-MM-DD
    status_filter = request.args.get('status', 'all')
    only_bookable = request.args.get('bookable', 'false') == 'true'
    search = request.args.get('search', '').strip().lower()

    with store.lock:
        events = list(store.events)
        last_update = store.last_update

    now = datetime.now(MSK_TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    filtered = []
    for ev in events:
        # Дата
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

        # Статус
        if status_filter != 'all' and ev['_status_key'] != status_filter:
            continue

        # Только доступные
        if only_bookable and ev['_status_key'] not in ('available', 'reserved'):
            continue

        # Поиск
        if search and search not in ev.get('eventName', '').lower():
            continue

        filtered.append(ev)

    # Сортировка: по дате мероприятия
    filtered.sort(key=lambda e: e.get('eventStart_msk') or datetime.max.replace(tzinfo=MSK_TZ))

    # Формат для фронта
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
        }
        t = threading.Thread(target=auto_record_worker, args=(task,), daemon=True)
        task['thread'] = t
        with store.lock:
            store.auto_tasks.append(task)
        t.start()
        scheduled.append({'eventId': eid, 'name': ev.get('eventName'), 'recordStart': rs.strftime('%Y-%m-%d %H:%M:%S')})

    return jsonify({'ok': True, 'scheduled': scheduled})

@app.route('/api/auto-record/status')
def api_auto_status():
    with store.lock:
        tasks = [{
            'event_id': t['event_id'],
            'event_name': t['event_name'],
            'record_start': t['record_start'].strftime('%Y-%m-%d %H:%M:%S'),
            'status': t['status'],
            'fire_time': t.get('fire_time'),
            'result': t.get('result'),
        } for t in store.auto_tasks]
    return jsonify({'ok': True, 'tasks': tasks})

@app.route('/api/dates')
def api_dates():
    """Список доступных дат для фильтра"""
    with store.lock:
        events = store.events
    dates = sorted(set(e['dayISO'] for e in events if e.get('dayISO')))
    return jsonify({'dates': dates})

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
  .event-name { font-weight: 600; color: #e6e6e6; margin-bottom: 3px; }
  .event-meta { color: #888; font-size: 11px; }
  .fill-bar { display: inline-block; width: 60px; height: 6px; background: #2a3441; border-radius: 3px; overflow: hidden; vertical-align: middle; margin-right: 6px; }
  .fill-bar > div { height: 100%; }
  .checkbox { width: 16px; height: 16px; cursor: pointer; }
  .selected-bar { position: sticky; bottom: 0; background: #242d38; padding: 12px; border-radius: 10px; margin-top: 12px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 -4px 20px rgba(0,0,0,0.5); }
  .log { background: #0a0d12; border: 1px solid #2a3441; border-radius: 8px; padding: 10px; margin-top: 12px; max-height: 200px; overflow-y: auto; font-family: monospace; font-size: 12px; }
  .log-entry { padding: 3px 0; border-bottom: 1px dashed #2a3441; }
  .log-ok { color: #3fb950; }
  .log-err { color: #f85149; }
  .log-info { color: #888; }
  .tabs { display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 1px solid #2a3441; }
  .tab { padding: 8px 14px; cursor: pointer; border-bottom: 2px solid transparent; color: #888; font-size: 13px; }
  .tab.active { color: #4da6ff; border-bottom-color: #4da6ff; }
  .hidden { display: none; }
</style>
</head>
<body>

<div class="header">
  <h1>🎯 Sirius Auto-Record</h1>
  <div class="meta" id="serverTime">—</div>
</div>

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
  <div class="actions">
    <button class="btn btn-primary" onclick="loadAutoTasks()">🔄 Обновить</button>
    <button class="btn btn-danger" onclick="clearAutoTasks()">🗑 Очистить завершённые</button>
  </div>
  <table>
    <thead>
      <tr>
        <th>ID</th>
        <th>Мероприятие</th>
        <th>Стрельба в</th>
        <th>Статус</th>
        <th>Выстрел</th>
        <th>Результат</th>
      </tr>
    </thead>
    <tbody id="autoBody"></tbody>
  </table>
</div>

<div class="log" id="log"></div>

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

async function refreshEvents() {
  log('Обновление списка...');
  await fetch('/api/refresh', {method:'POST'});
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
  document.getElementById('serverTime').textContent = `Сервер: ${data.server_time} | Обновлено: ${data.last_update || '—'}`;
  allEvents = data.events;
  renderEvents(data);
}

function renderEvents(data) {
  const body = document.getElementById('eventsBody');
  body.innerHTML = '';
  const stats = document.getElementById('stats');

  // Считаем статусы
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

  // Обработчики чекбоксов
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
  data.tasks.forEach(t => {
    const statusColor = {scheduled:'#888',waiting:'#e3b341',firing:'#4da6ff',done:'#3fb950',failed:'#f85149'}[t.status] || '#888';
    let result = '';
    if (t.result) {
      result = t.result.ok ? '<span style="color:#3fb950;">✅ OK</span>' : `<span style="color:#f85149;">❌ ${t.result.code||''}</span>`;
    }
    body.innerHTML += `
      <tr>
        <td style="font-family:monospace;color:#888;">${t.event_id}</td>
        <td>${t.event_name}</td>
        <td>${t.record_start}</td>
        <td><span style="color:${statusColor};font-weight:600;">${t.status}</span></td>
        <td>${t.fire_time || '—'}</td>
        <td>${result}</td>
      </tr>
    `;
  });
  document.getElementById('autoCount').textContent = data.tasks.length;
}

async function clearAutoTasks() {
  // На бэкенде нет эндпоинта, просто перезагрузим
  log('Очистка на стороне сервера не реализована — перезапустите сервер', 'info');
}

async function updateAutoCount() {
  const r = await fetch('/api/auto-record/status');
  const data = await r.json();
  document.getElementById('autoCount').textContent = data.tasks.length;
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

// Фильтры — автообновление
['search','dateFilter','statusFilter','bookableOnly'].forEach(id => {
  document.getElementById(id).addEventListener('input', loadEvents);
  document.getElementById(id).addEventListener('change', loadEvents);
});

// Автообновление
setInterval(loadEvents, 15000);
setInterval(updateAutoCount, 5000);

// Старт
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
    print("Загружаю мероприятия...")
    ok, msg = fetch_events()
    print(f"  {msg}")
    print()
    print("🌐 Открой в браузере:")
    print(f"     http://localhost:6689")
    print(f"     http://<твой_IP>:6689  (для доступа с других устройств)")
    print("=" * 60)
    app.run(host='0.0.0.0', port=6689, debug=False, threaded=True)