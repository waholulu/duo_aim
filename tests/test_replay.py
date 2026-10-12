from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'aim'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feedback import decide, dispatch
from policy import Controller, Settings
from recording import model_fingerprint
from replay import replay
from summarize import summarize, stats
from test_runtime import frame


class ReplayTests(unittest.TestCase):
    def recording(self):
        cfg = Settings(confirm_frames=1)
        controller = Controller(cfg)
        events = [dict(type='metadata', recording_version=1, settings=asdict(cfg)),
                  dict(type='gate', active=True, epoch=1, at_s=0)]
        state = [True, 1]
        for i, now in enumerate([1.02, 1.05, 1.09]):
            f = frame(i)
            f.update(pts_s=now-.01, host_start_s=now-.009, callback_to_coords_ms=1)
            f['detections'] = [dict(head_xy=[1060, 490], confidence=.9, head_confidence=.9,
                                   bbox_xyxy=[1020, 460, 1100, 690], person_center=[1060, 575])]
            decision = decide(controller, f, now, *state)
            events.append(dict(type='observation', frame=f, at_s=now, active=state[0], epoch=state[1], decision=decision))
            # Second decision expires during button query; third observes release.
            ticks = iter([now, now + (.09 if i == 1 else .001), now + (.09 if i == 1 else .001), now+.002])
            def transition(active):
                state[:] = [active, state[1]+1]
                controller.reset()
                events.append(dict(type='gate', active=active, epoch=state[1], at_s=now+.001))
            dispatch(controller, f, decision, lambda: i != 2, transition, lambda: state,
                     clock=lambda: next(ticks),
                     observe=lambda transaction: events.append(dict(type='output', frame_index=i, **transaction)))
        # Consumed old-epoch result must also be reproducible.
        f = frame(3)
        decision = decide(controller, f, 1.1, *state)
        events.append(dict(type='observation', frame=f, at_s=1.1, active=state[0], epoch=state[1], decision=decision))
        events.append(dict(type='stop', reason='completed'))
        return events

    def save(self, directory, events):
        path = Path(directory) / 'sample.replay.jsonl'
        path.write_text(''.join(json.dumps(e)+'\n' for e in events))
        return path

    def test_exact_replay_and_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.save(directory, self.recording())
            result = replay(path)
            self.assertEqual(result['mismatches'], [])
            self.assertEqual(result['counts']['verified_sends'], 1)
            self.assertEqual(result['counts']['reason_old_epoch'], 1)
            changed = replay(path, dict(gain_x=.3))
            self.assertEqual(changed['mismatches'], [])
            self.assertGreater(changed['counts']['decision_changed'], 0)

    def test_tampered_decision_is_detected(self):
        events = self.recording()
        next(e for e in events if e['type'] == 'observation')['decision']['dx'] = 999
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(replay(self.save(directory, events))['mismatches'])

    def test_model_hash_changes_with_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model'; path.mkdir()
            (path/'weights').write_bytes(b'one')
            before = model_fingerprint(path)
            self.assertEqual(before, model_fingerprint(path))
            (path/'weights').write_bytes(b'two')
            self.assertNotEqual(before, model_fingerprint(path))

    def test_summary_counts_only_successful_sends(self):
        rows = [dict(type='decision', frame=0, reason='move', output_status='simulated',
                     send_start_s=1, command_complete_s=1.01, send_age_ms=20),
                dict(type='decision', frame=1, reason='stale_before_move', output_status='dropped', send_age_ms=120),
                dict(type='decision', frame=2, reason='move', output_status='simulated',
                     send_start_s=1.03, command_complete_s=1.04, send_age_ms=30),
                dict(type='fault', error='test')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'log.jsonl'
            path.write_text(''.join(json.dumps(e)+'\n' for e in rows))
            result = summarize(path)
        self.assertEqual(result['send_age_ms']['median'], 25)
        self.assertAlmostEqual(result['send_interval_ms']['median'], 30)
        self.assertEqual(result['faults'], 1)
        self.assertEqual(result['stale_drops'], 1)
        self.assertIsNone(stats([]))

    def test_tampered_output_cannot_be_reported_as_verified(self):
        for change in [dict(dx=999), dict(output_status='dropped'), dict(send_start_s=50)]:
            events = self.recording()
            next(e for e in events if e['type'] == 'output')['result'].update(change)
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                result = replay(self.save(directory, events))
                self.assertTrue(result['mismatches'])
                self.assertEqual(result['counts'].get('verified_sends', 0), 0)

    def test_truncated_recording_does_not_pass_verification(self):
        events = self.recording()[:-1]
        with tempfile.TemporaryDirectory() as directory:
            result = replay(self.save(directory, events))
        self.assertIn(dict(stage='incomplete_recording'), result['mismatches'])
