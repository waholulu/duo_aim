"""Detector protocol, lifecycle and latest-result mailbox. No hardware imports."""
from contextlib import ExitStack, suppress
import json
import math
import os
import signal
import subprocess
import threading
import time

PROTOCOL_VERSION = 1
STARTUP_TIMEOUT = 120.0
ACTIVE_TIMEOUT = 5.0


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def vector(value, length):
    return isinstance(value, list) and len(value) == length and all(finite(v) for v in value)


def validate_event(event):
    """Reject malformed local IPC before it can reach the controller."""
    if not isinstance(event, dict) or type(event.get('protocol_version')) is not int or event['protocol_version'] != PROTOCOL_VERSION:
        raise ValueError('Unsupported detector protocol')
    kind = event.get('type')
    if kind == 'ready':
        valid = (integer(event.get('model_size'), 1) and integer(event.get('warmup_total')) and event['warmup_total'] == 30
                 and finite(event.get('requested_fps')) and event['requested_fps'] > 0)
    elif kind == 'warmup':
        valid = (integer(event.get('gate_epoch')) and integer(event.get('completed'), 1)
                 and event['completed'] <= 30 and integer(event.get('total')) and event['total'] == 30)
    elif kind == 'frame':
        valid = all(integer(event.get(k), 1 if k in ('width', 'height') else 0)
                    for k in ('index', 'gate_epoch', 'width', 'height'))
        timing = ('pts_s', 'host_start_s', 'callback_to_coords_ms', 'pts_to_coords_ms',
                  'pts_to_callback_ms', 'preprocess_ms', 'inference_ms', 'postprocess_ms')
        valid = valid and all(finite(event.get(k)) for k in timing)
        valid = valid and all(event[k] >= 0 for k in ('callback_to_coords_ms', 'preprocess_ms', 'inference_ms', 'postprocess_ms'))
        valid = valid and vector(event.get('crop_xywh'), 4)
        if valid:
            x, y, w, h = event['crop_xywh']
            valid = x >= 0 and y >= 0 and w > 0 and h > 0 and x+w <= event['width'] and y+h <= event['height']
        valid = valid and isinstance(event.get('detections'), list)
        if valid:
            for d in event['detections']:
                if not (isinstance(d, dict) and vector(d.get('head_xy'), 2)
                        and vector(d.get('person_center'), 2) and vector(d.get('bbox_xyxy'), 4)
                        and finite(d.get('confidence')) and 0 <= d['confidence'] <= 1
                        and finite(d.get('head_confidence')) and 0 <= d['head_confidence'] <= 1):
                    valid = False
                    break
                box = d['bbox_xyxy']
                if box[2] <= box[0] or box[3] <= box[1]:
                    valid = False
                    break
    else:
        valid = False
    if not valid:
        raise ValueError(f'Malformed detector event: {kind!r}')
    return event


class Watchdog:
    def __init__(self, started):
        self.started = started
        self.ready = False
        self.active = False
        self.last_progress = started

    def activate(self, active, now):
        self.active = active
        self.last_progress = now

    def check(self, now):
        if not self.ready and now-self.started >= STARTUP_TIMEOUT:
            raise TimeoutError('Detector startup exceeded 120 seconds')
        if self.ready and self.active and now-self.last_progress >= ACTIVE_TIMEOUT:
            raise TimeoutError('Active detector produced no progress for 5 seconds')


class DetectorSession:
    def __init__(self, process, clock=time.monotonic):
        self.process = process
        self.clock = clock
        self.condition = threading.Condition()
        self.frame = None
        self.ready = None
        self.error = None
        self.closed = False
        self.reader_thread = None
        self.active = False
        self.epoch = 0
        self.watchdog = Watchdog(clock())
        self.warmup = 0
        self.last_index = -1

    def start_reader(self):
        reader = threading.Thread(target=self.read, daemon=True)
        reader.start()
        self.reader_thread = reader

    def read(self):
        try:
            for line in self.process.stdout:
                event = validate_event(json.loads(line))
                self.accept(event)
        except Exception as error:
            with self.condition:
                self.error = error
        finally:
            with self.condition:
                self.closed = True
                self.condition.notify_all()

    def accept(self, event):
        with self.condition:
            kind = event['type']
            if kind == 'ready':
                if self.ready is not None:
                    raise ValueError('Duplicate detector readiness')
                self.ready = event
                self.watchdog.ready = True
            else:
                if self.ready is None:
                    raise ValueError('Detector result before readiness')
                if kind == 'warmup':
                    if event['completed'] != self.warmup + 1:
                        raise ValueError('Invalid warmup progress')
                    self.warmup = event['completed']
                else:
                    if self.warmup != 30:
                        raise ValueError('Frame received before warmup completed')
                    if event['index'] <= self.last_index:
                        raise ValueError('Reordered detector frame')
                    self.last_index = event['index']
                    self.frame = event
                if self.active and event['gate_epoch'] == self.epoch:
                    self.watchdog.last_progress = self.clock()
            self.condition.notify_all()

    def transition(self, active, controller, record=None):
        with self.condition:
            if active == self.active:
                return
            self.active = active
            self.epoch += 1
            controller.reset()
            self.frame = None
            self.watchdog.activate(active, self.clock())
            event = dict(type='gate', active=active, epoch=self.epoch, at_s=self.clock())
        if record:
            record(event)
        self.process.stdin.write(f'{int(active)} {self.epoch}\n')
        self.process.stdin.flush()

    def check(self):
        with self.condition:
            if self.error is not None:
                raise RuntimeError(f'Detector stream failed: {self.error}') from self.error
            if self.closed:
                raise RuntimeError(f'Detector exited unexpectedly ({self.process.poll()})')
            self.watchdog.check(self.clock())

    def take(self, timeout=.01):
        with self.condition:
            self.condition.wait_for(lambda: self.frame is not None or self.closed, timeout=timeout)
            frame = self.frame
            self.frame = None
            return frame

    def close(self):
        process = self.process
        # Stop the producer before closing its pipes. Sending a final gate command
        # here could block forever if the child is no longer reading stdin.
        def close_pipe(stream):
            # TextIOWrapper may retry a buffered gate write after the child exited.
            with suppress(BrokenPipeError):
                stream.close()
        with ExitStack() as streams:
            for stream in (process.stdin, process.stdout):
                streams.callback(close_pipe, stream)
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            if self.reader_thread is not None:
                self.reader_thread.join(timeout=3)
