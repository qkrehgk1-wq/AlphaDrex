import json
import hashlib
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from .core import ROOT, Store, Engine, uid, now, metrics, validate_pack
from . import providers
from .handoff import Handoff, public_targets, authenticate_agent
from .accounts import Accounts
from .extract import manual_text


def load_env():
    allowed = {'LINER_API_KEY', 'LINER_MODEL', 'DREX_API_KEY', 'DREX_URL', 'DREX_MODEL', 'ALPHADREX_PORT', 'ALPHADREX_INTERNAL_ONLY', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'ALPHADREX_TELEGRAM_TARGETS', 'ALPHADREX_AGENT_TOKENS'}
    allowed.update({'ALPHADREX_BRAIN', 'ALPHADREX_DREX_BUDGET_S', 'ALPHADREX_SHADOW_RATE', 'ALPHADREX_MAX_WAIT_S'})
    for name in ('env.local', '.env.local', '.env'):
        path = ROOT/name
        if path.is_file():
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                key, sep, value = line.strip().partition('=')
                if sep and key in allowed:
                    os.environ.setdefault(key, value.strip().strip('\"\''))


# Keys are loaded only when the server or a tool starts (main/load_env), never on import: tests import this
# module, and an import-time load put the real Liner key into unit tests (found 2026-11-14).
PORT = int(os.getenv('ALPHADREX_PORT', '8765'))
POLICY = {'mode': 'internal', 'external_enabled': False}
LOCK = threading.RLock()
LAST_TEST = {}
POLICY_REVISION = 0
BUILD_ID = hashlib.sha256(b''.join(
    p.relative_to(ROOT).as_posix().encode() + b'\0' + p.read_bytes()
    for p in sorted((ROOT/'app').rglob('*'))
    if p.suffix in ('.py','.js','.css','.html'))).hexdigest()
# Paths that may wait on Drex/Liner (Drex timeout is 180s). They run outside LOCK on a
# policy snapshot so the rest of the UI keeps responding (requirement: one failing or slow
# feature must not freeze the whole screen).
SLOW_PATHS = {'/api/message', '/api/search', '/api/test', '/api/handoff/send'}
ACTIVE_REQUESTS = {'internal': 0, 'external': 0}


DREX_STATUS = {'checked': 0.0, 'live': None}


def drex_status(max_age=10.0):
    """Cached local liveness for the header light (review B-2); probed outside LOCK, at most every 10s."""
    if time.monotonic() - DREX_STATUS['checked'] > max_age:
        DREX_STATUS.update(live=providers.drex_alive(), checked=time.monotonic())
    return DREX_STATUS['live']


def store(mode=None):
    return Store(mode=mode or POLICY['mode'])


# ---- accounts, permissions, logs (review follow-up 2026-11-17) ------------------------------------------
ACCOUNTS_DIR = ROOT / 'data'
LOG = logging.getLogger('alphadrex')
LOG.addHandler(logging.NullHandler())
LOG.propagate = False
OPEN_USER = {'name': '계정 미설정', 'role': 'admin', 'verified': False}
PUBLIC_GET = {'/api/health', '/api/auth'}
PUBLIC_POST = {'/api/login', '/api/logout', '/api/setup'}
ADMIN_GET = {'/api/export', '/api/backups', '/api/actions', '/api/users'}
ADMIN_POST = {'/api/settings', '/api/manual', '/api/manual/delete', '/api/pack', '/api/data/delete', '/api/backup',
              '/api/restore', '/api/users', '/api/users/disable', '/api/test'}
ACTION_KEYS = ('chat_id', 'turn_id', 'report_id', 'delivery_id', 'id', 'target_id', 'provider', 'mode', 'replaces', 'name', 'role')
LOG_ROUTES = PUBLIC_GET | PUBLIC_POST | ADMIN_GET | ADMIN_POST | SLOW_PATHS | {
    '/', '/app.js', '/style.css', '/handoff.js', '/api/state', '/api/handoff/state',
    '/api/agent/inbox', '/api/agent/claim', '/api/agent/reply', '/api/chat', '/api/chat/tab',
    '/api/chat/delete', '/api/preview', '/api/feedback', '/api/report/create', '/api/report/review',
    '/api/handoff/prepare', '/api/users/password'}


