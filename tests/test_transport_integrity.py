import io
import unittest
import urllib.request
from unittest.mock import patch
from app import providers
from app.core import explicitly_missing_context

class TransportIntegrityTests(unittest.TestCase):
    def test_missing_location_and_activity_are_not_treated_as_completed_facts(self):
        self.assertTrue(explicitly_missing_context('어느 구역에서 무엇을 했는지 모두 미확인입니다.'))
        self.assertTrue(explicitly_missing_context('장소와 시각을 누구도 확인하지 못했습니다.'))
        self.assertFalse(explicitly_missing_context('담당 직원 이름만 미확인입니다.'))
        self.assertFalse(explicitly_missing_context('담당자 이름을 아직 확인하지 못했습니다.'))

    def test_valid_json_with_truncated_http_body_is_rejected(self):
        response=io.BytesIO(b'{}');response.headers={'Content-Length':'12'}
        with patch('urllib.request.OpenerDirector.open',return_value=response):
            with self.assertRaisesRegex(providers.ProviderError,'불완전'):
                providers._open_json(urllib.request.Request('http://127.0.0.1/'),True)

    def test_complete_declared_body_is_accepted(self):
        response=io.BytesIO(b'{}');response.headers={'Content-Length':'2'}
        with patch('urllib.request.OpenerDirector.open',return_value=response):
            self.assertEqual(providers._open_json(urllib.request.Request('http://127.0.0.1/'),True),{})
