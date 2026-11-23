"""End-to-end HTTP checks without any API key or model: rule path, mock Liner path, handoff flow.

Patches the store factory and accounts directory to a temporary folder, so the real data/ is untouched.
No network: DREX_URL points at a closed local port and the Liner calls are replaced by mocks.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import app.server as server
from app import providers
from app.core import Store


class SmokeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.env = patch.dict(os.environ, {
            'DREX_URL': 'http://127.0.0.1:9/v1/systemone',
            'ALPHADREX_BRAIN': 'hybrid',
            'ALPHADREX_DREX_BUDGET_S': '3',
            'ALPHADREX_MAX_WAIT_S': '5',
        })
        cls.env.start()
        cls.original = (server.store, server.ACCOUNTS_DIR, server.POLICY.copy(), server.PORT)
        server.store = lambda mode=None: Store(directory=cls.tmp, mode=mode or server.POLICY['mode'])
        server.ACCOUNTS_DIR = cls.tmp
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        server.PORT = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{server.PORT}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.store, server.ACCOUNTS_DIR, server.PORT = cls.original[0], cls.original[1], cls.original[3]
        server.POLICY.clear()
        server.POLICY.update(cls.original[2])
        cls.env.stop()

    def call(self, path, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method or ('POST' if data is not None else 'GET'),
            headers={'Content-Type': 'application/json', 'X-AlphaDrex': '1'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def new_chat(self):
        status, chat = self.call('/api/chat', {'pack_id': 'field'})
        self.assertEqual(status, 200)
        return chat['id']

    def test_health_and_state(self):
        status, health = self.call('/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(health['status'], 'ok')
        self.assertEqual(len(health['build_id']), 64)
        status, state = self.call('/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(state['version'], '0.2.0')
        self.assertEqual(len(state['manuals']), 4)
        self.assertEqual(state['connections']['liner_key_registered'], False)

    def test_rule_path_without_model(self):
        """No Drex, no Liner key: the turn must still be answered by the local rule path and marked."""
        chat_id = self.new_chat()
        status, turn = self.call('/api/message', {'chat_id': chat_id, 'text': '가상 시설 A구역 정기 점검의 확인 목록을 알려 주세요.'})
        self.assertEqual(status, 200)
        self.assertEqual(turn['decision']['method'], '로컬 규칙 시험 (AI 판단 아님)')
        self.assertEqual(turn['status'], '담당자 검토 필요')
        self.assertTrue(turn['evidence'])
        self.assertEqual(turn['draft'], None)
        self.assertEqual(turn['draft_status'], '내부망: 자연어 초안 사용 불가')

    def test_emergency_blocks_draft_and_marks_turn(self):
        chat_id = self.new_chat()
        status, turn = self.call('/api/message', {'chat_id': chat_id, 'text': '가상 현장에서 연기가 발생했습니다. 쓰러진 사람이 대답이 없습니다.'})
        self.assertEqual(status, 200)
        self.assertTrue(turn['decision']['emergency'])
        self.assertEqual(turn['draft'], None)

    def test_handoff_flow_with_simulation_target(self):
        chat_id = self.new_chat()
        _, turn = self.call('/api/message', {'chat_id': chat_id, 'text': '가상 시설 A구역 정기 점검의 확인 목록을 알려 주세요.'})
        status, report = self.call('/api/report/create', {'mode': 'internal', 'turn_id': turn['id'], 'title': '가상 점검 보고'})
        self.assertEqual(status, 200, report)
        self.assertEqual(len(report['sha256']), 64)
        # Preparing without a review of this exact version must be refused.
        status, refused = self.call('/api/handoff/prepare', {'mode': 'internal', 'report_id': report['id'], 'target_id': 'simulation'})
        self.assertEqual(status, 400)
        self.assertIn('검토', refused['error'])
        status, review = self.call('/api/report/review', {'mode': 'internal', 'report_id': report['id'],
                                                          'sha256': report['sha256'], 'confirmed': True,
                                                          'reviewer': '테스트', 'note': '내용 확인'})
        self.assertEqual(status, 200, review)
        status, delivery = self.call('/api/handoff/prepare', {'mode': 'internal', 'report_id': report['id'], 'target_id': 'simulation'})
        self.assertEqual(status, 200, delivery)
        self.assertEqual(delivery['status'], 'prepared')
        status, sent = self.call('/api/handoff/send', {'mode': 'internal', 'delivery_id': delivery['id'],
                                                       'confirmation': delivery['confirmation'], 'confirmed': True})
        self.assertEqual(status, 200, sent)
        self.assertEqual(sent['status'], 'simulated')
        # Repeating the identical send must not create a second delivery.
        status, again = self.call('/api/handoff/send', {'mode': 'internal', 'delivery_id': delivery['id'],
                                                        'confirmation': delivery['confirmation'], 'confirmed': True})
        self.assertEqual(status, 200)
        self.assertEqual(again['status'], 'simulated')

    def inspection_line_id(self):
        """A real candidate id from the point-inspection manual, like the one Liner returns."""
        store = Store(directory=self.tmp, mode='internal')
        candidates = store.liner_candidates(store.get('pack', 'field'))
        return next(c['cid'] for c in candidates if c['doc']['id'] == 'demo-inspection')

    def conflict_line_id(self):
        store = Store(directory=self.tmp, mode='internal')
        candidates = store.liner_candidates(store.get('pack', 'field'))
        return next(c['cid'] for c in candidates if c['doc']['id'] == 'demo-conflict-a')

    def test_mock_liner_judgment_and_draft(self):
        """The Liner code path is exercised with a stand-in response, so no key and no network are needed."""
        judged = {'category': '시설 점검', 'needs_info': False, 'review': False, 'search': False, 'emergency': False,
                  'evidence_ids': [self.inspection_line_id()], 'dropped_ids': 0, 'reason': '시설 점검 절차를 묻는 가상 상황',
                  'emergency_model': False, 'emergency_score': None, 'emergency_denied': True,
                  'method': 'Liner 실제 판단', 'requested_model': 'liner-gpt-router', 'returned_model': 'gpt-6-sol',
                  'judge_ms': 1.0, 'raw': {'answer': {}, 'model': 'gpt-6-sol', 'usage': {}},
                  'score_note': '모의 응답'}
        drafted = {'text': '확인 목록 초안 (모의)', 'requested_model': 'liner-gpt-router',
                   'returned_model': 'gpt-6-sol', 'usage': {}, 'cost': None, 'route': 'Liner 플랫폼 라우터'}
        server.POLICY.update(mode='external', external_enabled=True)
        os.environ['LINER_API_KEY'] = 'test-key'
        os.environ['ALPHADREX_BRAIN'] = 'liner'
        try:
            with patch.object(providers, 'liner_judge', return_value=dict(judged)), \
                    patch.object(providers, 'draft', return_value=dict(drafted)):
                chat_id = self.new_chat()
                status, turn = self.call('/api/message', {'chat_id': chat_id, 'allow_external': True,
                                                          'text': '가상 시설 A구역 정기 점검의 확인 목록을 알려 주세요.'})
        finally:
            os.environ.pop('LINER_API_KEY', None)
            os.environ['ALPHADREX_BRAIN'] = 'hybrid'
            server.POLICY.update(mode='internal', external_enabled=False)
        self.assertEqual(status, 200, turn)
        self.assertEqual(turn['decision']['method'], 'Liner 실제 판단')
        self.assertTrue(turn['evidence'])
        self.assertEqual(turn['draft']['text'], '확인 목록 초안 (모의)')
        self.assertEqual(turn['draft_status'], 'Liner 실제 생성 · 담당자 검토 전')
        self.assertEqual(turn['metrics']['judge_calls'], 1)
        self.assertEqual(turn['metrics']['model_calls'], 1)

    def test_sensitive_input_never_reaches_transport(self):
        """Personal data in the situation must be refused before any request is opened, in either path."""
        text = '가상 점검 중 연락처 010-1234-5678로 보고했습니다.'
        pack = Store(directory=self.tmp, mode='internal').get('pack', 'field')
        server.POLICY.update(mode='external', external_enabled=True)
        os.environ['LINER_API_KEY'] = 'test-key'
        try:
            with patch.object(providers, 'request_json', side_effect=AssertionError('전송 시도됨')):
                with self.assertRaises(providers.ProviderError) as judged:
                    providers.liner_judge(text, pack, [{'id': 'L1', 'title': '가상 점검', 'text': '점검 절차'}], server.POLICY)
                with self.assertRaises(providers.ProviderError) as drafted:
                    providers.draft(text, [{'text': '점검 절차', 'title': '가상 점검', 'external_approved': True}], [], server.POLICY)
        finally:
            os.environ.pop('LINER_API_KEY', None)
            server.POLICY.update(mode='internal', external_enabled=False)
        self.assertIn('민감정보', str(judged.exception))
        self.assertIn('민감정보', str(drafted.exception))

    def test_conflict_shows_both_documents_and_blocks_draft(self):
        """A conflict the situation raises must show both sides and state why no draft was written."""
        judged = {'category': '시설 점검', 'needs_info': False, 'review': False, 'search': False, 'emergency': False,
                  'evidence_ids': [self.conflict_line_id()], 'dropped_ids': 0, 'reason': '가상 출입 승인 기준',
                  'emergency_model': False, 'emergency_score': None, 'emergency_denied': True,
                  'method': 'Liner 실제 판단', 'requested_model': 'liner-gpt-router', 'returned_model': 'gpt-6-sol',
                  'judge_ms': 1.0, 'raw': {'answer': {}, 'model': 'gpt-6-sol', 'usage': {}}, 'score_note': '모의 응답'}
        server.POLICY.update(mode='external', external_enabled=True)
        os.environ['LINER_API_KEY'] = 'test-key'
        os.environ['ALPHADREX_BRAIN'] = 'liner'
        try:
            with patch.object(providers, 'liner_judge', return_value=dict(judged)), \
                    patch.object(providers, 'draft', side_effect=AssertionError('충돌 시 초안을 만들면 안 됨')):
                chat_id = self.new_chat()
                status, turn = self.call('/api/message', {'chat_id': chat_id, 'allow_external': True,
                                                          'text': '가상 출입 승인 조건이 문서마다 다릅니다. 어느 기준을 따라야 하나요?'})
        finally:
            os.environ.pop('LINER_API_KEY', None)
            os.environ['ALPHADREX_BRAIN'] = 'hybrid'
            server.POLICY.update(mode='internal', external_enabled=False)
        self.assertEqual(status, 200, turn)
        self.assertTrue(turn['conflict'])
        self.assertEqual(turn['status'], '문서 충돌 · 담당자 검토 필요')
        self.assertEqual({e['id'] for e in turn['evidence']}, {'demo-conflict-a', 'demo-conflict-b'})
        self.assertEqual(turn['draft'], None)
        self.assertTrue(turn['draft_status'].startswith('문서 충돌'))


if __name__ == '__main__':
    unittest.main()