def log_route(path):
    if path in LOG_ROUTES:
        return path
    for prefix in ('/api/chat/', '/api/report/'):
        if path.startswith(prefix):
            return prefix + ':id'
    return '/unknown'


def log_targets(body):
    """Identifiers only; arbitrary request values become fingerprints, never log text."""
    targets = {}
    enums = {'mode': ('internal', 'external'), 'provider': ('drex', 'liner'), 'role': ('admin', 'staff')}
    for key in ACTION_KEYS:
        value = body.get(key) if isinstance(body, dict) else None
        if value is None or value == '':
            continue
        if isinstance(value, str) and (value in enums.get(key, ()) or
                (key.endswith('_id') or key in ('id', 'replaces')) and re.fullmatch(r'[0-9a-f]{32}', value)) \
                and not providers.contains_configured_secret(value):
            targets[key] = value
        else:
            targets[key + '_sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return targets


class AuthError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def accounts():
    return Accounts(ACCOUNTS_DIR)


def accounts_on():
    # Never creates the accounts file: reading state must not write (tests, first run).
    return (ACCOUNTS_DIR / 'accounts.sqlite3').is_file() and accounts().enabled()


def configure_logging(directory=None):
    """Rotating server log: method, path (no query), status, time, actor role and error tracebacks.
    Never request bodies, situations, documents or keys (review A-7)."""
    folder = Path(directory or ROOT / 'data' / 'logs')
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / 'server.log', maxBytes=1_000_000, backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    return folder / 'server.log'


def safe_traceback():
    # Exception messages and source lines may contain raw input or provider responses.
    exc_type, _, tb = sys.exc_info()
    locations = []
    while tb is not None:
        locations.append(f'{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}:{tb.tb_frame.f_code.co_name}')
        tb = tb.tb_next
    return (exc_type.__name__ if exc_type else 'UnknownError') + ' ' + ' -> '.join(locations[-8:])


class Handler(BaseHTTPRequestHandler):
    def agent_identity(self):
        self.guard()
        if self.headers.get('Origin'):
            raise ValueError('에이전트 API는 브라우저에서 호출할 수 없습니다')
        agent_id=self.headers.get('X-AlphaDrex-Agent','')
        mode=self.headers.get('X-AlphaDrex-Mode','')
        authorization=self.headers.get('Authorization','')
        token=authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
        if mode not in ('internal','external') or not authenticate_agent(agent_id,token,mode):
            raise ValueError('에이전트 인증 실패')
        if os.getenv('ALPHADREX_INTERNAL_ONLY')=='1' and mode!='internal':
            raise ValueError('내부망 전용 서버')
        return agent_id,mode

    def log_message(self, *args):
        pass  # Avoid storing situations, credentials, and URL query strings (see configure_logging).

    def reply(self, data, status=200, content_type='application/json; charset=utf-8', headers=None):
        body = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith('application/json') else data
        self._status = status
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def guard(self, mutation=False):
        host = self.headers.get('Host', '')
        if host not in (f'127.0.0.1:{PORT}', f'localhost:{PORT}'):
            raise ValueError('허용되지 않은 호스트')
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'):
            raise ValueError('교차 출처 요청 차단')
        if mutation and (self.headers.get('X-AlphaDrex') != '1' or not self.headers.get('Content-Type', '').startswith('application/json')):
            raise ValueError('잘못된 요청 헤더')

    def session_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except Exception:
            return ''
        return cookie['alphadrex_session'].value if 'alphadrex_session' in cookie else ''

    def authorize(self, path, method):
        """Who is calling, checked before any data is read. Without accounts the app stays open (as before)."""
        if not accounts_on():
            return dict(OPEN_USER)
        if (method == 'GET' and path in PUBLIC_GET) or (method == 'POST' and path in PUBLIC_POST):
            return None
        user = accounts().session(self.session_token())
        if not user:
            raise AuthError(401, '로그인이 필요합니다')
        if (method == 'GET' and path in ADMIN_GET) or (method == 'POST' and path in ADMIN_POST):
            if user['role'] != 'admin':
                raise AuthError(403, '관리자 권한이 필요합니다')
        return dict(user, verified=True)

    def record(self, path, body, result, error=None, actor=None, mode=None):
        """Append-only action log in the current network DB: who, what, which record, outcome. No text."""
        user = actor or getattr(self, '_user', None) or {'name': '로그인 전', 'role': '-', 'verified': False}
        targets = log_targets(body)
        if isinstance(result, dict) and result.get('id') and 'id' not in targets:
            targets.update({'result_' + key: value for key, value in log_targets({'id': result['id']}).items()})
        entry = {'id': uid(), 'created': now(), 'actor': user['name'], 'role': user['role'],
                 'verified': bool(user.get('verified')), 'path': log_route(path), 'targets': targets,
                 'outcome': '성공' if error is None else '거부·오류',
                 'error': (type(error).__name__ if isinstance(error, Exception) else 'rejected') if error is not None else None}
        try:
            store(mode or getattr(self, '_mode', None)).put('action', entry)
        except Exception:
            LOG.error('action log write failed\n%s', safe_traceback())

    def finish_log(self, method, path, started):
        user = getattr(self, '_user', None) or {}
        LOG.info('%s %s %s %.0fms role=%s', method, log_route(path), getattr(self, '_status', '-'),
                 (time.perf_counter() - started) * 1000, user.get('role', '-'))

    def do_GET(self):
        started, path = time.perf_counter(), urlparse(self.path).path
        try:
            self.guard()
            if path == '/api/health':
                return self.reply({'status':'ok','build_id':BUILD_ID,'pid':os.getpid(),'drex_live':drex_status()})
            if path=='/api/agent/inbox':
                agent,mode=self.agent_identity()
                return self.reply({'items':Handoff(store(mode),{'mode':mode,'external_enabled':False}).inbox(agent)})
            static = {'/':'index.html','/app.js':'app.js','/style.css':'style.css','/handoff.js':'handoff.js'}
            if path in static:
                ext = path.rsplit('.',1)[-1]
                mime = {'js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}.get(ext,'text/html; charset=utf-8')
                return self.reply((ROOT/'app/static'/static[path]).read_bytes(),content_type=mime)
            self._user = self.authorize(path, 'GET')
            if path == '/api/auth':
                on = accounts_on()
                user = accounts().session(self.session_token()) if on else None
                return self.reply({'accounts_enabled': on, 'user': user if on else OPEN_USER})
            live = drex_status() if path == '/api/state' else None
            with LOCK:
                self._user = self.authorize(path, 'GET')
                s = store()
                self._mode = s.mode
                if path=='/api/handoff/state':
                    chat_id=parse_qs(urlparse(self.path).query).get('chat_id',[None])[0]
                    return self.reply(Handoff(s,dict(POLICY)).state(chat_id))
                if path.startswith('/api/report/'):
                    return self.reply(s.get('report',path.rsplit('/',1)[1]))
                if path == '/api/state':
                    return self.reply({'version':'0.2.0','policy':POLICY,'internal_locked':os.getenv('ALPHADREX_INTERNAL_ONLY')=='1', 'storage':str(s.path), 'default_pack':s.default_pack(), 'public_sources':json.loads((ROOT/'manuals/public-sources.json').read_text(encoding='utf-8')), 'packs':s.all('pack'), 'manuals':s.all('manual'), 'chats':s.all('chat'), 'metrics':metrics(s), 'user':self._user, 'accounts_enabled':accounts_on(), 'connections':{'drex_model':os.getenv('DREX_MODEL','drex-v1.5'), 'drex_url':os.getenv('DREX_URL','http://127.0.0.1:8000/v1/systemone'), 'drex_live':live, 'liner_key_registered':bool(os.getenv('LINER_API_KEY')), 'liner_requested_model':os.getenv('LINER_MODEL','liner-gpt-router'), 'last_tests':LAST_TEST, 'mcp':'미구현'}})
                if path.startswith('/api/chat/'):
                    id_ = path.rsplit('/',1)[1]
                    return self.reply({'chat': s.get('chat', id_), 'turns': s.by_chat('turn', id_, chronological=True),
                                       'feedback': s.by_chat('feedback', id_), 'revisions': s.by_chat('revision', id_),
                                       'alerts': s.by_chat('alert', id_, chronological=True)})
                if path == '/api/export':
                    self.record(path, {}, None)
                    return self.reply({'mode':POLICY['mode'], 'created':now(), **{k:s.all(k) for k in ('chat','turn','feedback','revision','manual','pack','report','report_review','delivery','receipt','alert','audit','action')}})
                if path == '/api/backups':
                    return self.reply({'mode': s.mode, 'backups': s.backups()})
                if path == '/api/actions':
                    return self.reply({'mode': s.mode, 'actions': s.all('action')[:500]})
                if path == '/api/users':
                    return self.reply({'users': accounts().users() if accounts_on() else []})
            self.reply({'error':'찾을 수 없습니다'},404)
        except AuthError as exc:
            self.reply({'error':str(exc),'login_required':exc.status==401},exc.status)
        except ValueError as exc:
            self.reply({'error':str(exc)},400)
        except Exception:
            LOG.error('GET %s failed\n%s', log_route(path), safe_traceback())
            self.reply({'error':'서버 처리 오류 (민감한 상세정보는 출력하지 않습니다)'},500)
        finally:
            self.finish_log('GET', path, started)

    def do_POST(self):
        started, path, body, agent_identity = time.perf_counter(), urlparse(self.path).path, {}, None
        active_mode = None
        try:
            agent_identity=self.agent_identity() if path.startswith('/api/agent/') else None
            if agent_identity is None:
                self.guard(True)
                self._user = self.authorize(path, 'POST')
            length = int(self.headers.get('Content-Length','0'))
            limit = 12_000_000 if path == '/api/manual' else 2_000_000
            if not 0 < length <= limit:
                raise ValueError('요청 크기 제한: ' + ('12MB' if path == '/api/manual' else '2MB'))
            body = json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ValueError('JSON 객체 필요')
            if agent_identity:
                agent,mode=agent_identity
                actor={'name':'agent:'+agent,'role':'agent','verified':True}
                with LOCK:
                    service=Handoff(store(mode),{'mode':mode,'external_enabled':False})
                    if path=='/api/agent/claim':
                        result=service.claim(agent,body)
                    elif path=='/api/agent/reply':
                        result=service.reply(agent,body)
                    else:
                        raise ValueError('지원하지 않는 에이전트 요청')
                    self.record(path, body, None, actor=actor, mode=mode)
                return self.reply(result)
            if path in PUBLIC_POST:
                return self.account_action(path, body)
            with LOCK:
                self._user = self.authorize(path, 'POST')
                s, policy = store(), dict(POLICY)
                self._mode = s.mode
                if path not in SLOW_PATHS:
                    result = self.action(path, body, s, policy)
                    self.record(path, body, result)
                    return self.reply(result)
                revision = POLICY_REVISION
                policy['outbound_allowed'] = lambda: POLICY_REVISION == revision
                active_mode = s.mode
                ACTIVE_REQUESTS[active_mode] += 1
            result = self.action(path, body, s, policy)
            self.record(path, body, result)
            self.reply(result)
        except AuthError as exc:
            self.record(path, body, None, error=exc, actor={'name':'로그인 전' if exc.status==401 else self.session_name(),'role':'-','verified':False})
            self.reply({'error':str(exc),'login_required':exc.status==401},exc.status)
        except (ValueError, KeyError, TypeError) as exc:
            message = str(exc) if isinstance(exc,ValueError) else '필수 항목 또는 자료 형식 오류'
            if path not in PUBLIC_POST:
                agent_actor = {'name':'agent:'+agent_identity[0],'role':'agent','verified':True} if agent_identity else None
                self.record(path, body, None, error=message, actor=agent_actor, mode=agent_identity[1] if agent_identity else None)
            self.reply({'error':message},400)
        except providers.ProviderError as exc:
            self.record(path, body, None, error=exc)
            self.reply({'error':str(exc)},400)
        except Exception:
            LOG.error('POST %s failed\n%s', log_route(path), safe_traceback())
            self.record(path, body, None, error=RuntimeError())
            self.reply({'error':'서버 처리 오류 (민감한 상세정보는 출력하지 않습니다)'},500)
        finally:
            if active_mode is not None:
                with LOCK:
                    ACTIVE_REQUESTS[active_mode] -= 1
            self.finish_log('POST', path, started)

    def session_name(self):
        user = accounts().session(self.session_token()) if accounts_on() else None
        return user['name'] if user else '로그인 전'

    def account_action(self, path, b):
        cookie = 'alphadrex_session={}; HttpOnly; SameSite=Strict; Path=/; Max-Age={}'
        if path == '/api/setup':
            user = accounts().create_first_admin(b.get('name'), b.get('password'))
            self.record(path, {'name': user['name'], 'role': 'admin'}, None, actor=dict(user, verified=True))
            token, user = accounts().login(user['name'], b.get('password'))
            return self.reply({'user': user}, headers={'Set-Cookie': cookie.format(token, 12*3600)})
        if path == '/api/login':
            name = b.get('name') if isinstance(b.get('name'), str) else ''
            if not accounts_on():
                raise ValueError('아직 계정이 없습니다. 관리자 계정을 먼저 만드세요')
            try:
                token, user = accounts().login(name, b.get('password', ''))
            except ValueError as exc:
                known_name = name if any(u['name'] == name for u in accounts().users()) else '등록되지 않은 계정'
                self.record(path, {'name': name}, None, error=exc, actor={'name': known_name, 'role': '-', 'verified': False})
                raise
            self.record(path, {}, None, actor=dict(user, verified=True))
            return self.reply({'user': user}, headers={'Set-Cookie': cookie.format(token, 12*3600)})
        # logout
        user = accounts().session(self.session_token()) if accounts_on() else None
        if accounts_on():
            accounts().logout(self.session_token())
        if user:
            self.record(path, {}, None, actor=dict(user, verified=True))
        return self.reply({'ok': True}, headers={'Set-Cookie': cookie.format('', 0)})

    def action(self, path, b, s, policy):
        global POLICY_REVISION
        handoff_actions={'/api/report/create':'create_report','/api/report/review':'review',
                         '/api/handoff/prepare':'prepare','/api/handoff/send':'send'}
        if path in handoff_actions:
            if b.get('mode')!=policy['mode']:
                raise ValueError('망 설정이 변경되었습니다. 문서·전달함을 다시 여세요')
            user = getattr(self, '_user', None) or OPEN_USER
            if path == '/api/report/review' and user.get('verified'):
                return Handoff(s,policy).review(dict(b, reviewer=user['name']), verified=True)
            return getattr(Handoff(s,policy),handoff_actions[path])(b)
        if path == '/api/settings':
            if b.get('mode') not in ('internal','external'):
                raise ValueError('망 모드 오류')
            if os.getenv('ALPHADREX_INTERNAL_ONLY')=='1' and b['mode']!='internal':
                raise ValueError('관리자 환경 설정으로 내부망 고정')
            if b.get('default_pack'):
                store(b['mode']).default_pack(b['default_pack'])
            updated = {'mode': b['mode'], 'external_enabled': b['mode'] == 'external' and b.get('external_enabled') is True}
            if updated != POLICY:
                POLICY.update(updated)
                POLICY_REVISION += 1
            return {'ok':True}
        if path == '/api/chat':
            s.get('pack',b['pack_id'])
            return s.put('chat',{'id':uid(),'title':'새 대화','pack_id':b['pack_id'],'created':now(),'open':True})
        if path == '/api/chat/tab':
            return s.set_tab(b['id'], b.get('open') is True)
        if path == '/api/chat/delete':
            s.delete('chat',b['id']); return {'ok':True}
        if path == '/api/preview':
            text = b.get('text','')
            text = text.strip() if isinstance(text,str) else ''
            if not text or len(text)>6000:
                raise ValueError('상황은 1~6000자로 입력하세요')
            return Engine(s,policy).preview(b['chat_id'],text)
        if path == '/api/message':
            text = b.get('text','')
            text = text.strip() if isinstance(text,str) else ''
            if not text or len(text)>6000:
                raise ValueError('상황은 1~6000자로 입력하세요')
            return Engine(s,policy).run(b['chat_id'],text,b.get('allow_external') is True)
        if path == '/api/search':
            if not isinstance(b.get('query',''),str):
                raise ValueError('검색어 형식 오류')
            revision = Engine(s,policy).search_confirmed(b['turn_id'],b.get('query',''),b.get('confirmed') is True)
            LAST_TEST['search'] = {'status': '실제 호출 성공' if revision['search_calls'] and revision['results'] else revision['status'], 'checked': revision['created'], 'wall_ms': revision['search_ms'], 'search_calls': revision['search_calls']}
            return revision
        if path == '/api/feedback':
            t = s.get('turn',b['turn_id'])
            fields = ('category_ok','evidence_ok','search_needed','risk_missed','draft_useful')
            if any(b.get(k) not in ('yes','no','unknown') for k in fields):
                raise ValueError('검토 항목 값 오류')
            if any(not isinstance(b.get(k,''),str) or len(b.get(k,''))>10000 for k in ('correct_category','edited','reason')):
                raise ValueError('검토 내용 길이 오류')
            if b.get('correct_category') not in t['pack_snapshot']['categories']:
                raise ValueError('분류 값 오류')
            if not b.get('reason','').strip():
                raise ValueError('수정·검토 이유를 입력하세요')
            user = getattr(self, '_user', None) or OPEN_USER
            return s.put('feedback',{'id':uid(),'turn_id':t['id'],'chat_id':t['chat_id'],'created':now(),'author':user['name'],'author_verified':bool(user.get('verified')),**{k:b.get(k,'') for k in (*fields,'correct_category','edited','reason')}})
        if path == '/api/manual':
            filename = b.get('filename','')
            if not isinstance(filename, str) or not filename.strip():
                raise ValueError('파일 이름이 없습니다')
            text, extract_note = manual_text(filename, b.get('text'), b.get('data_base64'))
            old = s.get('manual', b['replaces']) if b.get('replaces') else None
            if old and old.get('superseded_by'):
                raise ValueError('이미 다른 개정판으로 교체된 문서입니다. 최신판을 선택하세요')
            if not isinstance(text,str) or not text.strip() or len(text)>300000:
                raise ValueError('텍스트는 1~300000자여야 합니다')
            if b.get('kind') not in ('가상·시연용','공개 자료','내부 자료'):
                raise ValueError('문서 종류 오류')
            if b['kind']=='가상·시연용' and ('가상' not in filename or '가상' not in text):
                raise ValueError('가상 자료는 파일명과 본문에 가상·시연용을 명시하세요')
            pack = s.get('pack',b['pack_id'])
            doc = {k:str(b.get(k,'미확인'))[:1000] for k in ('title','publisher','version','audience','date','url','license','freshness')}
            # Missing group must mean "no conflict group", not the literal '미확인' (which hid the doc from search).
            doc['conflict_group'] = ' '.join(str(b.get('conflict_group') or '').split())[:1000]
            doc.update(id=uid(),filename=Path(filename).name,text=text,kind=b['kind'],checked=now(),approved=b.get('approved') is True,external_approved=b.get('external_approved') is True and b['kind']!='내부 자료',
                       extract_note=extract_note, registered_by=(getattr(self,'_user',None) or OPEN_USER)['name'])
            if old:
                # Revision: the new edition takes the old one's place in every pack; the old edition is kept
                # (earlier answers cite it) but is no longer searched.
                doc['replaces'] = old['id']
                doc['document_family'] = old.get('document_family') or old['title']
                if not doc.get('conflict_group'):
                    doc['conflict_group'] = old.get('conflict_group', '')
                s.put('manual', doc)
                old.update(superseded_by=doc['id'], superseded=now())
                s.put('manual', old)
                for p_ in s.all('pack'):
                    if old['id'] in p_['manuals'] or p_['id'] == pack['id']:
                        p_['manuals'] = [doc['id'] if i == old['id'] else i for i in p_['manuals']]
                        if doc['id'] not in p_['manuals'] and p_['id'] == pack['id']:
                            p_['manuals'].append(doc['id'])
                        s.put('pack', p_)
                return doc
            s.put('manual',doc); pack['manuals'].append(doc['id']); s.put('pack',pack)
            return doc
        if path == '/api/manual/delete':
            s.delete('manual',b['id'])
            for p in s.all('pack'):
                p['manuals'] = [i for i in p['manuals'] if i!=b['id']]; s.put('pack',p)
            return {'ok':True}
        if path == '/api/pack':
            pack = validate_pack(dict(b.get('pack', b)))
            existing = next((p for p in s.all('pack') if p['id'] == pack['id']), None)
            if existing:
                if b.get('overwrite') is not True:
                    raise ValueError('같은 ID의 업무 팩을 교체하려면 확인이 필요합니다')
                pack['manuals'] = list(dict.fromkeys(existing['manuals'] + pack['manuals']))
            available = {doc['id'] for doc in s.all('manual') if not doc.get('superseded_by')}
            pack['manuals'] = [id_ for id_ in pack['manuals'] if id_ in available]
            return s.put('pack', pack)
        if path == '/api/data/delete':
            if b.get('confirm')!='현재 망 데이터 삭제':
                raise ValueError('삭제 확인 문구가 일치하지 않습니다')
            backup = s.clear_data()
            return {'ok':True, 'backup': backup.name}
        if path == '/api/backup':
            return {'ok': True, 'backup': s.backup('manual').name}
        if path == '/api/restore':
            if b.get('confirm') != '백업으로 복원':
                raise ValueError('복원 확인 문구가 일치하지 않습니다')
            if ACTIVE_REQUESTS[s.mode]:
                raise ValueError('처리 중인 요청이 있습니다. 완료된 뒤 복원하세요')
            before = s.restore(b.get('name'))
            return {'ok': True, 'restored': b.get('name'), 'before_restore_backup': before.name}
        if path == '/api/users':
            if not accounts_on():
                raise ValueError('관리자 계정을 먼저 만드세요')
            return accounts().create(b.get('name'), b.get('password'), b.get('role'))
        if path == '/api/users/disable':
            user = getattr(self, '_user', None) or OPEN_USER
            accounts().set_disabled(b.get('name'), b.get('disabled') is True, user['name'])
            return {'ok': True}
        if path == '/api/users/password':
            user = getattr(self, '_user', None) or OPEN_USER
            if not accounts_on():
                raise ValueError('계정이 없습니다')
            if b.get('name') != user['name'] and user['role'] != 'admin':
                raise AuthError(403, '자기 비밀번호만 바꿀 수 있습니다')
            accounts().set_password(b.get('name'), b.get('password'))
            return {'ok': True}
        if path == '/api/test':
            provider = b.get('provider')
            t = time.perf_counter()
            try:
                if provider=='drex':
                    r = providers.drex('가상 시설 A구역 정기 점검을 기록합니다.',s.get('pack',s.default_pack()),policy)
                elif provider=='liner':
                    r = providers.draft('가상 상황 보고 서식을 정리해 주세요.',[{'text':'가상·시연용: 발생 시각과 확인된 사실, 미확인 사항을 나누어 기록한다.','title':'연결 시험용 가상 문서','external_approved':True}],[],policy)
                else:
                    raise ValueError('검색 연결은 대화의 검색어 확인 후 시험하세요')
                result = {'status':'실제 호출 성공','result':r}
            except providers.ProviderError as exc:
                result = {'status':str(exc)}
            result.update(checked=now(),wall_ms=round((time.perf_counter()-t)*1000,2))
            LAST_TEST[provider] = result
            return result
        raise ValueError('지원하지 않는 요청')


def main():
    global PORT
    load_env()
    PORT = int(os.getenv('ALPHADREX_PORT', '8765'))
    configure_logging()
    for mode in ('internal', 'external'):
        try:
            made = Store(mode=mode).daily_backup()
            if made:
                LOG.info('daily backup %s', made.name)
        except Exception:
            LOG.error('daily backup failed %s', safe_traceback())
    store()
    LOG.info('server start port=%s build=%s accounts=%s', PORT, BUILD_ID[:8], accounts_on())
    server = ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    print(f'AlphaDrex http://127.0.0.1:{PORT} · 종료 Ctrl+C',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == '__main__':
    main()
