"""Create local agent credentials without printing them or changing existing keys."""
import json
import secrets
from app.core import ROOT


def setup():
    path=ROOT/'env.local'
    old=path.read_text(encoding='utf-8-sig') if path.exists() else ''
    if any(line.strip().startswith('ALPHADREX_AGENT_TOKENS=') for line in old.splitlines()):
        print('Agent configuration already exists; kept unchanged.')
        return
    agents={name:dict(token=secrets.token_urlsafe(32),mode='internal') for name in ('facilities','duty')}
    with path.open('a',encoding='utf-8') as handle:
        handle.write('\n# Local agent inbox credentials; never commit or print.\nALPHADREX_AGENT_TOKENS='+json.dumps(agents,separators=(',',':'))+'\n')
    print('Configured facilities and duty for internal inboxes. Restart a server to load them.')


if __name__=='__main__':
    setup()
