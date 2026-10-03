#!/usr/bin/env python3
"""Fail closed on expired, incomplete or inconsistent published snapshots."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from collector import ASSETS, INTERVAL_LIMITS, INTERVAL_MS, SNAPSHOT_MAX_AGE_MS


def timestamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp has no timezone')
    return int(dt.timestamp() * 1000)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def verify(folder='data', now=None):
    folder = Path(folder)
    now = int(datetime.now(timezone.utc).timestamp() * 1000) if now is None else now
    status = json.loads((folder / 'status.json').read_text())
    require(status['successful_frames'] == status['total_frames'] == 36, 'Expected 36/36 frames')
    require(set(status['assets']) == set(ASSETS), 'Expected exactly six active assets')
    require(0 <= now - timestamp(status['captured_at_utc']) < SNAPSHOT_MAX_AGE_MS, 'Snapshot stale or future dated')
    require(timestamp(status['captured_at_utc']) <= timestamp(status['generated_at_utc']) <= now, 'Invalid generation time')
    report = []
    for asset in ASSETS:
        p = json.loads((folder / (asset + '.json')).read_text())
        meta = status['assets'][asset]
        actual_meta = {k: v for k, v in p.items() if k != 'frames'}
        actual_meta['frames'] = {i: {k: v for k, v in f.items() if k != 'candles'} for i, f in p['frames'].items()}
        require(actual_meta == meta, asset + ': status/file mismatch')
        require(p['asset'] == asset and p['status'] == 'OK', asset + ': not OK')
        require(p['exchange'] in ('Binance', 'Bybit'), asset + ': invalid exchange')
        require(set(p['frames']) == set(INTERVAL_LIMITS), asset + ': missing frames')
        started = timestamp(p['collection_started_at_utc'])
        captured = timestamp(p['capture_time_utc'])
        require(started <= captured <= now < timestamp(p['expires_at_utc']) <= started + SNAPSHOT_MAX_AGE_MS,
                asset + ': invalid capture/expiry')
        for interval, f in p['frames'].items():
            prefix = asset + '/' + interval
            cutoff = timestamp(f['observation_started_at_utc'])
            require(f['status'] == 'OK' and f['exchange'] == p['exchange'] and
                    f['market'] == p['exchange'] + ' Spot' and f['symbol'] == asset + 'USDT' and
                    f['interval'] == interval, prefix + ': provenance mismatch')
            require(started <= cutoff <= timestamp(f['captured_at_utc']) <= captured and
                    now < timestamp(f['expires_at_utc']) <= cutoff + SNAPSHOT_MAX_AGE_MS, prefix + ': expired frame')
            bars = f['candles']
            period = INTERVAL_MS[interval]
            require(len(bars) >= INTERVAL_LIMITS[interval], prefix + ': truncated history')
            for index, bar in enumerate(bars):
                opened = bar['open_ms']
                require(opened % period == 0 and timestamp(bar['open_utc']) == opened and
                        bar['close_ms'] == opened + period - 1 and opened <= cutoff,
                        prefix + ': invalid candle boundaries')
                require(bar['complete'] is (bar['close_ms'] < cutoff - 1000), prefix + ': incorrect complete flag')
                if index:
                    require(opened - bars[index - 1]['open_ms'] == period, prefix + ': candle gap')
            require(bars[-1]['open_ms'] >= (cutoff - 5000) // period * period, prefix + ': stale candle')
            closed = [b for b in bars if b['complete']]
            require(bool(closed) and closed[-1]['close_ms'] + 1 >= (cutoff - 1000) // period * period,
                    prefix + ': missing closed candle')
            require(len(closed) == f['closed_candles'] and
                    timestamp(f['last_completed_close_utc']) == closed[-1]['close_ms'] + 1,
                    prefix + ': closed candle metadata mismatch')
        hourly = p['frames']['1h']['candles']
        # Recollect a previous-hour snapshot even when its 120-second TTL has not expired.
        require(hourly[-1]['open_ms'] == now // 3600000 * 3600000,
                asset + ': current hourly candle missing')
        require(hourly[-1]['complete'] is False and hourly[-2]['complete'] is True,
                asset + ': hourly closure not ready')
        report.append(dict(asset=asset, exchange=p['exchange'], capture_time_utc=p['capture_time_utc'],
                           latest_1h=hourly[-1]['open_utc'], previous_complete=hourly[-2]['complete'],
                           current_complete=hourly[-1]['complete']))
    return dict(successful_frames=36, captured_at_utc=status['captured_at_utc'], assets=report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default='data')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.data_dir), indent=2))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        parser.exit(1, 'Snapshot invalid: ' + str(exc) + '\n')
