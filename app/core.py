import json
import math
import os
import re
import sqlite3
import time
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from . import providers

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def dumps(x):
    return json.dumps(x, ensure_ascii=False)


def document_family(doc):
    """Conservative version-family key; versions are not automatically authoritative."""
    title = str(doc.get('title') or '').strip()
    return re.sub(r'\s*(?:판\s*|[vV](?:ersion)?\s*|제\s*)\d+(?:\.\d+)*(?:\s*판)?$', '', title).strip()


# Server-side life-safety guard for the rule path. The pack's risk_terms are single words; real
# reports often describe an emergency without them ("쓰러져 대답이 없다"). Over-triggering is
# preferred to missing an emergency (requirement 4), so these patterns are broad on purpose.
LIFE_SAFETY_PATTERNS = [
    r'쓰러(?:졌|져|짐|진)', r'의식(?:이|을)?\s*(?:없|잃|흐려)', r'(?:불러도|말을\s*걸어도)\s*(?:대답|반응)', r'(?:대답|반응)(?:이|을)?\s*없',
    r'숨(?:을|이)?\s*(?:잘\s*)?(?:못|안)\s*쉬', r'호흡(?:이|을)?\s*(?:곤란|없|힘들|어렵|못)',
    r'피(?:가|를)\s*(?:많이\s*)?(?:나|흘)', r'출혈', r'불꽃', r'스파크', r'(?:타는|탄)\s*냄새', r'불(?:이|길이)\s*(?:났|붙|번지)', r'합선',
    r'(?:엘리베이터|승강기).{0,25}(?:멈췄|멈춰|서\s*버렸|안\s*움직).{0,30}(?:안에|사람|두드리|소리)',
    r'(?:타일|천장|외벽|구조물|간판|유리|난간).{0,20}(?:떨어(?:져|졌|지)|무너|내려앉|깨져).{0,25}(?:사람|머리|옆|다쳤|맞)',
    r'(?:사람|방문객|관람객|동료|작업자|직원|아이)(?:이|가|은|는)?[^.!?\n]{0,24}(?:난간|발판|창문)(?:\s*(?:밖|바깥|외측|끝|쪽))?\s*(?:에|에서|으로)?\s*매달(?:려|린\s*채|렸)[^.!?\n]{0,32}(?:떨어질|추락할|손을 놓칠)',
    r'물(?:이)?\s*(?:쏟아|새|흘러).{0,30}(?:조명|전기|콘센트|전선|탁탁|불꽃|깜빡)',
]
# Words that look like risk_terms but describe something else ("행사가 연기되었다" = postponed).
NOT_RISK_PATTERNS = [
    r'연기(?:되|됐|된|하기로|해서|하자|를\s*결정)', r'긴급(?:한\s*일은|하지는|하진|하지)\s*(?:아니|않)',
    r'(?:지진|화재)\s*(?:대피\s*)?(?:교육|훈련)\s*(?:일정|계획|안내)', r'가스\s*(?:요금|고지서|검침)',
    r'(?:지진|화재)(?=\s*(?:대피\s*)?훈련(?:은|이|을|도)?[^.!?\n]{0,24}(?:끝났|종료됐|종료되었|마쳤))',
]


# Function/admin words that appear in almost any request or manual line. They carry no topic, and
# matching them alone made unrelated questions look evidenced (out-of-scope → never '근거 부족').
# Deliberately NOT domain nouns (교대, 행사, 시설 ...), which would hide real evidence.
GENERIC_STOPWORDS = {'다음', '기관', '방법', '경우', '관련', '내용', '사항', '처리', '절차', '정리', '진행', '필요', '이번',
                     '해당', '업무', '현장', '근무', '시간', '확인', '기록', '주세요', '알려', '무엇', '어떻게',
                     '궁금합니다', '모르겠습니다'}

# '오늘' appears in almost every field narrative ("오늘 오후 2시"); alone it does not mean the user
# needs current public information. It counts only next to an information-seeking word.
WEAK_SEARCH_TERMS = {'오늘'}
SEARCH_CONTEXT = r'발표|공지|특보|예보|날씨|기상|뉴스|공개\s*(?:자료|정보)'
SEARCH_CHANGE = r'(?:새로|요즘|올해|이번에).{0,15}(?:바뀐|바뀌었|달라졌|달라진|개정|나왔|발표)|(?:바뀌었|달라졌|개정됐|새로\s*나왔)(?:는지|다는|다던)'


def search_needed(text, terms):
    strong = [t for t in terms if t not in WEAK_SEARCH_TERMS]
    weak = [t for t in terms if t in WEAK_SEARCH_TERMS]
    return (any(t in text for t in strong) or (any(t in text for t in weak) and bool(re.search(SEARCH_CONTEXT, text)))
            or bool(re.search(SEARCH_CHANGE, text)))


def life_safety_signal(text):
    return any(re.search(pattern, text) for pattern in LIFE_SAFETY_PATTERNS)


def risk_term_hit(text, terms):
    cleaned = text
    for pattern in NOT_RISK_PATTERNS:
        cleaned = re.sub(pattern, ' ', cleaned)
    return any(t in cleaned for t in terms)


# Vague hand-me-down requests ("아까 그거 정리해 둬") with no task details.
VAGUE_PATTERNS = [
    r'(?:아까|그|저)\s*(?:그거|그것|건|일|쪽)(?:은|는|을|를|좀|도)?.{0,40}(?:모르|못\s*들|들은\s*(?:내용|게|것)(?:이)?\s*없|말(?:만)?\s*하고)',
    r'자세한\s*(?:이야기|내용|상황|설명)(?:은|는|을|를)?\s*(?:듣지\s*못|못\s*들|모르|없)', r'더\s*들은\s*(?:내용|게|것)(?:이|은)?\s*없',
    r'(?:알아서\s*하라|뭔지|무엇을\s*말하는지)(?:고|는)?.{0,20}(?:모르|퇴근|했습니다|하고)',
]


def vague_request(text):
    return any(re.search(pattern, text) for pattern in VAGUE_PATTERNS)


def explicitly_missing_context(text):
    # A missing incidental fact does not imply that the whole task is unspecified.
    for sentence in re.split(r'[.!?\n]', text):
        if not re.search(r'모르|모릅|없|미확인|알 수 없|알아듣지\s*못|못\s*알아듣|못\s*들|확인(?:을)?\s*(?:하지\s*못|못\s*했)', sentence):
            continue
        dimensions = [r'어디|장소|위치|어느\s*구역', r'언제|시각|시간', r'무슨|무엇을|어떤 일|상황|내용']
        if sum(bool(re.search(pattern, sentence)) for pattern in dimensions) >= 2:
            return True
    return False


# Words that talk about the answer itself (the manual, the evidence, the steps), not about a work topic.
META_WORDS = {'매뉴얼', '근거', '문서', '자료', '기준', '설명', '부분', '항목', '사항', '방법', '절차', '순서', '조치',
              '지금', '먼저', '바로', '함께', '우선', '어떤', '어느', '무슨'}
# Misreading a noun as a verb only restores the earlier behaviour (keep the evidence), the safe side.
VERB_ENDING = re.compile(r'(?:요|다|까|면|고|서|며|게|지|해|할|한|된|될|는|던|야|죠|네|나|기|운|어|아|라|려|워)$')
PARTICLE = re.compile(r'(?:에서는|에게서|에게|에는|으로|에서|부터|까지|처럼|이나|에|을|를|은|는|이|가|과|와|도|로|의|만)$')


def has_own_topic(sentence):
    """True when a request sentence names a subject of its own ("법인카드 증빙은 언제 내나요?").
    A generic closing ("어떻게 처리하면 될까요?", "매뉴얼에 따라 해야 할 일을 알려 주세요") has none and
    refers back to the situation described before it (2026-11-16: treating such closings as the whole
    request erased evidence for answerable narrative questions)."""
    for token in re.findall(r'[가-힣A-Za-z0-9]{2,}', sentence.lower()):
        stem = PARTICLE.sub('', token) if re.fullmatch(r'[가-힣]+', token) else token
        if len(stem) < 2 or stem in GENERIC_STOPWORDS or stem in META_WORDS:
            continue
        if stem == token and VERB_ENDING.search(token):
            continue  # a verb/adjective form, not a noun
        return True
    return False


def public_search_query(text):
    topics = [topic for topic in ('승강기', '지진', '호우', '폭염') if topic in text]
    if not topics:
        return '행정안전부 현장 안전 공개 행동요령 최신'
    return '행정안전부 ' + ' '.join(topics) + ' 국민행동요령 최신'


