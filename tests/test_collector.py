import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector as c

CAPTURE = 1789989870000


def api(url):
    q = parse_qs(urlparse(url).query)
    symbol = q['symbol'][0]
    asset = symbol[:-4]
    bybit = 'bybit' in url
    if 'exchangeInfo' in url:
        return {'symbols': [dict(symbol=symbol, baseAsset=asset, quoteAsset='USDT', status='TRADING', isSpotTradingAllowed=True)]}
    if 'instruments-info' in url:
        return dict(retCode=0, result=dict(category='spot', list=[dict(symbol=symbol, baseCoin=asset, quoteCoin='USDT', status='Trading')]))
    interval = q['interval'][0]
    if bybit:
        interval = next(i for i,v in c.BYBIT_INTERVALS.items() if v == interval)
    period = c.INTERVAL_MS[interval]
    count = int(q['limit'][0])
    cutoff = int(q['end' if bybit else 'endTime'][0])
    rows = []
    for n in range(count):
        opened = (cutoff//period-count+1+n)*period
        price = '202' if bybit else '102'
        rows.append([str(opened), price, '205', '99', price, '50', '10000'] if bybit else
                    [opened,price,'205','99',price,'50',opened+period-1,'10000',10,'20','4000'])
    return dict(retCode=0,result=dict(category='spot',symbol=symbol,list=rows[::-1])) if bybit else rows


class CollectorTests(unittest.TestCase):
    def test_all_36_frames_spot_only(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(c,'request_json',side_effect=api) as req:
            self.assertEqual(c.collect(CAPTURE,folder),36)
            status=json.loads((Path(folder)/'status.json').read_text())
            self.assertEqual(set(status['assets']),set(c.ASSETS))
            self.assertEqual(len(status['assets']),6)
            self.assertEqual(status['total_frames'],36)
            self.assertTrue(all('bybit' not in call.args[0] for call in req.call_args_list))
            self.assertIn('Closed?',(Path(folder)/'BTC.md').read_text())

    def test_binance_failure_uses_bybit_with_null_fields(self):
        def service(url):
            if 'binance' in url: raise OSError('Binance down')
            return api(url)
        with patch.object(c,'request_json',side_effect=service): p=c.collect_asset('BTC',CAPTURE)
        self.assertEqual(p['exchange'],'Bybit')
        self.assertEqual(p['status'],'OK')
        for f in p['frames'].values():
            self.assertNotIn('taker_buy_base',f['available_fields'])
            self.assertTrue(all(x['taker_buy_base'] is None and x['trades'] is None for x in f['candles']))

    def test_bybit_failure_does_not_break_healthy_primary(self):
        def service(url):
            if 'bybit' in url: raise OSError('Bybit down')
            return api(url)
        with patch.object(c,'request_json',side_effect=service):
            self.assertEqual(c.collect_asset('BTC',CAPTURE)['exchange'],'Binance')
            self.assertEqual(c.collect_asset('BTC',CAPTURE,'Bybit')['status'],'ERROR')

    def test_both_providers_fail(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(c,'request_json',side_effect=OSError('offline')):
            self.assertEqual(c.collect(CAPTURE,folder),0)
            report=(Path(folder)/'README.md').read_text()
            self.assertIn('0/36',report)
            self.assertIn('ERROR',report)

    def test_stale_binance_falls_back(self):
        def service(url):
            r=api(url)
            if '/api/v3/klines' in url:
                for row in r:row[0]-=864000000;row[6]-=864000000
            return r
        with patch.object(c,'request_json',side_effect=service): p=c.collect_asset('SOL',CAPTURE)
        self.assertEqual(p['exchange'],'Bybit')
        self.assertEqual(p['attempts'][0]['status'],'STALE')

    def test_both_stale_never_ok(self):
        def service(url):
            r=api(url)
            if '/klines?' in url:
                for row in r:row[0]-=864000000;row[6]-=864000000
            elif '/kline?' in url:
                for row in r['result']['list']:row[0]=str(int(row[0])-864000000)
            return r
        with patch.object(c,'request_json',side_effect=service): p=c.collect_asset('SOL',CAPTURE)
        self.assertEqual(p['status'],'STALE')
        self.assertTrue(all(not f['candles'] for f in p['frames'].values()))

    def test_missing_pair_no_candle_request(self):
        def service(url):
            if 'bybit' not in url: raise OSError('primary down')
            if 'instruments-info' in url:return dict(retCode=0,result=dict(category='spot',list=[]))
            self.fail('Must not query a missing pair')
        with patch.object(c,'request_json',side_effect=service): p=c.collect_asset('BNB',CAPTURE)
        self.assertEqual(p['status'],'ERROR')
        self.assertEqual(p['attempts'][-1]['status'],'SYMBOL_UNAVAILABLE')

    def test_reverse_order_and_duplicates(self):
        with patch.object(c,'request_json',side_effect=api):p=c.collect_asset('ETH',CAPTURE,'Bybit')
        bars=p['frames']['1h']['candles']
        self.assertEqual([b['open_ms'] for b in bars],sorted(b['open_ms'] for b in bars))
        url='https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=60&limit=240&end='+str(CAPTURE)
        raw=api(url)['result']['list'];raw[0]=raw[1]
        with self.assertRaisesRegex(c.FeedError,'Duplicate'):c.normalize(raw,'Bybit','1h',CAPTURE)

    def test_hour_closure_and_incomplete_bar(self):
        boundary=(CAPTURE//3600000)*3600000
        with patch.object(c,'request_json',side_effect=api):
            at=c.fetch_frame('Binance',c.SPOT_HOSTS[0],'BTC','1h',boundary)
            after=c.fetch_frame('Binance',c.SPOT_HOSTS[0],'BTC','1h',boundary+1000)
        self.assertFalse(at['candles'][-2]['complete'])
        self.assertTrue(after['candles'][-2]['complete'])
        self.assertFalse(after['candles'][-1]['complete'])
        self.assertEqual(after['last_completed_close_utc'],c.utc(boundary))

    def test_partial_primary_discarded_no_mixing(self):
        def service(url):
            if 'binance' in url and 'interval=1h&' in url:raise OSError('late frame fails')
            return api(url)
        with patch.object(c,'request_json',side_effect=service):p=c.collect_asset('BTC',CAPTURE)
        self.assertEqual(p['exchange'],'Bybit')
        self.assertTrue(all(f['exchange']=='Bybit' for f in p['frames'].values()))
        self.assertTrue(all(b['close']==202 for f in p['frames'].values() for b in f['candles']))

    def test_api_error_and_wrong_symbol_rejected(self):
        for result in [dict(retCode=10001,retMsg='bad pair'),dict(retCode=0,result=dict(category='linear',list=[]))]:
            with self.assertRaises(c.FeedError):c.bybit_result(result)

    def test_nonfinite_values_rejected(self):
        url='https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=120&endTime='+str(CAPTURE)
        raw=api(url);raw[0][2]='nan'
        with self.assertRaises(c.FeedError):c.normalize(raw,'Binance','1m',CAPTURE)

if __name__=='__main__':unittest.main()
