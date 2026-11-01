"""Visitor static files never authorize controller routes/chunks or HTTP."""
from _offline_guard import require_offline_copy
require_offline_copy()
import unittest
from unittest.mock import patch
import httpx
from backend import main


class VisitorStatic(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_https_visitor_document_and_isolated_assets_are_public(self):
        app=main.create_app()
        transport=httpx.ASGITransport(app=app,client=('192.0.2.10',1234))
        with patch.dict('os.environ',HUYAN_TOKEN='',HUYAN_ALLOWED_HOSTS='workstation.example'):
            async with httpx.AsyncClient(transport=transport,base_url='https://workstation.example') as client:
                cases=[('/remote-camera.html',{'sec-fetch-site':'none','sec-fetch-dest':'document'},True),
                    ('/assets/remote-camera/remote.js',{'sec-fetch-site':'same-origin'},True),
                    ('/assets/remote-camera/remote.js',{'referer':'https://workstation.example/remote-camera.html'},True),
                    ('/',{'sec-fetch-site':'none'},False),
                    ('/index.html',{'sec-fetch-site':'none'},False),
                    ('/assets/index-control.js',{'sec-fetch-site':'same-origin'},False),
                    ('/api/state',{'sec-fetch-site':'same-origin'},False),
                    ('/remote-camera.html',{'sec-fetch-site':'cross-site'},False),
                    ('/assets/remote-camera/remote.js',{'sec-fetch-site':'cross-site'},False),
                    ('/assets/remote-camera/remote.js',{'sec-fetch-site':'same-origin','origin':'https://evil.invalid'},False),
                    ('/assets/remote-camera/%2e%2e/index.js',{'sec-fetch-site':'same-origin'},False),
                    ('/assets/remote-camera/a%2f..%2findex.js',{'sec-fetch-site':'same-origin'},False)]
                for path,headers,allowed in cases:
                    with self.subTest(path=path,headers=headers):
                        response=await client.get(path,headers=headers)
                        self.assertEqual(response.status_code!=403,allowed,response.text[:100])
                self.assertEqual((await client.get('/remote-camera.html',headers={'host':'evil.invalid','sec-fetch-site':'none'})).status_code,400)
                self.assertEqual((await client.head('/remote-camera.html',headers={'sec-fetch-site':'none'})).status_code,403)
            async with httpx.AsyncClient(transport=transport,base_url='http://workstation.example') as client:
                self.assertEqual((await client.get('/remote-camera.html',headers={'sec-fetch-site':'none'})).status_code,403)


if __name__=='__main__':unittest.main(verbosity=2)
