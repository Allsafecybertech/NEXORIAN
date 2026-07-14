import sqlite3
from pathlib import Path
import secrets
import datetime

DB_PATH = Path(__file__).resolve().parent / 'data.db'


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS plans (
        id TEXT PRIMARY KEY,
        price REAL,
        daily_quota INTEGER
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT,
        plan_id TEXT,
        token TEXT UNIQUE,
        start_date TEXT,
        end_date TEXT,
        daily_quota INTEGER,
        used_date TEXT,
        used_count INTEGER DEFAULT 0
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS proxies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proxy TEXT UNIQUE,
        last_used TEXT,
        allocated_to TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT,
        plan_id TEXT,
        method TEXT,
        amount REAL,
        status TEXT,
        reference TEXT,
        tx_hash TEXT,
        proof_file TEXT,
        metadata TEXT,
        created_at TEXT
    )
    ''')
    conn.commit()
    add_default_plans(conn)
    conn.close()


def add_default_plans(conn=None):
    close = False
    if conn is None:
        conn = get_conn()
        close = True
    cur = conn.cursor()
    # default plan: 1 proxy per day for $1
    cur.execute('INSERT OR IGNORE INTO plans (id, price, daily_quota) VALUES (?, ?, ?)',
                ('daily1', 1.0, 1))
    cur.execute('INSERT OR IGNORE INTO plans (id, price, daily_quota) VALUES (?, ?, ?)',
                ('weekly5', 5.0, 5))
    conn.commit()
    if close:
        conn.close()


def ensure_user(email):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('INSERT OR IGNORE INTO users (email) VALUES (?)', (email,))
    conn.commit()
    conn.close()


def create_payment(email, plan_id, method, amount, reference=None, metadata=None):
    import datetime
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()
    cur.execute('INSERT INTO payments (email, plan_id, method, amount, status, reference, metadata, created_at) VALUES (?,?,?,?,?,?,?,?)',
                (email, plan_id, method, amount, 'pending', reference, metadata, now))
    conn.commit()
    cur.execute('SELECT * FROM payments WHERE rowid = last_insert_rowid()')
    row = cur.fetchone()
    conn.close()
    return dict(row)


def get_payment(payment_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM payments WHERE id = ?', (payment_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_payment_status(payment_id, status, tx_hash=None, proof_file=None):
    conn = get_conn()
    cur = conn.cursor()
    if tx_hash:
        cur.execute('UPDATE payments SET status = ?, tx_hash = ? WHERE id = ?', (status, tx_hash, payment_id))
    elif proof_file:
        cur.execute('UPDATE payments SET status = ?, proof_file = ? WHERE id = ?', (status, proof_file, payment_id))
    else:
        cur.execute('UPDATE payments SET status = ? WHERE id = ?', (status, payment_id))
    conn.commit()
    cur.execute('SELECT * FROM payments WHERE id = ?', (payment_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_payments_by_email(email):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM payments WHERE email = ? ORDER BY created_at DESC', (email,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_plans():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM plans')
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_plan(plan_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM plans WHERE id = ?', (plan_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def create_subscription(email, plan_id, months=1):
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError('plan not found')
    token = secrets.token_urlsafe(16)
    start = datetime.date.today()
    end = start + datetime.timedelta(days=30*months)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('INSERT INTO subscriptions (email, plan_id, token, start_date, end_date, daily_quota, used_date, used_count) VALUES (?,?,?,?,?,?,?,?)',
                (email, plan_id, token, start.isoformat(), end.isoformat(), plan['daily_quota'], start.isoformat(), 0))
    conn.commit()
    cur.execute('SELECT * FROM subscriptions WHERE token = ?', (token,))
    row = cur.fetchone()
    conn.close()
    return dict(row)


def get_subscription_by_token(token):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM subscriptions WHERE token = ?', (token,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_subscriptions_by_email(email):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM subscriptions WHERE email = ?', (email,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reset_usage_if_needed(sub):
    today = datetime.date.today().isoformat()
    if sub['used_date'] != today:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute('UPDATE subscriptions SET used_date = ?, used_count = 0 WHERE id = ?', (today, sub['id']))
        conn.commit()
        conn.close()
        sub['used_date'] = today
        sub['used_count'] = 0


def allocate_proxy_for_subscription(token):
    sub = get_subscription_by_token(token)
    if not sub:
        return None, 'subscription not found'
    # reset daily usage if day changed
    reset_usage_if_needed(sub)
    if sub['used_count'] >= sub['daily_quota']:
        return None, 'quota exhausted for today'
    conn = get_conn()
    cur = conn.cursor()
    # choose a proxy not recently used
    cur.execute('SELECT * FROM proxies ORDER BY last_used IS NULL DESC, last_used ASC LIMIT 1')
    row = cur.fetchone()
    if not row:
        conn.close()
        return None, 'no proxies available'
    proxy = row['proxy']
    now = datetime.datetime.utcnow().isoformat()
    cur.execute('UPDATE proxies SET last_used = ?, allocated_to = ? WHERE id = ?', (now, token, row['id']))
    cur.execute('UPDATE subscriptions SET used_count = used_count + 1 WHERE id = ?', (sub['id'],))
    conn.commit()
    conn.close()
    return proxy, None


def import_proxies(proxies):
    conn = get_conn()
    cur = conn.cursor()
    for p in proxies:
        p = p.strip()
        if not p:
            continue
        try:
            cur.execute('INSERT OR IGNORE INTO proxies (proxy) VALUES (?)', (p,))
        except Exception:
            pass
    conn.commit()
    conn.close()