def validate_pack(p):
    for k in ('id', 'name', 'version', 'description', 'template'):
        if not isinstance(p.get(k), str) or not p[k].strip() or len(p[k]) > 4000:
            raise ValueError('업무 팩 필수 문자열 오류: ' + k)
    for k in ('categories', 'risk_terms', 'search_terms', 'additional_questions', 'manuals', 'examples', 'question_types'):
        if not isinstance(p.get(k), list) or len(p[k]) > 100 or any(not isinstance(x, str) or len(x) > 1000 for x in p[k]):
            raise ValueError('업무 팩 목록 오류: ' + k)
    if not 2 <= len(p['categories']) <= 20 or not isinstance(p.get('keywords'), dict):
        raise ValueError('업무 팩 분류 기준 오류')
    if 'fallback_category' in p and p['fallback_category'] not in p['categories']:
        raise ValueError('대체 분류는 팩 분류 목록에 있어야 합니다')
    for k, v in p['keywords'].items():
        if k not in p['categories'] or not isinstance(v, list) or any(not isinstance(x, str) for x in v):
            raise ValueError('업무 팩 키워드 오류')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}', p['id']):
        raise ValueError('업무 팩 ID 오류')
    return p


class Store:
    def __init__(self, directory=None, mode='internal'):
        self.directory = Path(directory or ROOT / 'data')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.path = self.directory / (mode + '.sqlite3')
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS items(kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE INDEX IF NOT EXISTS items_by_chat ON items(kind,json_extract(body,'$.chat_id'));
            CREATE INDEX IF NOT EXISTS delivery_inbox ON items(kind,json_extract(body,'$.target.id'),json_extract(body,'$.status'));
            CREATE INDEX IF NOT EXISTS report_relations ON items(kind,json_extract(body,'$.report_id'));
            ''')
            if not db.execute("SELECT 1 FROM meta WHERE key='seeded'").fetchone():
                pack = json.loads((ROOT/'packs/field.json').read_text(encoding='utf-8-sig'))
                db.execute('INSERT INTO items VALUES (?,?,?)', ('pack', pack['id'], dumps(pack)))
                names = [('demo-inspection', '가상·시연용-현장점검.md', ''), ('demo-report', '가상·시연용-상황보고.md', ''), ('demo-conflict-a', '가상·시연용-충돌A.md', '출입 승인'), ('demo-conflict-b', '가상·시연용-충돌B.md', '출입 승인')]
                for id_, name, conflict in names:
                    doc = dict(id=id_, title=name, filename=name, text=(ROOT/'manuals'/name).read_text(encoding='utf-8'), kind='가상·시연용', publisher='AlphaDrex 창작', version='0.1.0', audience='가상 현장 담당자', date='2026-11-12', checked='2026-11-12', url='', license='프로젝트 자체 작성', freshness='시연용', approved=True, external_approved=True, conflict_group=conflict)
                    db.execute('INSERT INTO items VALUES (?,?,?)', ('manual', id_, dumps(doc)))
                db.execute("INSERT INTO meta VALUES ('seeded','1')")
            generation = db.execute("SELECT value FROM meta WHERE key='restore_generation'").fetchone()
            self.generation = generation[0] if generation else '0'
        self.recover_stuck_sending()

    def recover_stuck_sending(self, max_age=60):
        """Review A-5: a crash mid-send left 'sending' forever, which blocked deleting that chat and all data.
        A send older than max_age seconds (Telegram's own timeout is 25 s) cannot still be in flight, so it is
        marked 'unknown' for a person to check at the receiver. Never re-sent automatically."""
        with self.db() as db:
            rows = db.execute("SELECT body FROM items WHERE kind='delivery' AND json_extract(body,'$.status')='sending'").fetchall()
            if not rows:
                return 0
            fixed = 0
            db.execute('BEGIN IMMEDIATE')
            for (body,) in rows:
                item = json.loads(body)
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(item.get('attempted') or item['created'])).total_seconds()
                except (KeyError, ValueError, TypeError):
                    age = max_age + 1
                if age > max_age:
                    item.update(status='unknown', updated=now(), notice='서버 재시작 등으로 전송 결과 미확인. 수신처에서 확인하세요. 자동 재전송하지 않습니다.')
                    db.execute("UPDATE items SET body=? WHERE kind='delivery' AND id=?", (dumps(item), item['id']))
                    fixed += 1
            return fixed

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def all(self, kind):
        with self.db() as db:
            return [json.loads(x[0]) for x in db.execute('SELECT body FROM items WHERE kind=? ORDER BY rowid DESC', (kind,))]

    def get(self, kind, id_):
        with self.db() as db:
            row = db.execute('SELECT body FROM items WHERE kind=? AND id=?', (kind, id_)).fetchone()
        if not row:
            raise ValueError('항목을 찾을 수 없습니다')
        return json.loads(row[0])

    def by_chat(self, kind, chat_id, chronological=False):
        order = 'ASC' if chronological else 'DESC'
        with self.db() as db:
            rows = db.execute(f"SELECT body FROM items WHERE kind=? AND json_extract(body,'$.chat_id')=? ORDER BY rowid {order}", (kind, chat_id))
            return [json.loads(row[0]) for row in rows]

    def save_result(self, kind, result):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self.current_generation(db):
                raise ValueError('처리 중 백업이 복원되었습니다. 새 요청으로 다시 확인하세요')
            chat = db.execute("SELECT body FROM items WHERE kind='chat' AND id=?", (result['chat_id'],)).fetchone()
            if not chat:
                raise ValueError('처리 중 대화가 삭제되었습니다')
            db.execute('INSERT INTO items VALUES (?,?,?)', (kind, result['id'], dumps(result)))
            if kind == 'turn':
                db.execute("""UPDATE items SET body=json_set(body,'$.title',?)
                              WHERE kind='chat' AND id=? AND json_extract(body,'$.title')='새 대화'""",
                           (result['text'][:35], result['chat_id']))
        return result

    def current_generation(self, db):
        row = db.execute("SELECT value FROM meta WHERE key='restore_generation'").fetchone()
        return (row[0] if row else '0') == self.generation

    def set_tab(self, chat_id, opened):
        with self.db() as db:
            changed = db.execute("""UPDATE items SET body=json_set(body,'$.open',json(?))
                                   WHERE kind='chat' AND id=?""", (dumps(opened), chat_id))
            if not changed.rowcount:
                raise ValueError('항목을 찾을 수 없습니다')
            return json.loads(db.execute("SELECT body FROM items WHERE kind='chat' AND id=?", (chat_id,)).fetchone()[0])

    # Review A-4/B-5 (2026-11-17): deleting a chat or all data must not erase the proof of what was reviewed
    # and handed off. Those records become content-free audit stubs (who/what/when/hash/status) that no
    # delete path removes, and a handoff that someone may still pick up blocks deletion outright.
    LIVE_DELIVERY = ('sending', 'queued', 'claimed')
    HANDOFF_KINDS = ('report', 'report_review', 'delivery', 'receipt')
    AUDIT_FIELDS = ('id', 'chat_id', 'turn_id', 'report_id', 'delivery_id', 'created', 'updated', 'attempted', 'claimed',
                    'sha256', 'status', 'reviewer', 'reviewer_verified', 'scope', 'agent_id', 'provider_message_id', 'source_kind')

    def _audit_stubs(self, db, where, args):
        for kind in self.HANDOFF_KINDS:
            for (body,) in db.execute(f"SELECT body FROM items WHERE kind=? AND {where}", (kind, *args)).fetchall():
                item = json.loads(body)
                stub = {k: item[k] for k in self.AUDIT_FIELDS if k in item}
                if kind == 'receipt' and 'text' in item:
                    stub['text_sha256'] = hashlib.sha256(item['text'].encode('utf-8')).hexdigest()
                if 'target' in item:
                    stub['target'] = {k: item['target'].get(k) for k in ('id', 'label', 'transport')}
                stub.update(record_kind=kind, deleted=now(), note='원문 삭제됨 · 검토·전달 사실만 보존')
                db.execute('INSERT OR REPLACE INTO items VALUES (?,?,?)', ('audit', kind + ':' + item['id'], dumps(stub)))

    def backup(self, label='manual', keep=20):
        """Copy of this network's DB (sqlite online backup) under data/backups, newest `keep` kept."""
        folder = self.directory / 'backups'
        folder.mkdir(exist_ok=True)
        target = folder / f"{self.mode}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{label}.sqlite3"
        source, dest = sqlite3.connect(self.path), sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close(); source.close()
        for old in sorted(folder.glob(f'{self.mode}-*.sqlite3'))[:-keep]:
            old.unlink()
        return target

    BACKUP_NAME = re.compile(r'(internal|external)-\d{8}-\d{6}-\d{6}-[a-z-]{1,20}\.sqlite3')

    def backups(self):
        folder = self.directory / 'backups'
        if not folder.is_dir():
            return []
        rows = []
        for f in sorted(folder.glob(f'{self.mode}-*.sqlite3'), reverse=True):
            if self.BACKUP_NAME.fullmatch(f.name):
                rows.append({'name': f.name, 'bytes': f.stat().st_size,
                             'created': datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(),
                             'label': f.name.rsplit('-', 1)[-1].removesuffix('.sqlite3')})
        return rows

    def daily_backup(self):
        """At most one automatic backup per day per network (review B-9). Returns the file or None."""
        folder = self.directory / 'backups'
        today = datetime.now().strftime('%Y%m%d')
        if folder.is_dir() and any(folder.glob(f'{self.mode}-{today}-*-daily.sqlite3')):
            return None
        return self.backup('daily', keep=30)

    def restore(self, name):
        """Replace this network's data with a backup. The current state is backed up first, and the append-only
        records (action log, audit stubs) written since are carried over, so a restore cannot erase history."""
        if not isinstance(name, str) or not self.BACKUP_NAME.fullmatch(name) or not name.startswith(self.mode + '-'):
            raise ValueError('이 망의 백업 파일을 선택하세요')
        source = self.directory / 'backups' / name
        if not source.is_file():
            raise ValueError('백업 파일을 찾을 수 없습니다')
        # Read and validate before retention can remove the selected old file. Read-only opening
        # must never silently create an empty database and then copy it over the current one.
        try:
            src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
            try:
                src.execute('BEGIN')
                if src.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('백업 무결성을 확인할 수 없습니다')
                if [r[1] for r in src.execute('PRAGMA table_info(items)')] != ['kind', 'id', 'body'] or \
                        [r[1] for r in src.execute('PRAGMA table_info(meta)')] != ['key', 'value']:
                    raise ValueError('AlphaDrex 백업 형식이 아닙니다')
                rows = src.execute('SELECT kind,id,body FROM items').fetchall()
                metadata = src.execute('SELECT key,value FROM meta').fetchall()
                if dict(metadata).get('seeded') != '1':
                    raise ValueError('초기화된 AlphaDrex 백업이 아닙니다')
                for kind, ident, body in rows:
                    item = json.loads(body)
                    if not isinstance(item, dict) or item.get('id') != ident:
                        # Audit rows use a compound SQL key while retaining their original record ID.
                        if kind != 'audit' or not isinstance(item, dict) or ident != item.get('record_kind', '') + ':' + item.get('id', ''):
                            raise ValueError('백업 항목 형식이 올바르지 않습니다')
                    if kind == 'delivery' and item.get('status') in ('prepared', *self.LIVE_DELIVERY):
                        raise ValueError('미완료 전달이 담긴 백업은 복원할 수 없습니다. 중복 전달 방지를 위해 확인이 필요합니다')
            finally:
                src.close()
        except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
            raise ValueError('백업 형식 또는 무결성을 확인할 수 없습니다') from None
        before = self.backup('before-restore')
        generation = uid()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM items WHERE kind='delivery' AND json_extract(body,'$.status') IN ('sending','queued','claimed')").fetchone():
                raise ValueError('수신·회신이 끝나지 않은 전달이 있습니다. 끝난 뒤 복원하세요')
            keep = db.execute("SELECT kind,id,body FROM items WHERE kind IN ('action','audit')").fetchall()
            # A single transaction keeps failures and concurrent writers from leaving a half restore.
            db.execute('DELETE FROM items')
            db.execute('DELETE FROM meta')
            db.executemany('INSERT INTO items VALUES (?,?,?)', rows)
            db.executemany('INSERT INTO meta VALUES (?,?)', metadata)
            db.executemany('INSERT OR IGNORE INTO items VALUES (?,?,?)', keep)
            db.execute("INSERT OR REPLACE INTO meta VALUES ('restore_generation',?)", (generation,))
        self.generation = generation
        return before

    def clear_data(self):
        with self.db() as db:
            if db.execute("SELECT 1 FROM items WHERE kind='delivery' AND json_extract(body,'$.status') IN ('sending','queued','claimed')").fetchone():
                raise ValueError('수신·회신이 끝나지 않은 전달이 있습니다. 수신 완료나 결과 확인 뒤 삭제하세요')
        backup = self.backup('before-clear')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM items WHERE kind='delivery' AND json_extract(body,'$.status') IN ('sending','queued','claimed')").fetchone():
                raise ValueError('수신·회신이 끝나지 않은 전달이 있습니다. 수신 완료나 결과 확인 뒤 삭제하세요')
            self._audit_stubs(db, '1=1', ())
            db.execute("DELETE FROM items WHERE kind IN ('chat','turn','feedback','revision','manual','report','report_review','delivery','receipt','judgment','alert')")
            db.execute("""UPDATE items SET body=json_set(body,'$.manuals',json('[]')) WHERE kind='pack'""")
        return backup

    def put(self, kind, body):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO items VALUES (?,?,?)', (kind, body['id'], dumps(body)))
        return body

    def delete(self, kind, id_):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if kind=='chat' and db.execute("SELECT 1 FROM items WHERE kind='delivery' AND json_extract(body,'$.chat_id')=? AND json_extract(body,'$.status') IN ('sending','queued','claimed')",(id_,)).fetchone():
                raise ValueError('수신·회신이 끝나지 않은 전달이 있는 대화입니다. 수신 완료나 결과 확인 뒤 삭제하세요')
            if kind == 'chat':
                self._audit_stubs(db, "json_extract(body,'$.chat_id')=?", (id_,))
            db.execute('DELETE FROM items WHERE kind=? AND id=?', (kind, id_))
            if kind == 'chat':
                for subkind in ('turn', 'feedback', 'revision', 'report', 'report_review', 'delivery', 'receipt', 'judgment', 'alert'):
                    db.execute("DELETE FROM items WHERE kind=? AND json_extract(body,'$.chat_id')=?", (subkind, id_))

    def default_pack(self, value=None):
        with self.db() as db:
            if value is not None:
                self.get('pack', value)
                db.execute("INSERT OR REPLACE INTO meta VALUES ('default_pack',?)",(value,))
            row=db.execute("SELECT value FROM meta WHERE key='default_pack'").fetchone()
        return row[0] if row else 'field'

    @staticmethod
    def _hit(doc, group, line, i, score, urgent, match='키워드'):
        return {k: doc.get(k) for k in ('id', 'title', 'kind', 'publisher', 'version', 'audience', 'date', 'checked', 'url', 'license', 'freshness', 'external_approved')} | {
            'conflict_group': group, 'text': line, 'location': f'{i+1}행', 'score': score, 'urgent': urgent, 'match': match}

    @staticmethod
    def _content_lines(doc):
        """(index, line) pairs that may be cited: skips headings, preamble, scope and source sections."""
        lines = doc['text'].splitlines()
        sectioned = any(re.match(r'\s*##', line) for line in lines)
        heading, in_preamble, out = '', sectioned, []
        for i, line in enumerate(lines):
            if line.lstrip().startswith('#'):
                heading = line.lstrip().lstrip('#').strip()
                if line.lstrip().startswith('##'):
                    in_preamble = False
                continue
            if not line.strip() or in_preamble or heading in ('출처', '적용 범위'):
                continue
            out.append((i, line, heading))
        return out

    def liner_candidates(self, pack, limit=80):
        """Manual lines Liner may pick from: approved AND approved-for-external documents only."""
        out = []
        for doc in self.all('manual'):
            if doc['id'] in pack['manuals'] and doc['approved'] and not doc.get('superseded_by') and doc.get('external_approved'):
                for i, line, _ in self._content_lines(doc):
                    out.append({'cid': f'L{len(out) + 1}', 'doc': doc, 'i': i, 'line': line.strip()})
        return out[:limit]

    def manual_outline(self, pack):
        """Short labels for the Drex manual question: document title + section headings."""
        outline = {}
        for doc in self.all('manual'):
            if doc['id'] not in pack['manuals'] or not doc['approved'] or doc.get('superseded_by'):
                continue
            first = next((l.strip()[2:] for l in doc['text'].splitlines() if l.strip().startswith('# ')), '') or str(doc.get('title') or '')
            label = re.sub(r'\.(?:md|txt)$', '', first)
            label = re.sub(r'^가상·시연용\s*\|?\s*', '', label).replace('가상 ', '').replace(' 매뉴얼', '').strip()[:30] or doc['id'][:30]
            base, n = label, 2
            while label in outline:
                label, n = f'{base} {n}', n + 1
            heads = [l.strip()[3:] for l in doc['text'].splitlines() if l.strip().startswith('## ')]
            hint = ', '.join(h for h in heads if h not in ('적용 범위', '출처', '절차'))[:24] or label
            outline[label] = {'doc': doc, 'hint': hint}
        return outline

    def search(self, text, pack, emergency=None):
        tokens = set(re.findall(r'[가-힣A-Za-z0-9]{2,}', text.lower())) - GENERIC_STOPWORDS
        # Recover common noun stems when a Korean particle is attached. Two-syllable stems
        # (요청이 -> 요청) help recall but are noisy (이름을 -> 이름), so they weigh half: one alone
        # cannot make a line count as evidence.
        weak = set()
        for token in tuple(tokens):
            if re.fullmatch(r'[가-힣]+', token):
                stem = re.sub(r'(?:에서는|에게서|에게|에는|으로|에서|부터|까지|처럼|이나|에|을|를|은|는|이|가|과|와|도|로|의|만)$', '', token)
                if stem in GENERIC_STOPWORDS:
                    continue
                if stem != token and len(stem) >= 3:
                    tokens.add(stem)
                elif stem != token and len(stem) == 2 and stem not in tokens:
                    weak.add(stem)
        # Korean lexical matching is intentionally simple; no embedding claims.
        for term in ['점검', '누수', '보고', '인계', '출입', '지진', '승강기', '화재', '위험', '시설']:
            if term in text:
                tokens.add(term)
        results = []
        manuals = [doc for doc in self.all('manual') if doc['id'] in pack['manuals'] and doc['approved'] and not doc.get('superseded_by')]
        families = {}
        for doc in manuals:
            family = document_family(doc)
            families.setdefault(family, []).append(doc)
        version_groups = {family for family, docs in families.items() if family and
                          len({d.get('version') for d in docs}) > 1 and len({d['text'] for d in docs}) > 1}
        # The engine passes its final decision (rules OR Drex) so a Drex-only emergency still pulls the
        # emergency-procedure line; direct callers fall back to the rules.
        if emergency is None:
            emergency = risk_term_hit(text, pack['risk_terms']) or life_safety_signal(text)
        for doc in manuals:
            if doc['id'] not in pack['manuals'] or not doc['approved'] or doc.get('superseded_by'):
                continue
            # Whitespace-only groups used to raise IndexError and break every search in the pack.
            group_words = str(doc.get('conflict_group') or '').split()
            group = ' '.join(group_words)
            # Each group word must stand alone (출입증 is not 출입), optionally followed by a particle or a
            # verb ending (승인받을, 승인할). A written-together form (출입승인) also counts.
            if group_words and not (all(re.search(
                    r'(?<![가-힣A-Za-z0-9])' + re.escape(word) +
                    r'(?:(?:에서|으로|에는|에|은|는|을|를|이|가|과|와|의|도)|(?:받|하|해|할|한|된|되|됨)[가-힣]{0,4})?(?![가-힣A-Za-z0-9])', text)
                    for word in group_words) or (len(group_words) > 1 and ''.join(group_words) in text)):
                continue
            family = document_family(doc)
            if not group and family in version_groups:
                group = '버전 검토: ' + family
            # Introductions, scope notes and source lists describe the document, not a procedure.
            # They matched almost any question ("해솔 문화관", "기관") and hid '근거 부족'. Only skip
            # the preamble when the document actually has sections, so plain TXT uploads still work.
            for i, line, heading in self._content_lines(doc):
                low = line.lower()
                score = sum(1 for t in tokens if t in low) + 0.5 * sum(1 for t in weak if t in low)
                if score < 1:
                    score = 0
                urgent = emergency and bool(re.search(r'긴급 절차|지휘 체계|AI 응답을 기다리지', line))
                if score or urgent:
                    if len(line) < 40:
                        score += sum(1 for t in tokens if t in heading.lower())
                    results.append(self._hit(doc, group, line, i, score, urgent))
        ranked = sorted(results, key=lambda x: (-x['urgent'], -x['score']))
        top = ranked[:8]
        # Requirement: when documents conflict, show BOTH sources. A plain top-8 cut could drop
        # the counterpart document, hiding the conflict, so pull in its best-scoring line.
        groups = {e['conflict_group'] for e in top if e['conflict_group']}
        present = {e['id'] for e in top}
        for e in ranked[8:]:
            if e['conflict_group'] in groups and e['id'] not in present:
                top.append(e)
                present.add(e['id'])
        return top


EMERGENCY_WORD_VETO = 0.05
RULE_METHOD = '로컬 규칙 시험 (AI 판단 아님)'
# Manual lines that say what a report MUST contain. Kept narrow on purpose: words like "기록한다"
# alone are ordinary procedure, not a must-write instruction.
REQUIRED_PER_DOC, REQUIRED_TOTAL = 3, 12
REQUIRED_LINE = re.compile(r'보고서에는|보고서에\s*반드시|반드시\s*기재|필수\s*기재|꼭\s*적')
LINER_METHOD = 'Liner 실제 판단'


def brain():
    """'hybrid' (default): Drex judges first (local, no per-call cost); Liner is asked only when Drex is
    unsure, too slow, or a manual line must be chosen, plus a small shadow sample for measuring Drex.
    Every Drex/Liner pair is stored as a training record (foundation for later learning).
    'liner': Liner only. 'drex': Drex only. Both kept for comparison and rollback."""
    value = os.getenv('ALPHADREX_BRAIN', 'hybrid').strip().lower()
    return value if value in ('hybrid', 'liner', 'drex') else 'hybrid'


def max_wait():
    """Upper bound on the whole AI wait when Liner cannot take over (review A-8/B-1: a hung Drex held a
    person for up to ~360 s). Past it the turn falls back to the rule path, marked for human review; a late
    Drex answer is still recorded and a late emergency becomes a separate warning."""
    try:
        return max(1.0, float(os.getenv('ALPHADREX_MAX_WAIT_S', '120')))
    except ValueError:
        return 120.0


def bounded(fn, timeout, *args):
    """Run fn in a worker and wait at most `timeout` seconds. Returns (result, future); raises FutureTimeout."""
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn, *args)
    try:
        return future.result(timeout=timeout), future
    except FutureTimeout:
        exc = FutureTimeout()
        exc.future = future
        raise exc
    finally:
        executor.shutdown(wait=False)


def drex_budget():
    """Seconds the hybrid waits for Drex before handing the turn to Liner. On this PC's CPU Drex takes
    ~20-90 s, so the default favours collecting Drex answers; lower it when speed matters more."""
    try:
        return max(1.0, float(os.getenv('ALPHADREX_DREX_BUDGET_S', '60')))
    except ValueError:
        return 60.0


def shadow_rate():
    try:
        return min(1.0, max(0.0, float(os.getenv('ALPHADREX_SHADOW_RATE', '0.1'))))
    except ValueError:
        return 0.1


def shadow_pick(text):
    """Deterministic sample (same input -> same choice) so runs are reproducible."""
    return int(hashlib.sha256(text.encode('utf-8')).hexdigest()[:8], 16) % 1000 < shadow_rate() * 1000


# Fixed before any hybrid measurement: the same uncertainty band Drex already used for 'review'.
def escalation_reasons(drex_result, drex_note, weak_evidence):
    if drex_result is None:
        return ['Drex 결과 없음: ' + (drex_note or '미확인')]
    reasons = []
    answers = (drex_result.get('raw') or {}).get('answers') or {}
    confidence = (answers.get('category') or {}).get('confidence', 0)
    if not isinstance(confidence, (int, float)) or confidence < .75:
        reasons.append('분류 신뢰도 낮음')
    if any(isinstance((answers.get(k) or {}).get('noul'), (int, float)) and .3 < answers[k]['noul'] < .7
           for k in ('needs_info', 'review', 'search')):
        reasons.append('판단 경계값(0.3~0.7)')
    if weak_evidence:
        reasons.append('키워드 근거 약함: 매뉴얼 행 선택 필요')
    return reasons


def judgment_summary(result):
    if not result:
        return None
    keys = ('category', 'needs_info', 'review', 'search', 'emergency_model', 'emergency_score', 'evidence_ids',
            'returned_model', 'reason')
    out = {k: result.get(k) for k in keys if k in result}
    answers = (result.get('raw') or {}).get('answers')
    if result.get('method') == 'Drex 실제 응답' and answers:
        out['scores'] = {k: v.get('noul') for k, v in answers.items() if isinstance(v, dict) and 'noul' in v}
        out['category_confidence'] = (answers.get('category') or {}).get('confidence')
    return out


def agreement(drex, liner):
    if not drex or not liner:
        return None
    return {'category': drex.get('category') == liner.get('category'),
            'needs_info': drex.get('needs_info') == liner.get('needs_info'),
            'search': drex.get('search') == liner.get('search'),
            'emergency': None if drex.get('emergency_model') is None else drex.get('emergency_model') == liner.get('emergency_model')}
MANUAL_CHECK_BELOW = 2  # keyword evidence weaker than this asks Drex which manual (if any) answers


class Engine:
    def __init__(self, store, policy):
        self.store, self.policy = store, dict(policy)

    def liner_blocker(self, text, allow_external):
        """Why Liner may not be asked for this turn, or None."""
        if not allow_external:
            return '공개·가상 입력 확인 전'
        if self.policy['mode'] != 'external' or not self.policy['external_enabled']:
            return '내부망 또는 외부 호출 꺼짐'
        if not os.getenv('LINER_API_KEY'):
            return 'Liner API 키 미등록'
        if providers.sensitive(text):
            return '민감정보 의심 입력'
        return None

    def ask_liner(self, text, pack, candidates):
        providers.check_outbound_policy(self.policy)
        result = providers.liner_judge(text, pack, [{'id': c['cid'], 'title': c['doc']['title'], 'text': c['line']}
                                                    for c in candidates], self.policy)
        # A suggestion only (the user still confirms), so the cheap rule may add to Liner's answer.
        result['search'] = result['search'] or search_needed(text, pack['search_terms'])
        return result

    def hybrid(self, text, pack, allow_external, candidates, weak):
        local = providers.is_local_drex(os.getenv('DREX_URL', providers.DEFAULT_DREX_URL))
        drex_result, drex_note, self.late_drex = None, None, None
        started = time.perf_counter()
        blocker = self.liner_blocker(text, allow_external)
        # The time budget exists only to hand a slow turn to Liner. When Liner cannot be asked (internal
        # network, sensitive input, no key) waiting is better than dropping Drex for the rule path; the
        # provider's own timeout still bounds the wait (2026-11-16 internal re-measure found this).
        budget = drex_budget() if blocker is None else max_wait()
        if not local and not allow_external:
            drex_note = '공개·가상 입력 확인 전 외부 Drex 차단'
        else:
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(providers.drex, text, pack, self.policy)
            try:
                drex_result = future.result(timeout=budget)
            except FutureTimeout:
                drex_note = f'Drex 시간 예산 {budget:g}초 초과'
                self.late_drex = future  # its answer is still stored later as training data
            except providers.ProviderError as exc:
                drex_note = str(exc)
            finally:
                executor.shutdown(wait=False)
        drex_ms = round((time.perf_counter() - started) * 1000, 2)
        reasons = escalation_reasons(drex_result, drex_note, weak)
        shadow = not reasons and blocker is None and shadow_pick(text)
        liner_result, liner_error, liner_calls = None, None, 0
        if (reasons or shadow) and blocker is None:
            liner_calls = 1
            try:
                liner_result = self.ask_liner(text, pack, candidates)
            except providers.ProviderError as exc:
                liner_error = str(exc)
        if reasons and liner_result:
            final, route = dict(liner_result), 'Liner 상향 판단'
            # Safety merge (2026-11-17 review A-1): handing the turn to Liner must not drop Drex's danger signal.
            # Either model saying "emergency" is enough, and only Drex's near-zero score may overrule a risk
            # word; Liner's "no danger" alone never does in hybrid mode.
            drex_emergency = bool(drex_result and drex_result.get('emergency_model'))
            final['emergency_model'] = bool(final.get('emergency_model') or drex_emergency)
            final['emergency_score'] = (drex_result or {}).get('emergency_score')
            final['emergency_denied'] = False
            if drex_emergency:
                final['emergency_by_drex'] = True
        elif drex_result:
            final = dict(drex_result)
            route = 'Drex 단독 판단' if not reasons else 'Drex 판단 (상향 필요, Liner 사용 불가)'
            if reasons:
                final['review'] = True
        else:
            raise providers.ProviderError(' / '.join(x for x in (drex_note, liner_error or blocker) if x) or 'AI 판단 불가')
        d, l = judgment_summary(drex_result), judgment_summary(liner_result)
        final['hybrid'] = {'route': route, 'escalation_reasons': reasons, 'shadow': shadow, 'liner_blocked': blocker if (reasons or shadow) else None,
                           'liner_error': liner_error, 'liner_calls': liner_calls, 'drex_note': drex_note, 'drex_ms': drex_ms,
                           'drex': d, 'liner': l, 'agreement': agreement(d, l)}
        return final

    def classify(self, text, pack, allow_external, outline=None, candidates=None, hybrid=False, weak=False):
        start = time.perf_counter()
        local = providers.is_local_drex(os.getenv('DREX_URL', providers.DEFAULT_DREX_URL))
        self.late_drex = None
        try:
            if hybrid:
                result = self.hybrid(text, pack, allow_external, candidates, weak)
            elif candidates is not None:
                if not allow_external:
                    raise providers.ProviderError('공개·가상 입력 확인 전 Liner 판단 차단')
                if not os.getenv('LINER_API_KEY'):
                    raise providers.ProviderError('연결되지 않음: API 키 미등록')
                providers.check_outbound_policy(self.policy)  # also enforced in request_json; checked first here
                result = providers.liner_judge(text, pack, [{'id': c['cid'], 'title': c['doc']['title'], 'text': c['line']}
                                                            for c in candidates], self.policy)
                # A suggestion only (the user still confirms), so the cheap rule may add to Liner's answer.
                result['search'] = result['search'] or search_needed(text, pack['search_terms'])
            else:
                if not local and not allow_external:
                    raise providers.ProviderError('공개·가상 입력 확인 전 외부 Drex 차단')
                try:
                    result, _ = bounded(providers.drex, max_wait(), text, pack, self.policy)
                except FutureTimeout as late:
                    self.late_drex = late.future  # a late emergency still becomes a warning (see run)
                    raise providers.ProviderError(f'Drex 대기 상한 {max_wait():g}초 초과')
        except providers.ProviderError as exc:
            fallback = pack.get('fallback_category', pack['categories'][-1])
            category = next((k for k, terms in pack['keywords'].items() if any(t in text for t in terms)), fallback)
            lines = text.strip().splitlines()
            # A preamble that only introduces a note adds no task details.
            note = len(lines) == 2 and re.fullmatch(
                r'[^.!?]{0,40}메모[^.!?]{0,30}(?:입니다|있습니다|다음과 같습니다)\.', lines[0].strip()
            )
            short_input = len(text.strip()) < 25 or (bool(note) and len(lines[-1].strip()) < 15)
            result = {'method': RULE_METHOD, 'error': str(exc), 'category': category, 'needs_info': short_input, 'review': True, 'search': search_needed(text, pack['search_terms']), 'raw': None}
        if outline and result.get('method') == 'Drex 실제 응답':
            # Only after the main Drex call worked: an unreachable Drex is not asked twice. Sequential on
            # purpose: splitting questions across the server's two slots gave no reliable gain (2026-11-14).
            t = time.perf_counter()
            left = max_wait() - (t - start)
            try:
                if left < 1:
                    raise providers.ProviderError('대기 상한 도달: 매뉴얼 확인 생략')
                result['manual_check'], _ = bounded(providers.drex_manual, left, text, {k: v['hint'] for k, v in outline.items()}, self.policy)
            except FutureTimeout:
                result['manual_check'] = {'error': '대기 상한 도달: 매뉴얼 확인 중단'}
            except providers.ProviderError as exc:
                result['manual_check'] = {'error': str(exc)}
            result['manual_check']['wall_ms'] = round((time.perf_counter()-t)*1000, 2)
        result['wall_ms'] = round((time.perf_counter()-start)*1000, 2)
        if explicitly_missing_context(text) or vague_request(text):
            result['needs_info'] = True
            result['review'] = True
            result['server_guard'] = '상황의 핵심 정보 누락 명시 (서버 규칙, Drex 판정과 별개)'
        rule_term = risk_term_hit(text, pack['risk_terms'])
        rule_phrase = life_safety_signal(text)
        model_emergency = result.get('emergency_model')
        score = result.get('emergency_score')
        # A bare risk WORD ("긴급 연락망", "연기 수업", "가스레인지") is overruled only when Drex is almost
        # certain there is no danger. Life-safety phrases are never overruled. The turn still needs
        # review and the external draft stays blocked (see run). Threshold fixed before the 1000-case run.
        denied = (score is not None and score < EMERGENCY_WORD_VETO) or result.get('emergency_denied') is True
        word_overruled = bool(rule_term and not rule_phrase and denied)
        # Either source is enough: rules catch listed words, Drex catches described situations.
        result['emergency'] = bool((rule_term and not word_overruled) or rule_phrase or model_emergency)
        model_name = 'Liner' if result.get('method') == LINER_METHOD else 'Drex'
        if result.get('emergency_by_drex'):
            model_name = 'Drex'  # Drex said emergency even though Liner made the final call
        result['emergency_sources'] = [name for name, hit in (('위험 단어 규칙', rule_term and not word_overruled), ('생명·안전 표현 규칙', rule_phrase),
                                                              (model_name + ' 긴급 판정', model_emergency)) if hit]
        result['risk_word'] = bool(rule_term)
        if word_overruled:
            who = f'Drex 긴급 점수 {score:.3f} < {EMERGENCY_WORD_VETO}' if score is not None else 'Liner가 즉각 위험 아님으로 판정'
            result['risk_word_note'] = f'위험 단어가 있으나 {who}: 긴급 표시 안 함 · 담당자 검토 유지 · 외부 초안 차단'
            result['review'] = True
        if rule_phrase and not rule_term:
            result['safety_guard'] = '생명·안전 위험 표현 감지 (서버 규칙, Drex 판정과 별개)'
        result['review'] = result['review'] or result['emergency']
        return result

    def preview(self, chat_id, text):
        """Instant, local-only keyword result shown WHILE the model is still judging (Drex takes ~30-40s on
        this CPU). Nothing is saved and no model is called: the stored first result is still the one run()
        returns, and it may differ (manual choice, request-focus rule, conflicts)."""
        pack = self.store.get('pack', self.store.get('chat', chat_id)['pack_id'])
        rule_emergency = bool(risk_term_hit(text, pack['risk_terms']) or life_safety_signal(text))
        hits = [e for e in self.store.search(text, pack, rule_emergency) if e['urgent'] or e['score'] >= MANUAL_CHECK_BELOW]
        return {'preview': True, 'saved': False, 'emergency_rule': rule_emergency,
                'evidence': [{k: e[k] for k in ('title', 'location', 'text', 'urgent')} for e in hits[:3]],
                'note': '키워드 검색 미리보기 (AI 판단 전 · 저장 안 됨 · 최종 결과에서 바뀔 수 있음)'}

    def run(self, chat_id, text, allow_external=False):
        start = time.perf_counter()
        chat = self.store.get('chat', chat_id)
        pack = self.store.get('pack', chat['pack_id'])
        t = time.perf_counter()
        rule_emergency = risk_term_hit(text, pack['risk_terms']) or life_safety_signal(text)
        keyword = self.store.search(text, pack, rule_emergency)
        # Background such as the worker's role or location can match a manual
        # even when the final, explicit request has no supporting procedure.
        sentences = re.split(r'(?<=[.!?])\s+|\n+', text.strip())
        request = sentences[-1]
        explicit_request = len(sentences) > 1 and bool(
            re.search(r'알려\s*주|알고\s*싶|어떻게|무엇|궁금|질문|요청|문의|[?]$', request)) and has_own_topic(request)
        request_hits = self.store.search(request, pack, False) if explicit_request else []
        contextual_only = explicit_request and max(
            (hit['score'] for hit in request_hits), default=0) < MANUAL_CHECK_BELOW
        switched_topic = bool(re.search(r'실제\s*(?:질문|요청|문의)|별개로|다만\s*(?:질문|요청|문의)|질문의\s*핵심', request))
        follows_context = bool(re.search(r'무엇을|어떤\s*(?:항목|내용|정보)|어느\s*항목|해당|이\s*(?:항목|기록)', request))
        retrieval_ms = round((time.perf_counter()-t)*1000, 2)
        mode = brain()
        candidates = self.store.liner_candidates(pack) if mode in ('liner', 'hybrid') else None
        weak_keywords = not rule_emergency and (contextual_only or
                        max((e['score'] for e in keyword), default=0) < MANUAL_CHECK_BELOW)
        # Drex-only brain asks Drex for the manual; the hybrid hands that choice to Liner instead, except
        # when Liner cannot be asked (internal network, sensitive input, no key): then the local Drex picks
        # the manual too (2026-11-16 internal re-measure: 11 synonym misses with no other way to recover).
        liner_blocked = mode == 'hybrid' and self.liner_blocker(text, allow_external) is not None
        outline = self.store.manual_outline(pack) if weak_keywords and (mode == 'drex' or liner_blocked) else None
        result = self.classify(text, pack, allow_external, outline, candidates,
                               hybrid=mode == 'hybrid', weak=weak_keywords)
        t = time.perf_counter()
        if 'evidence_ids' in result:
            evidence = self.liner_evidence(text, pack, result, candidates)
            # Safety net (2026-11-14 real-Liner sample: 5/10 conflicts hidden because Liner dropped lines it
            # judged 'test-only'). A conflict the question's own words reveal is always shown, Liner or not.
            groups = {}
            for e in keyword:
                if e['conflict_group']:
                    groups.setdefault(e['conflict_group'], {}).setdefault(e['id'], e)
            seen = {(e['id'], e['location']) for e in evidence}
            for docs in groups.values():
                if len(docs) > 1:
                    for e in docs.values():
                        if (e['id'], e['location']) not in seen:
                            evidence.append(e | {'match': '키워드 충돌 규칙 (Liner 미선택)'})
        else:
            evidence = self.apply_manual_check(self.store.search(text, pack, result['emergency']), result, outline)
            fallback_scope = mode == 'liner' and result['method'] == RULE_METHOD and not result['needs_info'] and (
                switched_topic or (not request_hits and not follows_context))
            if (contextual_only and (mode == 'hybrid' or fallback_scope)
                    and not any(e['conflict_group'] for e in evidence)):
                evidence = [e for e in evidence if e['urgent']]
        evidence = self.add_required_lines(evidence)
        retrieval_ms = round(retrieval_ms + (time.perf_counter()-t)*1000, 2)
        # A conflict needs two different documents in the same group, not two lines of one document.
        docs_by_group = {}
        for e in evidence:
            if e['conflict_group']:
                docs_by_group.setdefault(e['conflict_group'], set()).add(e['id'])
        conflict = any(len(ids) > 1 for ids in docs_by_group.values())
        conflict_reasons = [group for group, ids in docs_by_group.items() if len(ids) > 1]
        status = '담당자 검토 필요'
        if not evidence:
            status = '근거 부족'
        elif conflict:
            status = '문서 충돌 · 담당자 검토 필요'
        elif result['needs_info']:
            status = '추가 정보 필요'
        draft_result = None
        # Say why a draft is absent. One message for every reason ('외부 전송 확인 또는 설정 필요')
        # described neither a document conflict nor an emergency, so a reader could think the only
        # missing step was a setting (found 2026-11-23 while re-checking the contest build).
        if result['needs_info']:
            draft_status = '추가 정보를 확인한 뒤 초안을 작성할 수 있습니다'
        elif self.policy['mode'] == 'internal':
            draft_status = '내부망: 자연어 초안 사용 불가'
        elif not evidence:
            draft_status = '근거 부족: 등록 매뉴얼에서 근거를 찾지 못해 초안을 만들지 않습니다'
        elif result['emergency']:
            draft_status = '긴급 상황 의심: AI 초안보다 기관 긴급 절차와 지휘 체계를 먼저 확인하세요'
        elif conflict:
            draft_status = '문서 충돌: 기준을 임의로 고르지 않습니다. 담당자가 기준 문서를 정한 뒤 작성합니다'
        elif result.get('risk_word'):
            draft_status = '위험 단어 검토 중: 담당자 확인 전에는 초안을 만들지 않습니다'
        elif not allow_external:
            draft_status = '공개·가상 입력 확인 전: 질문마다 외부 전송 동의가 필요합니다'
        elif not self.policy['external_enabled']:
            draft_status = '외부 전송이 꺼져 있습니다. 설정에서 허용한 뒤 초안을 요청하세요'
        else:
            draft_status = '외부 전송 확인 또는 설정 필요'
        model_ms = None
        model_calls = 0
        if evidence and not conflict and not result['emergency'] and not result.get('risk_word') and not result['needs_info'] and allow_external and self.policy['mode'] == 'external' and self.policy['external_enabled']:
            t = time.perf_counter()
            try:
                # Count actual outbound attempts only after preflight passes.
                if not os.getenv('LINER_API_KEY'):
                    raise providers.ProviderError('연결되지 않음: API 키 미등록')
                if providers.sensitive(text) or any(not e['external_approved'] or providers.sensitive(e['text']) for e in evidence):
                    raise providers.ProviderError('민감정보 또는 미승인 자료: 전송 차단')
                providers.check_outbound_policy(self.policy)
                model_calls = 1
                draft_result = providers.draft(text, evidence, [], self.policy)
                draft_status = 'Liner 실제 생성 · 담당자 검토 전'
            except providers.ProviderError as exc:
                draft_status = str(exc)
            model_ms = round((time.perf_counter()-t)*1000, 2)
        query = public_search_query(text) if result['search'] else None
        turn = {'id': uid(), 'chat_id': chat_id, 'created': now(), 'text': text, 'pack_snapshot': pack, 'decision': result, 'status': status,
                'evidence': evidence, 'conflict': conflict, 'conflict_reasons': conflict_reasons, 'questions': pack['additional_questions'], 'draft': draft_result, 'draft_status': draft_status,
                'template': pack['template'], 'search_query': query, 'allow_external': allow_external,
                'metrics': {'judge_calls': result['hybrid']['liner_calls'] if 'hybrid' in result else int(result.get('method') == LINER_METHOD), 'liner_judge_ms': result.get('judge_ms'),
                            'drex_wall_ms': result['wall_ms'] if result['raw'] and result.get('method') != LINER_METHOD else None, 'decision_attempt_ms': result['wall_ms'], 'retrieval_ms': retrieval_ms, 'liner_model_ms': model_ms, 'liner_search_ms': None, 'total_ms': round((time.perf_counter()-start)*1000,2), 'search_calls': 0, 'model_calls': model_calls, 'cost': None}}
        saved = self.store.save_result('turn', turn)
        if 'hybrid' in result:
            self.save_judgment(saved, result)
        elif getattr(self, 'late_drex', None) is not None:
            # Wait cap hit with no Liner (internal network): the rule answer is shown now, and a late Drex
            # "emergency" still reaches the person as a separate warning (review A-1 follow-up).
            store, turn_view = self.store, saved
            self.late_drex.add_done_callback(lambda future: Engine._late_alert(store, turn_view, future))
        return saved

    @staticmethod
    def _late_alert(store, turn, future, db=None):
        try:
            answer = future.result()
        except Exception:
            return
        if not answer.get('emergency_model') or turn['decision'].get('emergency'):
            return
        alert = {'id': uid(), 'chat_id': turn['chat_id'], 'turn_id': turn['id'], 'created': now(),
                 'type': 'late_drex_emergency', 'emergency_score': answer.get('emergency_score'),
                 'message': '늦게 도착한 Drex 판정이 긴급입니다. 기관 긴급 절차를 먼저 확인하세요.'}
        try:
            store.save_result('alert', alert)  # refuses if the chat was deleted meanwhile
        except ValueError:
            pass

    def save_judgment(self, turn, result):
        """Training record: Drex answer, Liner answer (when asked), the final decision and later human
        feedback can be joined by turn_id. Kept in the same network DB as the turn; never copied across."""
        h = result['hybrid']
        record = {'id': uid(), 'chat_id': turn['chat_id'], 'turn_id': turn['id'], 'created': now(),
                  'input_sha256': hashlib.sha256(turn['text'].encode('utf-8')).hexdigest(),
                  'sensitive': bool(providers.sensitive(turn['text'])), 'pack_id': turn['pack_snapshot']['id'],
                  'route': h['route'], 'escalation_reasons': h['escalation_reasons'], 'shadow': h['shadow'],
                  'drex': h['drex'], 'drex_late': False, 'liner': h['liner'], 'agreement': h['agreement'],
                  'final': {k: result.get(k) for k in ('category', 'needs_info', 'review', 'search', 'emergency')},
                  'evidence': [[e['id'], e['location']] for e in turn['evidence']],
                  'drex_manual': {k: v for k, v in (result.get('manual_check') or {}).items() if k != 'wall_ms'} or None}
        try:
            self.store.save_result('judgment', record)
        except ValueError:  # The chat was deleted immediately after the turn was saved.
            return
        late = getattr(self, 'late_drex', None)
        if late is not None:
            store = self.store

            def keep_late(future, record=record):
                try:
                    answer = future.result()
                except Exception:  # a failed late answer is simply not recorded
                    return
                with store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not store.current_generation(db):
                        return
                    row = db.execute("SELECT body FROM items WHERE kind='judgment' AND id=?", (record['id'],)).fetchone()
                    if row is None:
                        return
                    current = json.loads(row[0])
                    current['drex'], current['drex_late'] = judgment_summary(answer), True
                    current['agreement'] = agreement(current['drex'], current['liner'])
                    db.execute("UPDATE items SET body=? WHERE kind='judgment' AND id=?", (dumps(current), record['id']))
                    # Review A-1 follow-up: a late Drex "emergency" must reach the person, not only training data.
                    # The first result is never overwritten; the warning is a separate record shown with the turn.
                    if answer.get('emergency_model') and not record['final'].get('emergency'):
                        alert = {'id': uid(), 'chat_id': record['chat_id'], 'turn_id': record['turn_id'], 'created': now(),
                                 'type': 'late_drex_emergency', 'emergency_score': answer.get('emergency_score'),
                                 'message': '늦게 도착한 Drex 판정이 긴급입니다. 기관 긴급 절차를 먼저 확인하세요.'}
                        db.execute('INSERT INTO items VALUES (?,?,?)', ('alert', alert['id'], dumps(alert)))
            late.add_done_callback(keep_late)

    def liner_evidence(self, text, pack, result, candidates):
        """Lines Liner picked (by id, so text is never invented) + the conflict counterpart + urgent lines."""
        by_id = {c['cid']: c for c in candidates}
        docs = {c['doc']['id']: c['doc'] for c in candidates}
        families = {}
        for doc in docs.values():
            families.setdefault(document_family(doc), []).append(doc)
        version_groups = {f for f, ds in families.items() if f and len({d.get('version') for d in ds}) > 1
                          and len({d['text'] for d in ds}) > 1}

        def group_of(doc):
            # Same rule as keyword search: a manual group, else differing versions of one document family.
            group = ' '.join(str(doc.get('conflict_group') or '').split())
            family = document_family(doc)
            return group or ('버전 검토: ' + family if family in version_groups else '')

        hits = []
        for cid in result['evidence_ids']:
            c = by_id[cid]
            hits.append(self.store._hit(c['doc'], group_of(c['doc']), c['line'], c['i'], 0, False, 'Liner 선택'))
        # Requirement: a conflict must show BOTH documents, even if Liner picked only one side.
        groups = {h['conflict_group'] for h in hits if h['conflict_group']}
        present = {h['id'] for h in hits}
        for c in candidates:
            group = group_of(c['doc'])
            if group in groups and c['doc']['id'] not in present:
                hits.append(self.store._hit(c['doc'], group, c['line'], c['i'], 0, False, 'Liner 선택 문서와 같은 충돌 그룹'))
                present.add(c['doc']['id'])
        if result['emergency']:
            urgent = [e for e in self.store.search(text, pack, True) if e['urgent']]
            seen = {(u['id'], u['location']) for u in urgent}
            hits = urgent + [h for h in hits if (h['id'], h['location']) not in seen]
        return hits

    def add_required_lines(self, evidence):
        """A cited manual's must-write lines ("보고서에는 …", "반드시 기재 …") always travel with it.
        2026-11-17 real-Liner run: Liner cited the right manual 10/10 but skipped the report-code line
        10/10 because it picks only lines that answer the question. Only documents already cited are
        touched, so no new document is ever pulled in by this rule."""
        cited = list(dict.fromkeys(e['id'] for e in evidence if not e['urgent']))
        seen = {(e['id'], e['location']) for e in evidence}
        added = []
        for doc_id in cited:
            try:
                doc = self.store.get('manual', doc_id)
            except (KeyError, ValueError):
                continue
            group = next((e['conflict_group'] for e in evidence if e['id'] == doc_id), '')
            per_doc = 0
            for i, line, _ in self.store._content_lines(doc):
                # Per-manual cap (review follow-up): a long first manual can no longer crowd out the second
                # manual's must-write line, which the old global [:6] cut silently.
                if per_doc < REQUIRED_PER_DOC and REQUIRED_LINE.search(line) and (doc_id, f'{i+1}행') not in seen:
                    added.append(self.store._hit(doc, group, line, i, 0, False, '필수 기재 줄 (자동 포함)'))
                    per_doc += 1
                    seen.add((doc_id, f'{i+1}행'))
        return evidence + added[:REQUIRED_TOTAL]

    def apply_manual_check(self, evidence, result, outline):
        check = result.get('manual_check')
        if not outline or not check or 'choice' not in check or not check['applied']:
            if check is not None and 'choice' in check:
                check['effect'] = '신뢰도 낮음: 키워드 결과 유지'
            return evidence
        urgent = [e for e in evidence if e['urgent']]
        rest = [e for e in evidence if not e['urgent']]
        if check['none']:
            # Drex says no manual answers: one or two shared words are not evidence.
            kept = [e for e in rest if e['score'] >= MANUAL_CHECK_BELOW]
            check['effect'] = f'해당 매뉴얼 없음: 약한 키워드 근거 {len(rest) - len(kept)}개 제외'
            return urgent + kept
        doc = outline[check['choice']]['doc']
        chosen = [e for e in rest if e['id'] == doc['id']]
        if not chosen:
            # Keywords missed the document (synonyms). Cite its procedure lines, marked as Drex-selected.
            chosen = [self.store._hit(doc, '', line, i, 0, False, 'Drex 매뉴얼 선택 (키워드 불일치)')
                      for i, line, _ in self.store._content_lines(doc)[:6]]
        check['effect'] = f'매뉴얼 선택: {check["choice"]} 근거 {len(chosen)}개 우선'
        return urgent + chosen + [e for e in rest if e['id'] != doc['id']]

    def search_confirmed(self, turn_id, query, confirmed):
        original = self.store.get('turn', turn_id)
        if not original['search_query']:
            raise ValueError('이 결과에는 검색 확인 요청이 없습니다')
        if not confirmed or not query.strip():
            raise ValueError('검색어 확인이 필요합니다')
        if self.policy['mode'] != 'external' or not self.policy['external_enabled']:
            raise ValueError('서버 정책: 외부 검색 차단')
        if providers.sensitive(query):
            raise ValueError('민감정보 의심: 검색어를 공개 주제로 수정하세요')
        if len(query) > 200:
            raise ValueError('검색어는 200자 이하입니다')
        revision = {'id': uid(), 'chat_id': original['chat_id'], 'turn_id': turn_id, 'created': now(), 'query': query, 'results': [], 'search_calls': 0, 'model_calls': 0, 'draft': None, 'model_ms': None, 'cost': None}
        t = time.perf_counter()
        try:
            if not os.getenv('LINER_API_KEY'):
                raise providers.ProviderError('연결되지 않음: API 키 미등록')
            providers.check_outbound_policy(self.policy)
            revision['search_calls'] = 1
            revision['results'] = providers.search(query, self.policy)
            revision['status'] = '원문 미검증 · 담당자 검토 필요' if revision['results'] else '검색 결과 없음'
        except providers.ProviderError as exc:
            revision['status'] = str(exc)
        revision['search_ms'] = round((time.perf_counter()-t)*1000, 2)
        if revision['results'] and original['allow_external'] and original['evidence'] and not original['conflict'] and not original['decision']['emergency'] and not original['decision'].get('risk_word') and not original['decision']['needs_info']:
            t = time.perf_counter()
            try:
                if providers.sensitive(original['text']) or any(not e['external_approved'] or providers.sensitive(e['text']) for e in original['evidence']):
                    raise providers.ProviderError('민감정보 또는 미승인 자료: 초안 전송 차단')
                providers.check_outbound_policy(self.policy)
                revision['model_calls'] = 1
                revision['draft'] = providers.draft(original['text'], original['evidence'], revision['results'], self.policy)
            except providers.ProviderError as exc:
                revision['draft_error'] = str(exc)
            revision['model_ms'] = round((time.perf_counter()-t)*1000,2)
        return self.store.save_result('revision', revision)


def operation_metrics(turns, revisions):
    durations = sorted(t['metrics']['total_ms'] for t in turns if t['metrics'].get('total_ms') is not None)
    draft_records = [(t['metrics'].get('model_calls', 0), t.get('draft')) for t in turns]
    draft_records += [(r.get('model_calls', 0), r.get('draft')) for r in revisions]
    return {
        'turn_latency_ms': {
            'samples': len(durations),
            **{label: durations[math.ceil(len(durations) * p) - 1] if durations else None
               for label, p in (('p50', 0.5), ('p95', 0.95))},
        },
        'draft_attempts_without_output': sum(calls for calls, draft in draft_records if not draft),
        'search_attempts_without_results': sum(r.get('search_calls', 0) for r in revisions if not r.get('results')),
    }


def metrics(store):
    turns, revisions, feedback = store.all('turn'), store.all('revision'), store.all('feedback')
    by_id = {t['id']: t for t in turns}
    searched = {r['turn_id'] for r in revisions if r['search_calls']}
    latest = {}
    for f in sorted(feedback, key=lambda x:x['created']):
        if f['turn_id'] in by_id:
            latest[f['turn_id']] = f
    reviewed = [f for f in latest.values() if f.get('category_ok') in ('yes','no')]
    comparison_files = sorted((ROOT / 'evaluation').glob('ab-calls*.json'),
                              key=lambda path: (path.name != 'ab-calls.json', path.name))
    comparison = '미실행'
    if comparison_files:
        try:
            report = json.loads(comparison_files[-1].read_text(encoding='utf-8'))
            comparison = {key: report[key] for key in ('complete', 'a_always_search', 'b_drex_search',
                          'routing_reduction_pct', 'missed_expected_search', 'actual_liner_calls', 'cost_reduction_pct')}
            comparison['classified_cases'] = sum('search' in row for row in report['cases'])
            comparison['scope'] = '로컬 Drex 검색 질문만 실행 · 예상 요청 수'
        except (OSError, ValueError, KeyError, TypeError):
            comparison = '비교 기록을 읽을 수 없습니다'
    return {'turns': len(turns), 'search_calls': sum(r['search_calls'] for r in revisions), 'model_calls': sum(t['metrics']['model_calls'] for t in turns)+sum(r['model_calls'] for r in revisions),
            'reviewed_cases':len(reviewed), 'classification_accuracy': sum(f['category_ok']=='yes' for f in reviewed)/len(reviewed) if reviewed else None,
            'search_not_executed':sum(bool(t['search_query']) and t['id'] not in searched for t in turns),
            'reported_search_misses':sum(f.get('search_needed')=='yes' and not by_id[f['turn_id']]['decision']['search'] for f in latest.values()),
            'reported_risk_misses':sum(f.get('risk_missed')=='yes' for f in latest.values()), 'cost': None, 'ab_comparison': comparison,
            'last_timings':turns[0]['metrics'] if turns else None, 'hybrid': hybrid_metrics(store),
            'observability': operation_metrics(turns, revisions)}


def hybrid_metrics(store):
    records = store.all('judgment')
    if not records:
        return None
    pairs = [r['agreement'] for r in records if r.get('agreement')]
    def rate(field):
        vals = [p[field] for p in pairs if p.get(field) is not None]
        return f'{sum(vals)}/{len(vals)}' if vals else None
    return {'turns': len(records), 'drex_only': sum(r['route'] == 'Drex 단독 판단' for r in records),
            'escalated_to_liner': sum(r['route'] == 'Liner 상향 판단' for r in records),
            'shadow_liner': sum(bool(r['shadow']) for r in records),
            'liner_judge_calls_avoided': sum(r['route'] == 'Drex 단독 판단' and not r['shadow'] for r in records),
            'training_pairs': len(pairs), 'agreement': {k: rate(k) for k in ('category', 'needs_info', 'search', 'emergency')},
            'note': '일치율은 Drex와 Liner가 같은 답을 냈다는 뜻이며 정답률이 아닙니다.'}
