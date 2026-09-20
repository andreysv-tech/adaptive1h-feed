#!/usr/bin/env python3
"""Public Binance OHLCV -> versioned GitHub markdown and JSON snapshots.

No API keys, personal account, execution, or third-party Python packages.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ASSETS = ('BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'HYPE', 'BNB')
INTERVAL_LIMITS = {'1m': 120, '5m': 160, '15m': 160, '1h': 240, '4h': 120, '1d': 80}
INTERVAL_MS = {'1m': 60_000, '5m': 300_000, '15m': 900_000,
               '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000}
SPOT_HOSTS = ('https://data-api.binance.vision', 'https://api.binance.com',
              'https://api1.binance.com')
FUTURES_HOSTS = ('https://fapi.binance.com',)
USER_AGENT = 'Adaptive1H-Public-Candle-Collector/1.0'
OUTPUT_DIR = Path(os.environ.get('OHLCV_OUTPUT_DIR', 'data'))


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def local(ms):
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo('Asia/Jerusalem')).isoformat(timespec='seconds')


def request_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.load(response)


def get_klines(asset, interval, capture_ms):
    symbol = asset + 'USDT'
    future = asset == 'HYPE'
    path = '/fapi/v1/klines' if future else '/api/v3/klines'
    query = urllib.parse.urlencode({'symbol': symbol, 'interval': interval,
                                   'limit': INTERVAL_LIMITS[interval]})
    failures = []
    for host in FUTURES_HOSTS if future else SPOT_HOSTS:
        url = host + path + '?' + query
        try:
            rows = request_json(url)
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
                raise ValueError('API did not return a nonempty kline array')
            candles = []
            seen = set()
            for raw in rows:
                if len(raw) < 11:
                    raise ValueError('Incomplete Binance kline')
                opened, closed = int(raw[0]), int(raw[6])
                if opened in seen:
                    raise ValueError('Duplicate opening timestamp')
                seen.add(opened)
                o, h, l, c, volume, taker = map(float, (raw[1], raw[2], raw[3], raw[4], raw[5], raw[9]))
                if not (l <= min(o, c) <= max(o, c) <= h and l >= 0 and volume >= 0 and taker >= 0):
                    raise ValueError('Invalid OHLCV numeric relationship')
                if closed - opened + 1 != INTERVAL_MS[interval]:
                    raise ValueError('Unexpected kline time boundaries')
                candles.append({'open_ms': opened, 'open_utc': utc(opened),
                                'close_ms': closed, 'open': o, 'high': h, 'low': l,
                                'close': c, 'volume': volume, 'taker_buy_base': taker,
                                'trades': int(raw[8]),
                                'complete': closed < capture_ms - 1000})
            candles.sort(key=lambda x: x['open_ms'])
            if candles[-1]['open_ms'] > capture_ms + 1000:
                raise ValueError('Future-dated candle')
            for prev, nxt in zip(candles, candles[1:]):
                if nxt['open_ms'] - prev['open_ms'] != INTERVAL_MS[interval]:
                    raise ValueError('Candle gap or inconsistent interval')
            complete = [x for x in candles if x['complete']]
            if not complete:
                raise ValueError('No fully closed candle')
            last_end = complete[-1]['close_ms'] + 1
            # A freshly opened period naturally has just one completed previous candle.
            stale = capture_ms - last_end >= INTERVAL_MS[interval] + 120_000
            return {'status': 'STALE' if stale else 'OK', 'source': host + path,
                    'market': 'Binance USD-M Futures' if future else 'Binance Spot',
                    'symbol': symbol, 'interval': interval, 'captured_at_utc': utc(capture_ms),
                    'last_completed_close_utc': utc(last_end), 'closed_candles': len(complete),
                    'candles': candles, 'errors_before_success': failures}
        except (OSError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as exc:
            failures.append({'host': host, 'error': f'{type(exc).__name__}: {str(exc)[:160]}'})
    return {'status': 'ERROR', 'market': 'Binance USD-M Futures' if future else 'Binance Spot',
            'symbol': symbol, 'interval': interval, 'captured_at_utc': utc(capture_ms),
            'errors': failures, 'candles': []}


def fmt(x):
    return f'{x:.10g}'


def make_asset_markdown(asset, intervals, capture_ms):
    lines = [f'# {asset}USDT — Adaptive 1H candles', '',
             f'Collection: {utc(capture_ms)} UTC / {local(capture_ms)} Israel',
             f'Market: {"Binance USD-M Futures" if asset == "HYPE" else "Binance Spot"}',
             'These are market data, NOT a forecast or Polymarket execution quote.', '']
    for interval, item in intervals.items():
        lines.extend([f'## {interval} — {item["status"]}', '',
                      f'Source: `{item.get("source", "UNAVAILABLE")}`',
                      f'Last completed close: {item.get("last_completed_close_utc", "UNAVAILABLE")}', ''])
        if item['status'] == 'ERROR':
            lines.append('Errors: ' + json.dumps(item['errors'], ensure_ascii=False))
            lines.append('')
            continue
        lines.extend(['| Open UTC | O | H | L | C | Volume | Taker buy | Closed? |',
                      '|---|---:|---:|---:|---:|---:|---:|---|'])
        # Full history remains available in machine-readable JSON. Markdown is a readable excerpt.
        for c in item['candles'][-55:]:
            lines.append(f'| {c["open_utc"]} | {fmt(c["open"])} | {fmt(c["high"])} | '
                         f'{fmt(c["low"])} | {fmt(c["close"])} | {fmt(c["volume"])} | '
                         f'{fmt(c["taker_buy_base"])} | {"yes" if c["complete"] else "no"} |')
        lines.append('')
    return '\n'.join(lines) + '\n'


def collect(capture_ms=None, output=None):
    capture_ms = capture_ms if capture_ms is not None else int(time.time() * 1000)
    output = Path(output) if output is not None else OUTPUT_DIR
    output.mkdir(parents=True, exist_ok=True)
    summary = {'captured_at_utc': utc(capture_ms), 'captured_at_israel': local(capture_ms),
               'purpose': 'public candle data, not trading signals', 'assets': {}}
    successful = 0
    for asset in ASSETS:
        frames = {}
        for interval in INTERVAL_LIMITS:
            frames[interval] = get_klines(asset, interval, capture_ms)
            if frames[interval]['status'] == 'OK':
                successful += 1
        summary['assets'][asset] = {'market': frames['1h']['market'],
                                     'frames': {key: {'status': item['status'],
                                        'last_completed_close_utc': item.get('last_completed_close_utc'),
                                        'closed_candles': item.get('closed_candles', 0),
                                        'source': item.get('source'), 'errors': item.get('errors', [])}
                                        for key, item in frames.items()}}
        payload = {'asset': asset, 'capture_time_utc': utc(capture_ms), 'frames': frames}
        (output / f'{asset}.json').write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf8')
        (output / f'{asset}.md').write_text(make_asset_markdown(asset, frames, capture_ms), encoding='utf8')
    summary['successful_frames'] = successful
    summary['total_frames'] = len(ASSETS) * len(INTERVAL_LIMITS)
    (output / 'status.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    lines = ['# Adaptive 1H public market-data status', '',
             f'Collected: **{summary["captured_at_israel"]}** Israel / {summary["captured_at_utc"]} UTC',
             f'Available fresh frames: **{successful}/{summary["total_frames"]}**.',
             'No API key is used. Only actual responses are shown; no synthetic candles.', '',
             '| Asset | Market | 1m | 5m | 15m | 1h | 4h | 1d |',
             '|---|---|---|---|---|---|---|---|']
    for asset in ASSETS:
        info = summary['assets'][asset]
        stat = ' | '.join(f'[{info["frames"][i]["status"]}]({asset}.md)' for i in INTERVAL_LIMITS)
        lines.append(f'| {asset} | {info["market"]} | {stat} |')
    lines.extend(['', 'Click any asset to view actual OHLCV rows and source timestamps.',
                  'Machine-readable complete snapshots are in `<ASSET>.json`.',
                  'An ERROR is a data-access problem, not a No Trade signal.'])
    (output / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf8')
    print(f'Collection: {summary["captured_at_utc"]}; fresh frames={successful}/{summary["total_frames"]}', flush=True)
    # Commit the diagnostic even if sources fail, so a failed collection is visible.
    return successful


if __name__ == '__main__':
    try:
        collect()
    except Exception as exc:
        print('FATAL:', type(exc).__name__, str(exc), file=sys.stderr)
        raise
