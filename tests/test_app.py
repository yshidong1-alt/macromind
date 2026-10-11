import json
import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from app import create_app, now


class MacroMindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = dict(TESTING=True, DB_PATH=self.tmp.name+'/test.sqlite3', APP_PASSWORD='test-access-password',
                           SECRET_KEY='test-session-secret', START_SCHEDULER=False, FRED_API_KEY='', TWELVE_DATA_API_KEY='', MARKET_SYMBOLS_JSON='{}')
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.login()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self):
        r = self.client.post('/api/session', json={'password':'test-access-password'})
        self.assertEqual(r.status_code, 200)
        self.headers = {'X-CSRF-Token':r.json['csrf']}

    def payload(self):
        return dict(answers=['增长与通胀需要更多证据。','观察政策通过利率传导。','下周检查反证并重新评估。'], asset='gold', direction='neutral', confidence=50)

    def test_health_and_private_data(self):
        anonymous=self.app.test_client()
        self.assertEqual(anonymous.get('/api/health').json['status'], 'ok')
        self.assertEqual(anonymous.get('/api/judgments').status_code, 401)
        self.assertEqual(anonymous.get('/api/export').status_code, 401)
        self.assertEqual(self.client.post('/api/judgments',json=self.payload()).status_code, 403)

    def test_missing_keys_are_not_fabricated(self):
        for endpoint, count in [('/api/market',11),('/api/macro',17)]:
            r=self.client.get(endpoint)
            self.assertEqual(r.status_code,200)
            self.assertEqual(len(r.json['items']),count)
            self.assertTrue(all(x['value'] is None and x['status']=='UNCONFIGURED' for x in r.json['items']))
        b=self.client.get('/api/briefing').json
        self.assertEqual(b['status'],'INSUFFICIENT_DATA')
        self.assertEqual(b['observations'],[])
        self.assertIsNone(b['macro_score'])

    def test_persistence_and_immutable_snapshot_after_restart(self):
        r=self.client.post('/api/judgments',json=self.payload(),headers=self.headers)
        self.assertEqual(r.status_code,201)
        key=r.json['id']
        self.app=create_app(self.config)
        self.client=self.app.test_client()
        self.login()
        items=self.client.get('/api/judgments').json['items']
        self.assertEqual(items[0]['id'],key)
        self.assertEqual(items[0]['payload'],self.payload())
        self.assertEqual(len(items[0]['snapshot']['market']),11)
        self.assertEqual(self.client.post('/api/judgments',json=self.payload(),headers=self.headers).status_code,409)
        self.assertEqual(self.client.get('/api/export').json['judgments'][0]['id'],key)

    def test_invalid_inputs_and_early_review(self):
        for invalid in [None, [], {}, dict(self.payload(),confidence=True),dict(self.payload(),answers=['x']*3),dict(self.payload(),asset='unknown')]:
            self.assertEqual(self.client.post('/api/judgments',json=invalid,headers=self.headers).status_code,400)
        key=self.client.post('/api/judgments',json=self.payload(),headers=self.headers).json['id']
        self.assertEqual(self.client.post('/api/judgments/'+key+'/reviews',json={'horizon':7,'notes':'复盘内容需要真实证据'},headers=self.headers).status_code,409)

    def test_review_survives_restart(self):
        key=self.client.post('/api/judgments',json=self.payload(),headers=self.headers).json['id']
        with self.app.extensions['db']() as con:
            con.execute('UPDATE judgments SET day=? WHERE id=?',((now()-timedelta(days=40)).date().isoformat(),key))
        r=self.client.post('/api/judgments/'+key+'/reviews',json={'horizon':7,'notes':'用实际发生的数据检查原来判断'},headers=self.headers)
        self.assertEqual(r.status_code,200)
        self.app=create_app(self.config);self.client=self.app.test_client();self.login()
        self.assertEqual(self.client.get('/api/judgments').json['items'][0]['reviews'][0]['horizon'],7)

    def test_provider_success_and_failure_preserves_cache(self):
        self.app.config['FRED_API_KEY']='fixture-only-not-a-real-key'
        observation=(now()-timedelta(days=2)).date().isoformat()
        response={'observations':[{'date':observation,'value':'123.45','realtime_start':observation,'realtime_end':observation}]}
        with patch('app.fetch_json',return_value=response):
            self.app.extensions['refresh']('market')
        item=self.client.get('/api/market').json['items'][0]
        self.assertEqual(item['value'],123.45)
        self.assertEqual(item['status'],'EOD')
        self.assertEqual(item['observation_date'],observation)
        with patch('app.fetch_json',side_effect=ValueError('fixture failure')):
            self.app.extensions['refresh']('market')
        item=self.client.get('/api/market').json['items'][0]
        self.assertEqual(item['value'],123.45)
        self.assertEqual(item['status'],'STALE')
        self.assertNotIn('fixture-only',json.dumps(self.client.get('/api/status').json))

    def test_stale_and_future_dates(self):
        self.app.config['FRED_API_KEY']='fixture-key'
        for days, expected in [(30,'STALE'),(-1,'ERROR')]:
            with self.app.extensions['db']() as con:con.execute('DELETE FROM cache')
            response={'observations':[{'date':(now()-timedelta(days=days)).date().isoformat(),'value':'123'}]}
            with patch('app.fetch_json',return_value=response):self.app.extensions['refresh']('market')
            self.assertEqual(self.client.get('/api/market').json['items'][0]['status'],expected)

    def test_daily_bar_excludes_current_day(self):
        cfg=dict(self.config,TWELVE_DATA_API_KEY='fixture-key',MARKET_SYMBOLS_JSON='{"gold":"fixture-symbol"}')
        app=create_app(cfg)
        rows={'values':[{'datetime':now().date().isoformat(),'close':'999'}, {'datetime':(now()-timedelta(days=1)).date().isoformat(),'close':'111'}]}
        with patch('app.fetch_json',return_value=rows),patch('app.time.sleep'):
            app.extensions['refresh']('market')
        gold=next(x for x in app.extensions['data_for']('market') if x['id']=='gold')
        self.assertEqual(gold['value'],111)
        self.assertEqual(gold['status'],'EOD')

    def test_production_fails_closed(self):
        with patch.dict(os.environ,{'APP_ENV':'production','SESSION_SECRET':''}):
            with self.assertRaises(RuntimeError):create_app(self.config)


if __name__=='__main__':unittest.main()
