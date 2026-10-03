import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collector as c
from test_collector import CAPTURE, api
from verify_snapshot import verify


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        with patch.object(c, 'request_json', side_effect=api):
            c.collect(CAPTURE, self.folder)

    def mutate(self, filename, change):
        path = self.folder / filename
        obj = json.loads(path.read_text())
        change(obj)
        path.write_text(json.dumps(obj))

    def test_valid_all_six_and_expiry_boundary(self):
        self.assertEqual(len(verify(self.folder, CAPTURE)['assets']), 6)
        verify(self.folder, CAPTURE + 119999)
        with self.assertRaisesRegex(ValueError, 'stale'):
            verify(self.folder, CAPTURE + 120000)

    def test_missing_asset_and_incomplete_count(self):
        self.mutate('status.json', lambda p: p.update(successful_frames=35))
        with self.assertRaises(ValueError): verify(self.folder, CAPTURE)
        self.mutate('status.json', lambda p: p.update(successful_frames=36))
        (self.folder / 'DOGE.json').unlink()
        with self.assertRaises(OSError): verify(self.folder, CAPTURE)

    def test_status_does_not_hide_old_asset_file(self):
        self.mutate('ETH.json', lambda p: p.update(capture_time_utc=c.utc(CAPTURE-3600000)))
        with self.assertRaisesRegex(ValueError, 'mismatch'): verify(self.folder, CAPTURE)

    def test_mixed_exchange_rejected_even_with_matching_status(self):
        for filename in ('BTC.json', 'status.json'):
            def change(p):
                a = p['assets']['BTC'] if 'assets' in p else p
                a['frames']['1h']['exchange'] = 'Bybit'
            self.mutate(filename, change)
        with self.assertRaisesRegex(ValueError, 'provenance'): verify(self.folder, CAPTURE)

    def test_hourly_flags_and_missing_candle(self):
        def change(p): p['frames']['1h']['candles'][-1]['complete'] = True
        self.mutate('SOL.json', change)
        with self.assertRaisesRegex(ValueError, 'complete'): verify(self.folder, CAPTURE)

    def test_rollover_requires_new_hour_even_inside_ttl(self):
        boundary = CAPTURE // 3600000 * 3600000
        capture = boundary + 3600000 - 10000
        with patch.object(c, 'request_json', side_effect=api): c.collect(capture, self.folder)
        with self.assertRaisesRegex(ValueError, 'current hourly'): verify(self.folder, capture + 15000)

    def test_future_capture_rejected(self):
        with self.assertRaisesRegex(ValueError, 'future'): verify(self.folder, CAPTURE-1)

    def test_missing_frame_rejected(self):
        def change(p):
            a = p['assets']['XRP'] if 'assets' in p else p
            del a['frames']['1m']
        for f in ('XRP.json', 'status.json'): self.mutate(f, change)
        with self.assertRaisesRegex(ValueError, 'missing frames'): verify(self.folder, CAPTURE)

    def test_bybit_whole_asset_still_valid(self):
        with patch.object(c, 'request_json', side_effect=api): c.collect(CAPTURE, self.folder, 'Bybit')
        self.assertTrue(all(a['exchange'] == 'Bybit' for a in verify(self.folder, CAPTURE)['assets']))
