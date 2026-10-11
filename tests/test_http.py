"""Real HTTP and process restart test. Uses disposable data and no live providers."""
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, build_opener, HTTPCookieProcessor


class HttpRestartTests(unittest.TestCase):
    def test_gunicorn_restart_persists_judgment(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env=dict(os.environ, PORT=str(port), DB_PATH=tmp+'/test.sqlite3', APP_ENV='development',
                     APP_PASSWORD='http-test-only-password', SESSION_SECRET='http-test-only-session-secret',
                     ENABLE_SCHEDULER='false', FRED_API_KEY='', TWELVE_DATA_API_KEY='',MARKET_SYMBOLS_JSON='{}',PYTHONDONTWRITEBYTECODE='1')
            url='http://127.0.0.1:'+str(port)
            opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def call(path, body=None, csrf=None):
                headers={'Content-Type':'application/json'}
                if csrf:headers['X-CSRF-Token']=csrf
                req=Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
                with opener.open(req,timeout=5) as response:return json.load(response)
            def start():
                process=subprocess.Popen([sys.executable,'-m','gunicorn','-c','gunicorn.conf.py','wsgi:app'],cwd=str(root),env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                for _ in range(80):
                    try:
                        if call('/api/health')['status']=='ok':return process
                    except (URLError,OSError):pass
                    if process.poll() is not None:raise AssertionError(process.stderr.read().decode())
                    time.sleep(.1)
                process.terminate();process.wait(timeout=10)
                raise AssertionError('server did not become healthy')
            def stop(process):
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                process.stderr.close()
            process=start()
            try:
                csrf=call('/api/session',{'password':'http-test-only-password'})['csrf']
                for endpoint in ['/api/briefing','/api/market','/api/macro','/api/macro/cpi']:
                    self.assertIsInstance(call(endpoint),dict)
                record=call('/api/judgments',dict(answers=['测试记录：增长证据需要核验','测试记录：政策经利率传导','测试记录：七天后复盘反证'],asset='gold',direction='neutral',confidence=50),csrf)
            finally:stop(process)
            process=start()
            try:
                csrf=call('/api/session',{'password':'http-test-only-password'})['csrf']
                self.assertEqual(call('/api/judgments')['items'][0]['id'],record['id'])
                self.assertEqual(call('/api/export')['judgments'][0]['id'],record['id'])
            finally:stop(process)


if __name__=='__main__':unittest.main()
