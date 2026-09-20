import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector

CAPTURE = 1_780_000_000_000  # synthetic fixture clock, never real market data


def fake_klines(url):
    from urllib.parse import parse_qs, urlparse
    args = parse_qs(urlparse(url).query)
    interval = args['interval'][0]
    period = collector.INTERVAL_MS[interval]
    count = int(args['limit'][0])
    start = (CAPTURE // period - count + 1) * period
    result = []
    for i in range(count):
        opened = start + i * period
        result.append([opened, '101', '103', '99', '102', '99.3', opened + period - 1,
                       '10000', 120, '40.1', '4000', '0'])
    return result


class CollectorTests(unittest.TestCase):
    def test_all_42_frames_and_futures_route(self):
        calls = []
        def service(url):
            calls.append(url)
            return fake_klines(url)
        with tempfile.TemporaryDirectory() as temp, patch.object(collector, 'request_json', side_effect=service):
            result = collector.collect(CAPTURE, temp)
            self.assertEqual(result, 42)
            status = json.loads((Path(temp) / 'status.json').read_text())
            self.assertEqual(status['successful_frames'], 42)
            self.assertEqual(len(status['assets']), 7)
            h = json.loads((Path(temp) / 'HYPE.json').read_text())
            b = json.loads((Path(temp) / 'BTC.json').read_text())
            self.assertIn('/fapi/v1/klines', h['frames']['1h']['source'])
            self.assertIn('/api/v3/klines', b['frames']['1h']['source'])
            self.assertTrue(all('HYPEUSDT' in url for url in calls if '/fapi/' in url))
            self.assertEqual(len(calls), 42)
            self.assertIn('Closed?', (Path(temp) / 'BTC.md').read_text())

    def test_failure_is_error_not_no_trade(self):
        def failed(url):
            raise OSError('synthetic DNS outage')
        with tempfile.TemporaryDirectory() as temp, patch.object(collector, 'request_json', side_effect=failed):
            self.assertEqual(collector.collect(CAPTURE, temp), 0)
            report = (Path(temp) / 'README.md').read_text()
            self.assertIn('ERROR', report)
            self.assertIn('0/42', report)
            status = json.loads((Path(temp) / 'status.json').read_text())
            self.assertEqual(status['assets']['BTC']['frames']['1h']['status'], 'ERROR')

    def test_partial_last_bar_is_marked_incomplete(self):
        with patch.object(collector, 'request_json', side_effect=fake_klines):
            item = collector.get_klines('BTC', '1h', CAPTURE)
        self.assertEqual(item['status'], 'OK')
        self.assertFalse(item['candles'][-1]['complete'])
        self.assertTrue(item['candles'][-2]['complete'])


if __name__ == '__main__':
    unittest.main()
