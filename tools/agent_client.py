"""Reference agent connector. No task execution; acknowledgement is explicitly labelled."""
import argparse
import json
import os
import urllib.request
import urllib.error
from urllib.parse import urlparse
from app.server import load_env


class AgentClient:
    def __init__(self, base, agent, token, mode='internal'):
        parsed=urlparse(base)
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('참조 커넥터는 로컬 AlphaDrex 서버만 연결합니다')
        self.base,self.agent,self.token,self.mode=base.rstrip('/'),agent,token,mode

    def request(self, endpoint, body=None):
        request=urllib.request.Request(self.base+'/api/agent/'+endpoint,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+self.token,'X-AlphaDrex-Agent':self.agent,
                     'X-AlphaDrex-Mode':self.mode,'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        try:
            with opener.open(request,timeout=15) as response:
                return json.load(response)
        except (urllib.error.URLError,TimeoutError,ValueError):
            raise RuntimeError('에이전트 연결 실패. 서버와 인증 설정을 확인하세요.') from None

    def inbox(self):
        return self.request('inbox')['items']

    def claim(self, delivery_id):
        return self.request('claim',{'delivery_id':delivery_id})

    def reply(self, delivery_id, text, request_id):
        return self.request('reply',dict(delivery_id=delivery_id,text=text,request_id=request_id))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError('리디렉션 차단')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='http://127.0.0.1:8767')
    parser.add_argument('--agent',required=True,help='서버에 등록된 에이전트 ID')
    parser.add_argument('--delivery-id',help='Only process this explicitly selected delivery')
    parser.add_argument('--acknowledge',action='store_true',help='접수 확인만 회신. 업무 완료/AI 분석으로 표시하지 않음')
    args=parser.parse_args()
    load_env()
    try:
        entry=json.loads(os.getenv('ALPHADREX_AGENT_TOKENS','{}'))[args.agent]
        client=AgentClient(args.base,args.agent,entry['token'],entry['mode'])
        items=client.inbox()
        if args.delivery_id:
            items=[item for item in items if item['id']==args.delivery_id]
        print('Pending deliveries:',len(items))
        if args.acknowledge:
            for item in items:
                claimed=client.claim(item['id'])
                client.reply(item['id'],
                    '문서 접수를 확인했습니다. 참조 커넥터의 자동 접수 회신이며 AI 분석·현장 조치·업무 완료는 수행하지 않았습니다. 담당 에이전트의 검토가 필요합니다.',
                    'ack:'+claimed['report']['sha256'])
                print('Acknowledged:',item['id'])
    except (KeyError,ValueError,TypeError,RuntimeError):
        print('Agent configuration or connection failed; no credentials or payloads printed.')
        raise SystemExit(1) from None


if __name__=='__main__':
    main()
