"""Local accounts and sessions (review follow-up B-6/A-6, 2026-11-17).

Kept in its own file (data/accounts.sqlite3), separate from the internal/external network DBs: it holds only
user names, roles, salted PBKDF2 password hashes and hashed session tokens, never situations or documents.

Until the first administrator is created the app stays open exactly as before (single-PC demo). Once any
account exists, every screen API needs a valid session and administrative actions need the admin role.
"""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ITERATIONS = 240_000
SESSION_SECONDS = 12 * 3600
LOCK_AFTER, LOCK_SECONDS = 5, 60
NAME = re.compile(r'[0-9A-Za-z가-힣._-]{2,30}')
ROLES = ('admin', 'staff')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(password, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations).hex()


def _token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class Accounts:
    def __init__(self, directory):
        self.path = Path(directory) / 'accounts.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY, role TEXT NOT NULL, salt TEXT NOT NULL,
                hash TEXT NOT NULL, iterations INTEGER NOT NULL, created TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, name TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS login_failures(name_hash TEXT PRIMARY KEY, count INTEGER NOT NULL, first REAL NOT NULL);
            ''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    # ---- users -------------------------------------------------------------------------------------
    def enabled(self):
        with self.db() as db:
            return db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None

    def users(self):
        with self.db() as db:
            return [dict(name=n, role=r, created=c, disabled=bool(d))
                    for n, r, c, d in db.execute('SELECT name,role,created,disabled FROM users ORDER BY created')]

    @staticmethod
    def _check(name, password, role):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('이름은 2~30자의 한글·영문·숫자로 입력하세요')
        if not isinstance(password, str) or len(password) < 8 or len(password) > 200:
            raise ValueError('비밀번호는 8자 이상으로 정하세요')
        if role not in ROLES:
            raise ValueError('권한 값 오류')

    def create(self, name, password, role):
        self._check(name, password, role)
        salt = secrets.token_bytes(16)
        with self.db() as db:
            if db.execute('SELECT 1 FROM users WHERE name=?', (name,)).fetchone():
                raise ValueError('이미 있는 이름입니다')
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?,0)', (name, role, salt.hex(), _hash(password, salt), ITERATIONS, _now()))
        return dict(name=name, role=role)

    def create_first_admin(self, name, password):
        """Check and create in one transaction, including simultaneous first-run requests."""
        self._check(name, password, 'admin')
        salt = secrets.token_bytes(16)
        password_hash = _hash(password, salt)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise ValueError('이미 관리자가 있습니다. 관리자에게 계정을 요청하세요')
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?,0)',
                       (name, 'admin', salt.hex(), password_hash, ITERATIONS, _now()))
        return dict(name=name, role='admin')

    def set_password(self, name, password):
        self._check(name, password, 'staff')
        salt = secrets.token_bytes(16)
        with self.db() as db:
            if not db.execute('UPDATE users SET salt=?, hash=?, iterations=? WHERE name=?',
                              (salt.hex(), _hash(password, salt), ITERATIONS, name)).rowcount:
                raise ValueError('없는 계정입니다')
            db.execute('DELETE FROM sessions WHERE name=?', (name,))

    def set_disabled(self, name, disabled, acting):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT role FROM users WHERE name=?', (name,)).fetchone()
            if not row:
                raise ValueError('없는 계정입니다')
            if disabled and name == acting:
                raise ValueError('자기 계정은 중지할 수 없습니다')
            if disabled and row[0] == 'admin' and db.execute(
                    "SELECT count(*) FROM users WHERE role='admin' AND disabled=0").fetchone()[0] <= 1:
                raise ValueError('마지막 관리자는 중지할 수 없습니다')
            db.execute('UPDATE users SET disabled=? WHERE name=?', (int(bool(disabled)), name))
            if disabled:
                db.execute('DELETE FROM sessions WHERE name=?', (name,))

    # ---- sessions ----------------------------------------------------------------------------------
    def login(self, name, password):
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(password, str) or len(password) > 200:
            raise ValueError('이름 또는 비밀번호가 맞지 않습니다')
        # Accounts objects are recreated per request. Persist throttling in the local accounts DB;
        # a transaction also prevents parallel requests from bypassing the failure counter.
        stamp, name_hash, error, result = time.time(), _token_hash(name), None, None
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM login_failures WHERE first<=?', (stamp - LOCK_SECONDS,))
            failure = db.execute('SELECT count,first FROM login_failures WHERE name_hash=?', (name_hash,)).fetchone()
            count, first = failure or (0, stamp)
            if count >= LOCK_AFTER:
                raise ValueError('로그인 실패가 많아 1분 동안 잠겼습니다')
            row = db.execute('SELECT role,salt,hash,iterations,disabled FROM users WHERE name=?', (name,)).fetchone()
            ok = bool(row) and not row[4] and hmac.compare_digest(_hash(password, bytes.fromhex(row[1]), row[3]), row[2])
            if not ok:
                db.execute('INSERT OR REPLACE INTO login_failures VALUES (?,?,?)', (name_hash, count + 1, first))
                error = '이름 또는 비밀번호가 맞지 않습니다'
            else:
                db.execute('DELETE FROM login_failures WHERE name_hash=?', (name_hash,))
                token = secrets.token_urlsafe(32)
                db.execute('DELETE FROM sessions WHERE expires<?', (stamp,))
                db.execute('INSERT INTO sessions VALUES (?,?,?)', (_token_hash(token), name, stamp + SESSION_SECONDS))
                result = token, dict(name=name, role=row[0])
        if error:
            raise ValueError(error)  # outside the transaction: keep the failed-attempt count
        return result

    def session(self, token):
        if not token:
            return None
        with self.db() as db:
            row = db.execute('''SELECT u.name,u.role FROM sessions s JOIN users u ON u.name=s.name
                                WHERE s.token_hash=? AND s.expires>? AND u.disabled=0''', (_token_hash(token), time.time())).fetchone()
        return dict(name=row[0], role=row[1]) if row else None

    def logout(self, token):
        if token:
            with self.db() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (_token_hash(token),))
