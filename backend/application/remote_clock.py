"""Bounded heartbeat clock estimate; never a camera capture-time certificate."""
import math
import secrets


class RemoteClock:
    def __init__(self, clock):
        self.clock = clock
        self.clear()

    def clear(self):
        self.pending = None
        self.samples = []

    @staticmethod
    def number(value):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 2**52:
            raise ValueError('远程时钟数值无效')
        return float(value)

    def exchange(self, value):
        if not isinstance(value, dict) or set(value) - {'sent_ms', 'previous'} or 'sent_ms' not in value:
            raise ValueError('远程时钟字段无效')
        sent = self.number(value['sent_ms'])
        now = self.clock() * 1000
        previous = value.get('previous')
        if previous is not None:
            pending, self.pending = self.pending, None
            if (not isinstance(previous, dict) or set(previous) != {'nonce', 'received_ms'}
                    or not pending or previous['nonce'] != pending['nonce']
                    or not 0 <= now - pending['server_sent_ms'] <= 8000):
                self.samples.clear()
                raise ValueError('远程时钟回执已失效')
            received = self.number(previous['received_ms'])
            elapsed = received - pending['client_sent_ms']
            processing = pending['server_sent_ms'] - pending['server_received_ms']
            rtt = elapsed - processing
            if not 0 <= rtt <= 5000 or sent < received:
                self.samples.clear()
                raise ValueError('远程时钟往返时间无效')
            self.samples.append(dict(
                offset_ms=((pending['server_received_ms'] - pending['client_sent_ms'])
                           + (pending['server_sent_ms'] - received)) / 2,
                uncertainty_ms=rtt / 2, round_trip_ms=rtt,
                measured_at_ms=pending['server_sent_ms']))
            self.samples = self.samples[-8:]
        server_sent = self.clock() * 1000
        self.pending = dict(nonce=secrets.token_urlsafe(16), client_sent_ms=sent,
                            server_received_ms=now, server_sent_ms=server_sent)
        return dict(self.pending)

    def snapshot(self):
        now = self.clock() * 1000
        self.samples = [sample for sample in self.samples if 0 <= now - sample['measured_at_ms'] < 10000]
        best = min(self.samples, key=lambda sample: sample['uncertainty_ms']) if self.samples else None
        return dict(status='estimated' if best else 'unknown',
                    scope='heartbeat_round_trip',
                    offset_ms=best['offset_ms'] if best else None,
                    uncertainty_ms=best['uncertainty_ms'] if best else None,
                    round_trip_ms=best['round_trip_ms'] if best else None,
                    measurement_age_ms=now-best['measured_at_ms'] if best else None,
                    capture_age_available=False, control_qualified=False)
