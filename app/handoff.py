"""Immutable reports and durable, explicitly confirmed handoffs in the current DB."""
import hashlib
import hmac
import json
import os
import re
from .core import dumps, now, uid
from . import providers


def digest(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def string(body, key, maximum, required=True):
    value=body.get(key,'')
    if not isinstance(value,str) or len(value)>maximum or (required and not value.strip()):
        raise ValueError('문서·전달 입력 형식 오류: '+key)
    return value.strip()


def agent_entries():
    try:
        entries=json.loads(os.getenv('ALPHADREX_AGENT_TOKENS','{}'))
        if not isinstance(entries,dict) or len(entries)>50:
            raise ValueError()
        for key,value in entries.items():
            if (not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',key) or key=='simulation' or
                    not isinstance(value,dict) or not isinstance(value.get('token'),str) or
                    len(value['token'])<32 or value.get('mode') not in ('internal','external') or
                    not isinstance(value.get('label',key),str) or len(value.get('label',key))>100):
                raise ValueError()
        return entries
    except (ValueError,TypeError):
        raise ValueError('에이전트 인증 설정 형식 오류') from None


def targets():
    result=[dict(id='simulation',label='가상 수신자 · 모의 전송',transport='simulation')]
    entries=agent_entries()
    defaults={'facilities':'시설 담당 에이전트','duty':'당직 담당 에이전트'}
    for agent_id in dict.fromkeys([*defaults,*entries]):
        entry=entries.get(agent_id,{})
        result.append(dict(id=agent_id,label=entry.get('label',defaults.get(agent_id,agent_id)),
                           transport='agent',mode=entry.get('mode'),connected=bool(entry)))
    # Administrators may add destinations; the browser cannot supply an address.
    try:
        configured=json.loads(os.getenv('ALPHADREX_TELEGRAM_TARGETS','{}'))
    except ValueError:
        raise ValueError('Telegram 수신 대상 설정 형식 오류') from None
    if not isinstance(configured,dict) or len(configured)>30:
        raise ValueError('Telegram 수신 대상 설정 형식 오류')
    if os.getenv('TELEGRAM_CHAT_ID'):
        configured.setdefault('default',dict(label='Telegram 기본 수신자',chat_id=os.getenv('TELEGRAM_CHAT_ID')))
    for key,value in configured.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',key) or not isinstance(value,dict):
            raise ValueError('Telegram 수신 대상 설정 형식 오류')
        chat=str(value.get('chat_id',''))
        label=value.get('label','')
        if not re.fullmatch(r'-?\d{1,30}|@[A-Za-z0-9_]{5,64}',chat) or not isinstance(label,str) or not 1<=len(label)<=100:
            raise ValueError('Telegram 수신 대상 설정 형식 오류')
        result.append(dict(id='telegram:'+key,label=label,transport='telegram',chat_id=chat))
    return result


def public_targets():
    return [t | {'configured':bool(os.getenv('TELEGRAM_BOT_TOKEN')) if t['transport']=='telegram'
                 else t.get('connected',True)} for t in targets()]


def authenticate_agent(agent_id, token, mode):
    try:
        configured=agent_entries()
        entry=configured.get(agent_id,{})
        expected=entry.get('token','')
        return (agent_id in configured and len(expected)>=32 and
                entry.get('mode')==mode and hmac.compare_digest(expected,token))
    except (ValueError,TypeError,AttributeError):
        return False


class Handoff:
    def __init__(self, store, policy):
        self.store,self.policy=store,policy

    def external_preflight(self, report):
        providers.check_outbound_policy(self.policy)
        turn=self.store.get('turn',report['turn_id'])
        if not report['external_eligible'] or providers.sensitive(report['text']) or providers.contains_configured_secret(report['text']):
            raise ValueError('민감정보 또는 외부 전송 미승인 원본: 전달 차단')
        if len(report['text'].encode('utf-8'))>200000:
            raise ValueError('외부 전달 문서는 200KB 이하여야 합니다')
        for hit in turn['evidence']:
            doc=self.store.get('manual',hit['id'])
            if not doc.get('approved') or not doc.get('external_approved'):
                raise ValueError('근거 문서의 전송 승인이 취소되었습니다')

    def state(self, chat_id=None):
        # Bound document payloads and fetch only related records, not entire tables.
        with self.store.db() as db:
            if chat_id:
                rows=db.execute("SELECT body FROM items WHERE kind='report' AND json_extract(body,'$.chat_id')=? ORDER BY rowid DESC LIMIT 50",(chat_id,))
            else:
                rows=db.execute("SELECT body FROM items WHERE kind='report' ORDER BY rowid DESC LIMIT 50")
            headers=('id','chat_id','turn_id','created','parent_id','root_id','title','sha256','source_status')
            reports=[{key:value for key,value in json.loads(row[0]).items() if key in headers} for row in rows]
            ids=[r['id'] for r in reports]
            result=dict(targets=public_targets(),mode=self.store.mode,reports=reports,limit=50)
            for kind,label in [('report_review','reviews'),('delivery','deliveries'),('receipt','receipts')]:
                if not ids:
                    result[label]=[];continue
                placeholders=','.join('?' for _ in ids)
                rows=db.execute(f"SELECT body FROM items WHERE kind=? AND json_extract(body,'$.report_id') IN ({placeholders}) ORDER BY rowid DESC",[kind]+ids)
                result[label]=[json.loads(row[0]) for row in rows]
            return result

    def create_report(self, body):
        turn=self.store.get('turn',body['turn_id'])
        parent=None
        if body.get('parent_id'):
            parent=self.store.get('report',body['parent_id'])
            if parent['turn_id']!=turn['id']:
                raise ValueError('다른 답변의 문서를 수정할 수 없습니다')
        source_kind=body.get('source_kind','turn')
        source_id=body.get('source_id',turn['id'])
        if source_kind not in ('turn','revision','feedback','receipt'):
            raise ValueError('문서 원본 종류 오류')
        source=self.store.get(source_kind,source_id)
        source_turn_id=self.store.get('report',source['report_id'])['turn_id'] if source_kind=='receipt' else source.get('turn_id')
        if (source_kind=='turn' and source_id!=turn['id']) or (source_kind!='turn' and source_turn_id!=turn['id']):
            raise ValueError('다른 답변의 원본을 사용할 수 없습니다')
        title=string(body,'title',120)
        # No inference: generated advice is never converted into completed work.
        content=body.get('content')
        if content is None:
            content=source.get('text') if source_kind=='receipt' else source.get('edited') if source_kind=='feedback' else (source.get('draft') or {}).get('text')
            content=content or turn['template']
        content=string({'content':content},'content',24000)
        facts=string(body,'facts',4000,False) or '미확인 · 작성자 확인 필요'
        actions=string(body,'actions',4000,False) or '미확인 · 실제 수행한 조치만 작성'
        request=string(body,'request',2000,False) or '내용 검토와 후속 확인 요청'
        evidence=turn['evidence']
        flags=[]
        if turn['decision']['emergency']:
            flags.append('긴급 상황 의심: AI 또는 에이전트 회신을 기다리지 말고 기관 긴급 절차와 현장 지휘 체계를 우선하세요.')
        if turn['conflict']:
            flags.append('문서 기준·적용 버전 검토 필요: 임의로 하나를 채택하지 마세요.')
        if turn['decision']['needs_info']:
            flags.append('정보 부족: 추가 확인 전 확정 답변이나 완료 처리로 사용하지 마세요.')
        text='\n\n'.join([f'# {title}','검토용 전달 문서 · 공식 승인 또는 조치 완료를 뜻하지 않습니다.',
            '## 문서 원본\n'+{'turn':'최초 답변','revision':'후속 검색 답변','feedback':'담당자 수정 기록','receipt':'에이전트 회신 · 담당자 재검토 필요'}[source_kind],
            '## 주의사항\n'+('\n'.join(flags) or '담당자 검토 후 사용하세요.'),
            '## 원본 상태\n'+turn['status'], '## 작성자가 확인한 사실\n'+facts,
            '## 실제 수행한 조치\n'+actions,'## 검토 내용\n'+content,
            '## 수신자에게 요청할 사항\n'+request,
            '## 근거\n'+('\n'.join(f"- {e['title']} / {e['location']} / 버전 {e.get('version','미확인')}\n  {e['text']}" for e in evidence) or '등록 근거 없음'),
            '## 추가 확인 질문\n'+'\n'.join('- '+q for q in turn['questions'])])
        if source_kind=='revision':
            text+='\n\n## 후속 검색 링크 · 원문 미검증\n'+'\n'.join(
                str(r.get('title',''))+' '+str(r.get('url','')) for r in source.get('results',[]))
        if len(text.encode('utf-8'))>200000:
            raise ValueError('근거를 포함한 문서가 200KB를 넘습니다. 문서 원본을 분할해 검토하세요')
        report=dict(id=uid(),chat_id=turn['chat_id'],turn_id=turn['id'],created=now(),
                    schema_version='alphadrex.handoff/1',evidence=evidence,questions=turn['questions'],
                    flags=dict(emergency=turn['decision']['emergency'],conflict=turn['conflict'],needs_info=turn['decision']['needs_info']),
                    usage='검토 데이터 · 실행 명령 또는 업무 완료 증명이 아님',
                    parent_id=parent['id'] if parent else None,root_id=parent['root_id'] if parent else None,
                    source_kind=source_kind,source_id=source_id,title=title,content=content,facts=facts,
                    actions=actions,request=request,text=text,sha256=digest(text),
                    external_eligible=turn.get('allow_external') is True and bool(evidence) and
                    all(e.get('external_approved') is True and not providers.sensitive(e['text']) for e in evidence)
                    and not providers.sensitive(turn['text']),source_status=turn['status'])
        report['root_id']=report['root_id'] or report['id']
        return self.store.save_result('report',report)

    def review(self, body, verified=False):
        report=self.store.get('report',body['report_id'])
        if body.get('confirmed') is not True or body.get('sha256')!=report['sha256']:
            raise ValueError('현재 문서 내용 확인이 필요합니다')
        # With accounts on, the server fills reviewer from the login (verified); otherwise it is self-reported.
        return self.store.save_result('report_review',dict(id=uid(),chat_id=report['chat_id'],
            report_id=report['id'],sha256=report['sha256'],created=now(),
            reviewer=string(body,'reviewer',100),note=string(body,'note',1000),reviewer_verified=verified,
            scope='전달 내용 검토 · 로그인 계정 확인됨 · 업무 승인 아님' if verified else '전달 내용 검토 · 신원 인증 또는 업무 승인 아님'))

    def prepare(self, body):
        report=self.store.get('report',body['report_id'])
        if providers.contains_configured_secret(report['text']):
            raise ValueError('인증정보가 포함된 문서는 전달할 수 없습니다')
        reviews=self.store.by_chat('report_review',report['chat_id'])
        if not any(r['report_id']==report['id'] and r['sha256']==report['sha256'] for r in reviews):
            raise ValueError('이 문서 버전의 전달 내용 검토를 먼저 저장하세요')
        target=next((t for t in targets() if t['id']==body.get('target_id')),None)
        if target is None:
            raise ValueError('등록된 수신 대상을 선택하세요')
        # Review A-3: an agent without a configured token can never pick the handoff up, so queuing to it
        # looked like "delivered" while nobody received it.
        if target['transport']=='agent' and not target.get('connected'):
            raise ValueError('연결되지 않은 에이전트입니다. 관리자가 수신 에이전트를 연결한 뒤 전달하세요')
        if target['transport']=='agent' and target.get('mode') not in (None,self.store.mode):
            raise ValueError('다른 망에 연결된 에이전트입니다. 망 사이 문서를 자동 복사하지 않습니다')
        if target['transport']=='telegram':
            self.external_preflight(report)
            if not os.getenv('TELEGRAM_BOT_TOKEN'):
                raise ValueError('Telegram 봇 토큰 미설정')
        ident=digest([report['id'],target])[:32]
        delivery=dict(id=ident,chat_id=report['chat_id'],report_id=report['id'],created=now(),
                      target=target,status='prepared',sha256=report['sha256'],
                      confirmation=digest([report['sha256'],target]),title=report['title'])
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute("SELECT body FROM items WHERE kind='delivery' AND id=?",(ident,)).fetchone()
            if existing:
                return json.loads(existing[0])
            if not db.execute("SELECT 1 FROM items WHERE kind='report' AND id=?",(report['id'],)).fetchone():
                raise ValueError('문서가 삭제되었습니다')
            db.execute('INSERT INTO items VALUES (?,?,?)',('delivery',ident,dumps(delivery)))
        return delivery

    def send(self, body):
        if body.get('confirmed') is not True:
            raise ValueError('수신자와 실제 전송 문서 확인이 필요합니다')
        delivery=self.store.get('delivery',body['delivery_id'])
        if not isinstance(body.get('confirmation'),str) or not hmac.compare_digest(body['confirmation'],delivery['confirmation']):
            raise ValueError('전송 확인 값이 일치하지 않습니다')
        report=self.store.get('report',delivery['report_id'])
        if providers.contains_configured_secret(report['text']):
            raise ValueError('인증정보가 포함된 문서는 전달할 수 없습니다')
        target=delivery['target']
        current = next((item for item in targets() if item['id']==target['id']),None)
        # Older agent previews did not store mode or connection metadata.
        legacy_match = (
            current is not None and current['transport']=='agent'
            and 'mode' not in target and 'connected' not in target
            and current['mode'] in (None,self.store.mode)
            and target == {key:value for key,value in current.items() if key not in ('mode','connected')}
        )
        if current != target and not legacy_match:
            raise ValueError('수신 대상 설정이 변경되었습니다. 새 미리보기가 필요합니다')
        if current['transport']=='agent' and not current.get('connected'):
            raise ValueError('연결되지 않은 에이전트입니다. 관리자가 수신 에이전트를 연결한 뒤 전달하세요')
        if target['transport']=='telegram':
            self.external_preflight(report)
            if body.get('external_confirmed') is not True:
                raise ValueError('외부 전송 확인이 필요합니다')
        # Commit a reservation BEFORE any network request. A crash cannot auto-retry it.
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT body FROM items WHERE kind='delivery' AND id=?",(delivery['id'],)).fetchone()
            if not row:
                raise ValueError('전달 요청이 삭제되었습니다')
            delivery=json.loads(row[0])
            if delivery['status']!='prepared':
                return delivery
            delivery.update(status='sending',attempted=now())
            db.execute("UPDATE items SET body=? WHERE kind='delivery' AND id=?",(dumps(delivery),delivery['id']))
        if target['transport']=='telegram':
            try:
                result=providers.telegram_document(report['title'],report['text'],target['chat_id'],self.policy)
                delivery.update(status='sent',provider_message_id=result['message_id'])
            except providers.ProviderError:
                delivery.update(status='unknown',notice='전송 결과 미확인. 수신처에서 확인하세요. 자동 재전송하지 않습니다.')
        elif target['transport']=='agent':
            delivery.update(status='queued',notice='에이전트 수신함 등록 · 아직 접수되지 않음')
        else:
            delivery.update(status='simulated',notice='모의 전송 완료 · 실제 외부 전송 없음')
        delivery['updated']=now()
        with self.store.db() as db:
            db.execute("UPDATE items SET body=? WHERE kind='delivery' AND id=?",(dumps(delivery),delivery['id']))
        return delivery

    def inbox(self, agent_id):
        with self.store.db() as db:
            rows=db.execute("""SELECT body FROM items WHERE kind='delivery'
                AND json_extract(body,'$.target.id')=?
                AND json_extract(body,'$.status') IN ('queued','claimed')
                ORDER BY json_extract(body,'$.status')='queued' DESC, rowid LIMIT 50""",(agent_id,))
            return [json.loads(row[0]) for row in rows]

    def claim(self, agent_id, body):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT body FROM items WHERE kind='delivery' AND id=?",(body['delivery_id'],)).fetchone()
            delivery=json.loads(row[0]) if row else {}
            if delivery.get('target',{}).get('id')!=agent_id or delivery.get('status') not in ('queued','claimed'):
                raise ValueError('접수 가능한 자신의 전달 요청이 아닙니다')
            delivery.update(status='claimed',claimed=delivery.get('claimed') or now())
            report=db.execute("SELECT body FROM items WHERE kind='report' AND id=?",(delivery['report_id'],)).fetchone()
            if not report:
                raise ValueError('원본 문서가 없습니다')
            db.execute("UPDATE items SET body=? WHERE kind='delivery' AND id=?",(dumps(delivery),delivery['id']))
            return dict(delivery=delivery,report=json.loads(report[0]))

    def reply(self, agent_id, body):
        text=string(body,'text',12000)
        key=string(body,'request_id',100)
        if providers.contains_configured_secret(text) or providers.contains_configured_secret(key):
            raise ValueError('인증정보가 포함된 회신은 저장할 수 없습니다')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT body FROM items WHERE kind='delivery' AND id=?",(body['delivery_id'],)).fetchone()
            delivery=json.loads(row[0]) if row else {}
            if delivery.get('target',{}).get('id')!=agent_id:
                raise ValueError('자신의 전달 요청에만 회신할 수 있습니다')
            ident=digest([delivery['id'],key])[:32]
            existing=db.execute("SELECT body FROM items WHERE kind='receipt' AND id=?",(ident,)).fetchone()
            if existing:
                existing=json.loads(existing[0])
                if existing['text']!=text:
                    raise ValueError('같은 회신 요청 ID의 내용이 다릅니다')
                return existing
            if delivery['status']!='claimed':
                raise ValueError('접수한 요청에 한 번만 최종 회신할 수 있습니다')
            receipt=dict(id=ident,delivery_id=delivery['id'],chat_id=delivery['chat_id'],
                         report_id=delivery['report_id'],agent_id=agent_id,text=text,created=now(),
                         status='담당자 검토 전 · 에이전트 회신')
            db.execute('INSERT INTO items VALUES (?,?,?)',('receipt',ident,dumps(receipt)))
            delivery.update(status='replied',updated=now())
            db.execute("UPDATE items SET body=? WHERE kind='delivery' AND id=?",(dumps(delivery),delivery['id']))
            return receipt
