"""Password accounts and opaque server-side login sessions."""
import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from fastapi import HTTPException, Request

DB_PATH = Path(__file__).resolve().parents[1] / 'data' / 'accounts.sqlite3'
COOKIE = 'deepsearch_session'

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS logins (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL)')
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def login(name, password, register=False):
    name = name.strip()
    if not 2 <= len(name) <= 80 or not 8 <= len(password) <= 256:
        raise HTTPException(400, '用户名需为2–80个字符，密码需为8–256个字符')
    with connect() as conn:
        if register:
            salt = secrets.token_hex(16)
            digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
            try:
                conn.execute('INSERT INTO users VALUES (?,?,?,?)', (secrets.token_hex(16), name, salt, digest))
            except sqlite3.IntegrityError:
                raise HTTPException(409, '用户名已存在')
        row = conn.execute('SELECT id,salt,password FROM users WHERE name=?', (name,)).fetchone()
        salt = row[1] if row else '00' * 16
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        if not row or not hmac.compare_digest(row[2], digest):
            raise HTTPException(401, '用户名或密码错误')
        token = secrets.token_urlsafe(32)
        conn.execute('INSERT INTO logins VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row[0], time.time()+30*86400))
    return token

def identify(token):
    if not token:
        raise HTTPException(401, '请先登录')
    with connect() as conn:
        row = conn.execute('SELECT user_id FROM logins WHERE token=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
    if not row:
        raise HTTPException(401, '登录已过期，请重新登录')
    return row[0]

def current_user(request: Request):
    return identify(request.cookies.get(COOKIE))
