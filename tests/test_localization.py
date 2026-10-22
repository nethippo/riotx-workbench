import ast
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from backend import main as wb
from backend.localization import language, translate, CATALOG


class LocalizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=wb.app),base_url='http://workbench.test')
    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_http_error_language_and_no_request_input_translation(self):
        for lang,expected in [('en','Item not found'),('ko','항목을 찾을 수 없습니다')]:
            with patch.object(wb,'secret_profile',side_effect=wb.HTTPException(404,'항목을 찾을 수 없습니다')):
                response=await self.client.put('/api/profiles/missing',headers={'Accept-Language':lang},json={'name':'소스','url':'redis://localhost:6401/0'})
            self.assertEqual(response.status_code,404)
            self.assertEqual(response.json()['detail'],expected)

    async def test_validation_message_and_inputs_preserved(self):
        for lang,expected in [('en','The DB number must be an integer'),('ko','DB 번호는 정수여야 합니다')]:
            response=await self.client.post('/api/profiles',headers={'Accept-Language':lang},json={'name':'고객 연결','url':'redis://localhost/not-a-db'})
            self.assertEqual(response.status_code,422)
            error=response.json()['detail'][0]
            self.assertIn(expected,error['msg'])
            self.assertEqual(error['input'],'redis://localhost/not-a-db')

    async def test_protection_errors_follow_request_language(self):
        with patch.dict('os.environ',{'WORKBENCH_TOKEN':'test-secret'}):
            response=await self.client.get('/api/state',headers={'Accept-Language':'en'})
        self.assertEqual(response.status_code,401)
        self.assertEqual(response.json()['detail'],'An access token is required')
        response=await self.client.post('/api/profiles',headers={'Accept-Language':'en','Origin':'http://other.test'},json={})
        self.assertEqual(response.status_code,403)
        self.assertEqual(response.json()['detail'],'Cross-site mutation request rejected')

    async def test_catalog_covers_backend_korean_literals(self):
        for path in Path(wb.__file__).parent.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.Constant) and isinstance(node.value,str) and any('가'<=c<='힣' for c in node.value):
                    self.assertIn(node.value.strip(),CATALOG,f'{path.name}:{node.lineno}')
        self.assertEqual(language('en-US,en;q=0.9'),'en')
        self.assertEqual(language('ko-KR,en;q=0.9'),'ko')
        self.assertEqual(language(None),'ko')
        self.assertEqual(translate('ValueError: 지원하지 않거나 관리되는 옵션: --bad','en'),'ValueError: Unsupported or managed option: --bad')
