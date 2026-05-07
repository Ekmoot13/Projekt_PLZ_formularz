from flask import Flask, request, jsonify, Response, session
from functools import wraps
from prometheus_flask_exporter import PrometheusMetrics
import psycopg2
import psycopg2.extras
import os
import time
import json
from datetime import datetime

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://localhost/formularz')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-fallback-key')
metrics = PrometheusMetrics(app)

ADMIN_USER = os.getenv('ADMIN_USER', 'admin')
ADMIN_PASS = os.getenv('ADMIN_PASS', 'admin')

# ── Auth ──────────────────────────────────────────────────

def check_auth(username, password):
    return username == ADMIN_USER and password == ADMIN_PASS

def authenticate():
    return Response(
        'Brak dostępu. Zaloguj się.\n', 401,
        {'WWW-Authenticate': 'Basic realm="Kapitanat PLŻ"'}
    )

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # 1) Basic Auth
        auth = request.authorization
        if auth and check_auth(auth.username, auth.password):
            session['admin'] = True
            return f(*args, **kwargs)
        # 2) Session (po zalogowaniu przez /admin)
        if session.get('admin'):
            return f(*args, **kwargs)
        return authenticate()
    return decorated

# ── DB ────────────────────────────────────────────────────

def get_db():
    url = DATABASE_URL
    # Railway używa "postgres://" — psycopg2 wymaga "postgresql://"
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(url)

