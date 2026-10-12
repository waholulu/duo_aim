import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'aim'))
from policy import Controller
from runtime import DetectorSession, Watchdog, validate_event


def ready():
    return dict(type='ready', protocol_version=1, model_size=640, warmup_total=30, requested_fps=240)


def warmup(i, epoch=1):
    return dict(type='warmup', protocol_version=1, completed=i, total=30, gate_epoch=epoch)


def frame(i=0, epoch=1):
    return dict(type='frame', protocol_version=1, index=i, gate_epoch=epoch,
                width=1920, height=1080, crop_xywh=[480, 60, 960, 960], detections=[],
                pts_s=1., host_start_s=1.001, callback_to_coords_ms=10.,
                pts_to_coords_ms=11., pts_to_callback_ms=1., preprocess_ms=1.,
                inference_ms=8., postprocess_ms=1.)


class RuntimeTests(unittest.TestCase):
    def make_session(self, lines=()):
        proc = Mock()
        proc.stdout = io.StringIO(''.join(json.dumps(e)+'\n' if not isinstance(e, str) else e for e in lines))
        proc.stdin = io.StringIO()
        proc.poll.return_value = 0
        return DetectorSession(proc, lambda: 1.)

    def test_readiness_and_full_warmup_then_latest_slot(self):
        s = self.make_session([ready(), *(warmup(i) for i in range(1, 31)), frame(), frame(1)])
        s.read()
        self.assertIsNone(s.error)
        self.assertEqual(s.warmup, 30)
        self.assertEqual(s.take(0)['index'], 1)
        self.assertIsNone(s.take(0))

    def test_startup_and_active_timeouts_but_not_idle(self):
        w = Watchdog(0)
        w.check(119)
        with self.assertRaises(TimeoutError): w.check(120)
        w.ready = True
        w.check(1000)
        w.activate(True, 1000)
        w.check(1004.9)
        with self.assertRaises(TimeoutError): w.check(1005)
        w.activate(False, 1005)
        w.check(10000)

    def test_bad_json_protocol_and_early_exit(self):
        for events in [['bad\n'], [dict(ready(), protocol_version=2)], [frame()],
                       [ready(), frame()], [ready(), ready()], [ready(), warmup(2)]]:
            with self.subTest(events=events):
                s = self.make_session(events)
                s.read()
                with self.assertRaises(RuntimeError): s.check()
        s = self.make_session()
        s.read()
        with self.assertRaisesRegex(RuntimeError, 'exited'): s.check()

    def test_invalid_frame_fields_fail_closed(self):
        for changes in [dict(pts_s=float('nan')), dict(width=True), dict(crop_xywh=[0, 0, 2000, 1]),
                        dict(detections=[{}]), dict(inference_ms=-1), dict(index=-1)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_event(dict(frame(), **changes))

    def test_release_repress_clears_slot_but_preserves_cooldown(self):
        s = self.make_session()
        c = Controller(); c.commit_move(1)
        s.transition(True, c)
        s.frame = frame()
        s.transition(False, c)
        self.assertIsNone(s.frame)
        s.transition(True, c)
        self.assertEqual(s.epoch, 3)
        self.assertEqual(c.last_move, 1)
        self.assertEqual(s.process.stdin.getvalue(), '1 1\n0 2\n1 3\n')

    def test_stale_epoch_does_not_refresh_watchdog(self):
        s = self.make_session()
        s.accept(ready())
        s.transition(True, Controller())
        s.watchdog.last_progress = 0
        s.accept(warmup(1, epoch=0))
        self.assertEqual(s.watchdog.last_progress, 0)
        s.accept(warmup(2))
        self.assertEqual(s.watchdog.last_progress, 1)

    def test_shutdown_escalates_and_closes_streams(self):
        import subprocess
        s = self.make_session()
        s.process.poll.return_value = None
        s.process.wait.side_effect = [subprocess.TimeoutExpired('fake', 3), 0]
        with patch('runtime.os.killpg') as kill:
            s.close()
        self.assertEqual(kill.call_count, 2)
        self.assertTrue(s.process.stdout.closed)
        self.assertTrue(s.process.stdin.closed)

    def test_reader_start_failure_does_not_join_unstarted_thread(self):
        s = self.make_session()
        with patch('threading.Thread.start', side_effect=RuntimeError('no thread')):
            with self.assertRaisesRegex(RuntimeError, 'no thread'):
                s.start_reader()
        s.close()
        self.assertTrue(s.process.stdin.closed)

    def test_shutdown_does_not_write_into_a_stalled_control_pipe(self):
        s = self.make_session()
        s.process.poll.return_value = None
        s.process.stdin = Mock()
        with patch('runtime.os.killpg'):
            s.close()
        s.process.stdin.write.assert_not_called()
        s.process.stdin.flush.assert_not_called()
        s.process.stdin.close.assert_called_once()

    def test_both_streams_close_even_if_one_close_fails(self):
        s = self.make_session()
        s.process.stdout = Mock()
        s.process.stdout.close.side_effect = OSError('close failed')
        with self.assertRaisesRegex(OSError, 'close failed'):
            s.close()
        self.assertTrue(s.process.stdin.closed)

    def test_full_gate_pipe_fails_instead_of_hiding_the_failure(self):
        s = self.make_session()
        s.process.stdin = Mock()
        s.process.stdin.flush.side_effect = BlockingIOError('pipe full')
        with self.assertRaises(BlockingIOError):
            s.transition(True, Controller())
        s.process.stdin.close.side_effect = BrokenPipeError('child gone')
        s.close()
        self.assertTrue(s.process.stdout.closed)

    def test_real_stalled_child_pipe_and_reader_are_reaped(self):
        import os
        import subprocess
        process = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True, bufsize=1, start_new_session=True)
        s = DetectorSession(process)
        try:
            os.set_blocking(process.stdin.fileno(), False)
            s.start_reader()
            # Fill the actual pipe while the child never reads control messages.
            while True:
                try:
                    os.write(process.stdin.fileno(), b'x' * 65536)
                except BlockingIOError:
                    break
            with self.assertRaises(BlockingIOError):
                s.transition(True, Controller())
            s.close()
            self.assertIsNotNone(process.poll())
            self.assertFalse(s.reader_thread.is_alive())
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
