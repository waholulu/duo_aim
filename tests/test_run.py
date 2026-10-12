"""Lifecycle integration with deterministic fake process, device and clock."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'aim'))
import run
from runtime import DetectorSession
from test_runtime import ready, warmup, frame
from replay import replay


class FakeClock:
    def __init__(self):
        self.now = 100.
    def __call__(self):
        return self.now


class RunTests(unittest.TestCase):
    def exercise(self, mode, record=True, live=False):
        import io
        clock = FakeClock()
        process = Mock()
        process.poll.return_value = None
        class ControlPipe(io.StringIO):
            def fileno(self): return 123
        process.stdin = ControlPipe(); process.stdout = io.StringIO()
        device = Mock()
        device.__enter__ = Mock(return_value=device)
        device.__exit__ = Mock(return_value=False)
        device.physical_left.return_value = True
        def held():
            if mode == 'duration_during_query' and state.get('index', 0) == 2:
                clock.now += .01
            return True
        device.physical_left.side_effect = held
        args = SimpleNamespace(model='model.bin', crop=960, backend='kmbox', serial_port='fake',
                               activation='hold-left', live=live, seconds=1, record_replay=record)
        if mode == 'duration_during_query':
            args.seconds = .065
        state = {}
        def start_reader(session):
            state['session'] = session
            if mode == 'thread_failure':
                raise RuntimeError('Cannot start reader')
            if mode != 'startup_timeout':
                session.accept(ready())
        def take(session, timeout=.01):
            if mode == 'startup_timeout':
                clock.now += 121
                return None
            if mode == 'active_timeout':
                clock.now += 6
                return None
            if mode == 'bad_json':
                session.error = ValueError('bad JSON')
                return None
            if mode == 'exit':
                session.closed = True
                return None
            if session.warmup == 0:
                for i in range(1, 31): session.accept(warmup(i))
                # One harmless confirming frame, then one output opportunity.
                state['index'] = 0
            index = state['index']; state['index'] += 1
            if index >= 2:
                clock.now += 2
                return None
            clock.now += .03
            f = frame(index)
            f.update(pts_s=clock.now-.01, host_start_s=clock.now-.009, callback_to_coords_ms=1)
            f['detections'] = [dict(head_xy=[1060,490], person_center=[1060,575],
                                   confidence=.9, head_confidence=.9, bbox_xyxy=[1020,460,1100,690])]
            return f
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory); (root/'model.bin').write_bytes(b'model')
            stack.enter_context(patch.object(run, 'ROOT', root))
            stack.enter_context(patch.object(run, 'KMBox', return_value=device))
            popen = stack.enter_context(patch.object(run.subprocess, 'Popen', return_value=process))
            stack.enter_context(patch.object(DetectorSession, 'start_reader', start_reader))
            stack.enter_context(patch.object(DetectorSession, 'take', take))
            kill = stack.enter_context(patch('runtime.os.killpg'))
            nonblocking = stack.enter_context(patch('run.os.set_blocking'))
            stack.enter_context(patch('builtins.print'))
            if mode == 'popen_failure': popen.side_effect = OSError('cannot spawn')
            if mode == 'output_failure': device.move.side_effect = OSError('disconnected')
            if mode in ('startup_timeout', 'active_timeout'):
                args.seconds = 0
            error = None
            try:
                run.run_session(args, run.Settings(), 'fake-detector', clock)
            except (RuntimeError, OSError) as exc:
                error = exc
            logs = list((root/'results').glob('*.jsonl'))
            contents = '\n'.join(path.read_text() for path in logs)
            if mode == 'success' and record:
                recording = next((root/'results').glob('*.replay.jsonl'))
                self.assertEqual(replay(recording)['mismatches'], [])
            device.__exit__.assert_called_once()
            if mode != 'popen_failure':
                nonblocking.assert_called_once_with(123, False)
                self.assertTrue(process.stdout.closed)
                self.assertTrue(process.stdin.closed)
                kill.assert_called_once()
            return error, contents, device

    def test_success_dry_run_records_replay_and_never_moves(self):
        error, contents, device = self.exercise('success')
        self.assertIsNone(error)
        device.move.assert_not_called()
        self.assertIn('simulated', contents)
        self.assertIn('model_sha256', contents)

    def test_no_replay_file_by_default(self):
        error, _, device = self.exercise('success', record=False)
        self.assertIsNone(error)
        device.move.assert_not_called()

    def test_faults_cleanup_resources_and_are_logged(self):
        for mode in ['thread_failure', 'popen_failure', 'startup_timeout', 'active_timeout', 'bad_json', 'exit', 'output_failure']:
            with self.subTest(mode=mode):
                error, contents, _ = self.exercise(mode, live=mode == 'output_failure')
                self.assertIsNotNone(error)
                self.assertIn('"type":"fault"', contents)

    def test_duration_crossed_during_final_query_never_moves(self):
        error, contents, device = self.exercise('duration_during_query', live=True)
        self.assertIsNone(error)
        device.move.assert_not_called()
        self.assertIn('duration_reached', contents)