def init_db():
    retries = 8
    while retries > 0:
        try:
            conn = get_db()
            cur = conn.cursor()

            # Listy klubów
            cur.execute('''
                CREATE TABLE IF NOT EXISTS club_lists (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    clubs JSONB NOT NULL DEFAULT '[]',
                    active BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')

            # Listy dań
            cur.execute('''
                CREATE TABLE IF NOT EXISTS meal_lists (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    days JSONB NOT NULL DEFAULT '[]',
                    active BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')

            # Ustawienia (klucz-wartość: hashtag itp.)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
            ''')

            # Zamówienia
            cur.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    club_name TEXT NOT NULL,
                    hashtag TEXT DEFAULT '',
                    orders JSONB NOT NULL DEFAULT '{}',
                    edit_history JSONB NOT NULL DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            # Migracja: dodaj kolumny jeśli tabela istniała bez nich
            cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS edit_history JSONB NOT NULL DEFAULT '[]'")
            cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

            # Dane domyślne – lista klubów
            cur.execute("SELECT COUNT(*) FROM club_lists")
            if cur.fetchone()[0] == 0:
                default_clubs = json.dumps([
                    {"name": "Klub 1", "count": 10, "password": "haslo1"},
                    {"name": "Klub 2", "count": 8,  "password": "haslo2"},
                    {"name": "Klub 3", "count": 6,  "password": "haslo3"}
                ])
                cur.execute(
                    "INSERT INTO club_lists (name, clubs, active) VALUES (%s, %s, TRUE)",
                    ("Lista domyślna", default_clubs)
                )

            # Dane domyślne – lista dań
            cur.execute("SELECT COUNT(*) FROM meal_lists")
            if cur.fetchone()[0] == 0:
                default_days = json.dumps([
                    {"day": "Dzień 1", "dishes": ["kotlet", "pierogi"]},
                    {"day": "Dzień 2", "dishes": ["szpinak", "jabłko"]}
                ])
                cur.execute(
                    "INSERT INTO meal_lists (name, days, active) VALUES (%s, %s, TRUE)",
                    ("Menu domyślne", default_days)
                )

            # Domyślny hashtag
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('hashtag', '') ON CONFLICT (key) DO NOTHING"
            )

            conn.commit()
            cur.close()
            conn.close()
            print("Baza danych gotowa!")
            break
        except Exception as e:
            print(f"Baza niegotowa, próbuję ponownie... {e}")
            time.sleep(2)
            retries -= 1

init_db()

# ── Strony ────────────────────────────────────────────────

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/formularz')
def formularz():
    return app.send_static_file('form.html')

@app.route('/admin')
def admin():
    auth = request.authorization
    if auth and check_auth(auth.username, auth.password):
        session['admin'] = True
        return app.send_static_file('admin.html')
    return authenticate()

# ── Club Lists (admin) ────────────────────────────────────

@app.route('/api/club-lists', methods=['GET', 'POST'])
@requires_auth
def handle_club_lists():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    if request.method == 'GET':
        cur.execute('SELECT id, name, clubs, active, created_at FROM club_lists ORDER BY created_at DESC')
        rows = cur.fetchall()
        result = [{
            "id": r['id'], "name": r['name'],
            "clubs": r['clubs'], "active": r['active'],
            "created_at": r['created_at'].isoformat() if r['created_at'] else None
        } for r in rows]
        cur.close(); conn.close()
        return jsonify(result)

    elif request.method == 'POST':
        data = request.json
        name  = data.get('name', 'Nowa lista')
        clubs = data.get('clubs', [])
        cur.execute(
            "INSERT INTO club_lists (name, clubs, active) VALUES (%s, %s, FALSE) RETURNING id",
            (name, json.dumps(clubs))
        )
        new_id = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return jsonify({"id": new_id, "name": name, "clubs": clubs, "active": False}), 201

@app.route('/api/club-lists/<int:list_id>', methods=['PUT', 'DELETE'])
@requires_auth
def handle_club_list(list_id):
    conn = get_db()
    cur = conn.cursor()

    if request.method == 'PUT':
        data  = request.json
        name  = data.get('name')
        clubs = data.get('clubs')
        cur.execute(
            "UPDATE club_lists SET name = %s, clubs = %s WHERE id = %s",
            (name, json.dumps(clubs), list_id)
        )
        conn.commit(); cur.close(); conn.close()
        return jsonify({"status": "updated"})

    elif request.method == 'DELETE':
        cur.execute('DELETE FROM club_lists WHERE id = %s', (list_id,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"status": "deleted"})

@app.route('/api/club-lists/<int:list_id>/activate', methods=['POST'])
@requires_auth
def activate_club_list(list_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE club_lists SET active = FALSE")
    cur.execute("UPDATE club_lists SET active = TRUE WHERE id = %s", (list_id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"status": "activated"})

# ── Meal Lists (admin) ────────────────────────────────────

@app.route('/api/meal-lists', methods=['GET', 'POST'])
@requires_auth
def handle_meal_lists():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    if request.method == 'GET':
        cur.execute('SELECT id, name, days, active, created_at FROM meal_lists ORDER BY created_at DESC')
        rows = cur.fetchall()
        result = [{
            "id": r['id'], "name": r['name'],
            "days": r['days'], "active": r['active']
        } for r in rows]
        cur.close(); conn.close()
        return jsonify(result)

    elif request.method == 'POST':
        data = request.json
        name = data.get('name', 'Nowe menu')
        days = data.get('days', [])
        cur.execute(
            "INSERT INTO meal_lists (name, days, active) VALUES (%s, %s, FALSE) RETURNING id",
            (name, json.dumps(days))
        )
        new_id = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return jsonify({"id": new_id, "name": name, "days": days, "active": False}), 201

@app.route('/api/meal-lists/<int:list_id>', methods=['PUT', 'DELETE'])
@requires_auth
def handle_meal_list(list_id):
    conn = get_db()
    cur = conn.cursor()

    if request.method == 'PUT':
        data = request.json
        cur.execute(
            "UPDATE meal_lists SET name = %s, days = %s WHERE id = %s",
            (data.get('name'), json.dumps(data.get('days', [])), list_id)
        )
        conn.commit(); cur.close(); conn.close()
        return jsonify({"status": "updated"})

    elif request.method == 'DELETE':
        cur.execute('DELETE FROM meal_lists WHERE id = %s', (list_id,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"status": "deleted"})

@app.route('/api/meal-lists/<int:list_id>/activate', methods=['POST'])
@requires_auth
def activate_meal_list(list_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE meal_lists SET active = FALSE")
    cur.execute("UPDATE meal_lists SET active = TRUE WHERE id = %s", (list_id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"status": "activated"})

# ── Dane publiczne (bez auth) ─────────────────────────────

@app.route('/api/active/clubs', methods=['GET'])
def get_active_clubs():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute('SELECT clubs FROM club_lists WHERE active = TRUE LIMIT 1')
    r = cur.fetchone()
    cur.close(); conn.close()
    if not r:
        return jsonify([])
    # Zwróć nazwy BEZ haseł
    clubs = [{"name": c['name'], "count": c.get('count', 0)} for c in (r['clubs'] or [])]
    return jsonify(clubs)

@app.route('/api/active/meals', methods=['GET'])
def get_active_meals():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute('SELECT days FROM meal_lists WHERE active = TRUE LIMIT 1')
    r = cur.fetchone()
    cur.close(); conn.close()
    if not r:
        return jsonify([])
    return jsonify(r['days'] or [])

# ── Hashtag ───────────────────────────────────────────────

@app.route('/api/hashtag', methods=['GET'])
def get_hashtag():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = 'hashtag'")
    r = cur.fetchone()
    cur.close(); conn.close()
    return jsonify({"hashtag": r[0] if r else ""})

@app.route('/api/hashtag', methods=['POST'])
@requires_auth
def set_hashtag():
    value = request.json.get('hashtag', '')
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES ('hashtag', %s) ON CONFLICT (key) DO UPDATE SET value = %s",
        (value, value)
    )
    conn.commit(); cur.close(); conn.close()
    return jsonify({"status": "saved", "hashtag": value})

# ── Weryfikacja klubu ─────────────────────────────────────

@app.route('/api/verify-club', methods=['POST'])
def verify_club():
    data      = request.json
    club_name = data.get('club_name', '')
    password  = data.get('password', '')

    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute('SELECT clubs FROM club_lists WHERE active = TRUE LIMIT 1')
    r = cur.fetchone()
    cur.close(); conn.close()

    if not r:
        return jsonify({"valid": False, "error": "Brak aktywnej listy klubów"})

    for club in (r['clubs'] or []):
        if club['name'] == club_name and club.get('password', '') == password:
            # Sprawdź czy istnieje już zamówienie dla tego klubu i hashtagu
            conn2 = get_db()
            cur2  = conn2.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur2.execute("SELECT value FROM settings WHERE key = 'hashtag'")
            ht = cur2.fetchone()
            hashtag = ht[0] if ht else ''

            cur2.execute(
                'SELECT id, orders FROM orders WHERE club_name = %s AND hashtag = %s ORDER BY created_at DESC LIMIT 1',
                (club_name, hashtag)
            )
            existing = cur2.fetchone()
            cur2.close(); conn2.close()

            return jsonify({
                "valid": True,
                "club_name": club['name'],
                "athlete_count": club.get('count', 4),
                "has_existing_order": existing is not None,
                "existing_orders": existing['orders'] if existing else {}
            })

    return jsonify({"valid": False, "error": "Nieprawidłowe hasło"})

# ── Złóż / zaktualizuj zamówienie (upsert) ────────────────

@app.route('/api/submit', methods=['POST'])
def submit():
    data        = request.json
    club_name   = data.get('club_name', '')
    orders_data = data.get('orders', {})

    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Pobierz bieżący hashtag
    cur.execute("SELECT value FROM settings WHERE key = 'hashtag'")
    r = cur.fetchone()
    hashtag = r[0] if r else ''

    # Sprawdź czy istnieje zamówienie dla tego klubu + hashtag
    cur.execute(
        'SELECT id, orders, edit_history FROM orders WHERE club_name = %s AND hashtag = %s ORDER BY created_at DESC LIMIT 1',
        (club_name, hashtag)
    )
    existing = cur.fetchone()

    now_iso = datetime.now().isoformat(timespec='seconds')

    if existing:
        # Zapisz poprzednią wersję do historii
        history = list(existing['edit_history'] or [])
        history.append({
            "version":   len(history) + 1,
            "orders":    existing['orders'],
            "edited_at": now_iso
        })
        cur.execute(
            '''UPDATE orders SET orders = %s, edit_history = %s, updated_at = NOW()
               WHERE id = %s''',
            (json.dumps(orders_data), json.dumps(history), existing['id'])
        )
        status = "updated"
    else:
        cur.execute(
            'INSERT INTO orders (club_name, hashtag, orders, edit_history) VALUES (%s, %s, %s, %s)',
            (club_name, hashtag, json.dumps(orders_data), json.dumps([]))
        )
        status = "created"

    conn.commit(); cur.close(); conn.close()
    return jsonify({"status": status}), 201

# ── Zamówienia (admin) ────────────────────────────────────

@app.route('/api/submissions', methods=['GET'])
@requires_auth
def get_submissions():
    search         = request.args.get('search', '').strip()
    club_filter    = request.args.get('club', '').strip()
    hashtag_filter = request.args.get('hashtag', '').strip()

    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    query  = 'SELECT id, club_name, hashtag, orders, edit_history, created_at, updated_at FROM orders WHERE TRUE'
    params = []

    if search:
        query += ' AND club_name ILIKE %s'
        params.append(f'%{search}%')
    if club_filter:
        query += ' AND club_name = %s'
        params.append(club_filter)
    if hashtag_filter:
        query += ' AND hashtag = %s'
        params.append(hashtag_filter)

    query += ' ORDER BY created_at DESC'
    cur.execute(query, params)
    rows = cur.fetchall()

    result = [{
        "id":           r['id'],
        "club_name":    r['club_name'],
        "hashtag":      r['hashtag'],
        "orders":       r['orders'],
        "edit_history": r['edit_history'] or [],
        "date":         r['created_at'].strftime("%Y-%m-%d %H:%M") if r['created_at'] else None,
        "updated_at":   r['updated_at'].strftime("%Y-%m-%d %H:%M") if r.get('updated_at') else None
    } for r in rows]

    # Unikalne kluby i hashtagi do filtrów
    cur.execute('SELECT DISTINCT club_name FROM orders ORDER BY club_name')
    clubs = [row[0] for row in cur.fetchall()]

    cur.execute("SELECT DISTINCT hashtag FROM orders WHERE hashtag != '' ORDER BY hashtag")
    hashtags = [row[0] for row in cur.fetchall()]

    cur.close(); conn.close()
    return jsonify({"orders": result, "clubs": clubs, "hashtags": hashtags})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
