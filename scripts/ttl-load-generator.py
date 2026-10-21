#!/usr/bin/env python3
"""Paced, namespaced Redis TTL workload. Stop with SIGTERM; never flushes data."""
import json
import os
import secrets
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import redis


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Config:
    keys: int = 10_000
    burst_seconds: float = 5
    pause_seconds: float = 60
    ttl: int = 60
    batch: int = 100
    prefix: str = 'workbench:ttl-load'
    payload_bytes: int = 64

    def validate(self):
        if not (1 <= self.keys <= 1_000_000 and 1 <= self.batch <= 1_000):
            raise ValueError('keys must be 1..1000000; batch must be 1..1000')
        if not (self.burst_seconds > 0 and self.pause_seconds >= 0 and 1 <= self.ttl <= 86400):
            raise ValueError('invalid burst/pause/TTL')
        if not (self.prefix and 1 <= self.payload_bytes <= 4096):
            raise ValueError('prefix and payload size are required')
        return self


def run_burst(client, config, stop, cycle, session, on_progress,
              monotonic=time.monotonic, wait=None):
    """Write each key once using atomic SET NX EX, paced across the whole window."""
    wait = wait or stop.wait
    started = monotonic()
    confirmed = 0
    first_key = last_key = None
    for offset in range(0, config.keys, config.batch):
        size = min(config.batch, config.keys - offset)
        deadline = started + config.burst_seconds * (offset + size) / config.keys
        if wait(max(0, deadline - monotonic())) or stop.is_set():
            break
        with client.pipeline(transaction=False) as pipeline:
            for index in range(offset, offset + size):
                key = f'{config.prefix}:{session}:{cycle:06d}:{index:05d}'
                record = {'id': uuid.uuid4().hex, 'cycle': cycle, 'index': index,
                          'created_at': now(), 'value': secrets.token_hex(config.payload_bytes),
                          'score': secrets.randbelow(1_000_000)}
                pipeline.set(key, json.dumps(record, separators=(',', ':')), ex=config.ttl, nx=True)
                first_key = first_key or key
                last_key = key
            results = pipeline.execute()
        if len(results) != size or not all(results):
            raise RuntimeError('SET NX collision; existing keys were not overwritten')
        confirmed += size
        on_progress(confirmed)
    elapsed = monotonic() - started
    return {'confirmed_keys': confirmed, 'duration_seconds': round(elapsed, 4),
            'complete': confirmed == config.keys, 'first_key': first_key, 'last_key': last_key,
            'overrun_seconds': round(max(0, elapsed - config.burst_seconds), 4)}


def main():
    config = Config(keys=int(os.getenv('LOAD_KEYS', '10000')),
                    burst_seconds=float(os.getenv('LOAD_BURST_SECONDS', '5')),
                    pause_seconds=float(os.getenv('LOAD_PAUSE_SECONDS', '60')),
                    ttl=int(os.getenv('LOAD_TTL_SECONDS', '60')),
                    batch=int(os.getenv('LOAD_BATCH_SIZE', '100')),
                    prefix=os.getenv('LOAD_PREFIX', 'workbench:ttl-load'),
                    payload_bytes=int(os.getenv('LOAD_PAYLOAD_BYTES', '64'))).validate()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    client = redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://source:6379/0'),
                                  decode_responses=True, socket_timeout=3,
                                  socket_connect_timeout=3, health_check_interval=15)
    status_path = Path(os.getenv('LOAD_STATUS_FILE', '/data/ttl-load-status.json'))
    status_path.parent.mkdir(parents=True, exist_ok=True)
    session = uuid.uuid4().hex
    state = {'session': session, 'namespace': f'{config.prefix}:{session}', 'phase': 'STARTING',
             'cycle': 0, 'completed_cycles': 0, 'total_confirmed_keys': 0,
             'keys_per_burst': config.keys, 'burst_seconds': config.burst_seconds,
             'pause_seconds': config.pause_seconds, 'ttl_seconds': config.ttl,
             'started_at': now()}

    def publish():
        state.update(updated_at=now(), heartbeat_epoch=time.time())
        temporary = status_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(status_path)

    def emit(event, **fields):
        print(json.dumps({'time': now(), 'event': event, 'session': session,
                          'cycle': state['cycle'], **fields}, ensure_ascii=False), flush=True)

    emit('started', **{k: state[k] for k in ('namespace', 'keys_per_burst', 'burst_seconds', 'pause_seconds', 'ttl_seconds')})
    publish()
    try:
        while not stop.is_set():
            state.update(cycle=state['cycle'] + 1, phase='GENERATING', confirmed_keys=0, error=None)
            publish()
            last_publish = time.monotonic()

            def progress(count):
                nonlocal last_publish
                state['confirmed_keys'] = count
                if time.monotonic() - last_publish >= .5 or count == config.keys:
                    publish()
                    last_publish = time.monotonic()

            try:
                client.ping()
                emit('burst_started')
                result = run_burst(client, config, stop, state['cycle'], session, progress)
                state['last_burst'] = result
                state['total_confirmed_keys'] += result['confirmed_keys']
                if result['complete']:
                    state['completed_cycles'] += 1
                    emit('burst_complete', **result)
                else:
                    emit('burst_interrupted', **result)
            except Exception as exc:
                # A network failure can make the last batch's outcome uncertain.
                # Never report it as success or replay the backlog in a burst.
                state.update(error=str(exc), phase='ERROR')
                state['total_confirmed_keys'] += state['confirmed_keys']
                emit('burst_failed', error_type=type(exc).__name__, confirmed_keys=state['confirmed_keys'])
            if stop.is_set():
                break
            rest_end = time.monotonic() + config.pause_seconds
            state.update(phase='ERROR' if state.get('error') else 'RESTING',
                         next_burst_epoch=time.time() + config.pause_seconds)
            publish()
            emit('rest_started', pause_seconds=config.pause_seconds, next_burst_epoch=state['next_burst_epoch'])
            while not stop.is_set() and time.monotonic() < rest_end:
                stop.wait(min(1, max(0, rest_end - time.monotonic())))
                publish()
    finally:
        state.update(phase='STOPPED', stopped_at=now())
        publish()
        emit('stopped', total_confirmed_keys=state['total_confirmed_keys'])
        client.close()


if __name__ == '__main__':
    main()
