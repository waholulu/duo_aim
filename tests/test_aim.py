import hashlib,hmac,json,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'aim'))
from policy import Controller,Settings
from windows_receiver import Validator
from kmbox import KMBox

def frame(i=0,head=(1060,490),age=10,confidence=.9):
 return dict(index=i,width=1920,height=1080,pts_to_coords_ms=age,detections=[dict(head_xy=head,head_confidence=confidence,confidence=.9,bbox_xyxy=[head[0]-40,head[1]-30,head[0]+40,head[1]+200])])

class PolicyTests(unittest.TestCase):
    def test_center_follows_frame_resolution(self):
        for width, height in [(1280, 720), (2560, 1440), (3440, 1440), (1080, 1920), (1919, 1079)]:
            with self.subTest(width=width, height=height):
                center = (width / 2, height / 2)
                controller = Controller(Settings(confirm_frames=1))
                centered = dict(frame(head=center), width=width, height=height)
                self.assertEqual(controller.step(centered, 0)['reason'], 'aligned')
                offset = (center[0] + 100, center[1] - 50)
                moved = dict(frame(1, head=offset), width=width, height=height)
                result = controller.step(moved, .03)
                self.assertEqual(result['error'], [100, -50])
                self.assertEqual((result['dx'], result['dy']), (15, -8))

    def test_confirm_and_direction(self):
        c=Controller();self.assertEqual(c.step(frame(),0)['reason'],'confirming')
        r=c.step(frame(1),.03);self.assertEqual((r['dx'],r['dy']),(15,-8))
    def test_central_gate_excludes_streamer(self):
        c=Controller(Settings(confirm_frames=1));self.assertEqual(c.step(frame(head=(139,635)),0)['reason'],'no_target')
    def test_stale_low_conf_lost_inactive_stop(self):
        for f,active,reason in [(frame(age=200),True,'stale'),(frame(confidence=.2),True,'no_target'),(dict(frame(),detections=[]),True,'no_target'),(frame(),False,'inactive')]:
            c=Controller(Settings(confirm_frames=1));self.assertEqual(c.step(f,0,active)['reason'],reason)
    def test_no_repeated_frame_movement(self):
        c=Controller(Settings(confirm_frames=1));self.assertEqual(c.step(frame(),0)['reason'],'move')
        self.assertEqual(c.step(frame(),1)['reason'],'duplicate_or_reordered')
    def test_stale_queue_and_clock_failure(self):
        c=Controller();self.assertEqual(c.step(frame(),0,queue_age_ms=100)['reason'],'stale')
        self.assertEqual(c.step(frame(1,age=float('nan')),1)['reason'],'stale')
    def test_deadzone_and_clamp(self):
        c=Controller(Settings(confirm_frames=1,gain_x=1))
        self.assertEqual(c.step(frame(head=(961,541)),0)['reason'],'aligned')
        self.assertEqual(c.step(frame(1),.1)['dx'],32)
    def test_rate_limit_and_reacquire(self):
        c=Controller(Settings(confirm_frames=1));c.step(frame(),0);c.commit_move(0)
        self.assertEqual(c.step(frame(1),.001)['reason'],'rate_limited')
        c.step(dict(frame(2),detections=[]),.1);self.assertIsNone(c.previous)
    def test_confirmation_resets_after_feed_gap(self):
        c=Controller();c.step(frame(),0)
        self.assertEqual(c.step(frame(1),1)["reason"],"confirming")
    def test_closed_loop_reduces_error(self):
        c=Controller();x,y=1150.,420.
        for i in range(150):
            r=c.step(frame(i,head=(x,y)),i*.03)
            if r['reason'] == 'move': c.commit_move(i*.03)
            x-=r['dx']*2;y-=r['dy']*2
        self.assertLess(math.hypot(x-960,y-540),6)

class PacketTests(unittest.TestCase):
 key=b'x'*32
 def packet(self,**changes):
  p=dict(session='test',seq=1,ts=100.,dx=3,dy=-2);p.update(changes)
  raw=json.dumps(p).encode()
  return json.dumps(dict(payload=raw.decode(),mac=hmac.new(self.key,raw,hashlib.sha256).hexdigest())).encode()
 def test_good_and_replay(self):
  v=Validator(self.key);self.assertEqual(v.decode(self.packet(),100.)['dx'],3);self.assertIsNone(v.decode(self.packet(),100.))
 def test_invalid_packets(self):
  for p in [b'no',self.packet(ts=90),self.packet(dx=1000),self.packet(dy=float('nan')),self.packet(dx=True)]:
   self.assertIsNone(Validator(self.key).decode(p,100.))
  p=json.loads(self.packet());p['mac']='0'*64
  self.assertIsNone(Validator(self.key).decode(json.dumps(p).encode(),100.))
 def test_receiver_pins_sender_session(self):
  v=Validator(self.key);v.decode(self.packet(),100.)
  self.assertIsNone(v.decode(self.packet(session='new',seq=2),100.))

class KMBoxTests(unittest.TestCase):
 def test_only_physical_left_activates(self):
  box=KMBox.__new__(KMBox)
  for state,expected in [(0,False),(1,True),(2,False),(3,True)]:
   box.command=lambda *args, state=state, **kwargs:f'km.left()\r\n{state}\r\n>>> '
   self.assertEqual(box.physical_left(),expected)

 def test_malformed_button_response_fails_closed(self):
  box=KMBox.__new__(KMBox)
  box.command=lambda *args,**kwargs:'km.left()\r\nunknown\r\n>>> '
  with self.assertRaises(ValueError):box.physical_left()


class OutputBoundaryTests(unittest.TestCase):
    def timed_frame(self, pts=0):
        return dict(frame(), pts_s=pts, host_start_s=pts, callback_to_coords_ms=0, gate_epoch=1)

    def test_suggestion_does_not_consume_interval(self):
        c = Controller(Settings(confirm_frames=1))
        c.step(frame(), 0)
        self.assertEqual(c.step(frame(1), .001)['reason'], 'move')
        c.commit_move(.02)
        self.assertEqual(c.step(frame(2), .03)['reason'], 'rate_limited')
        c.reset()
        self.assertEqual(c.step(frame(3), .04)['reason'], 'rate_limited')

    def exercise(self, held, times, output=None):
        from feedback import decide, dispatch
        c = Controller(Settings(confirm_frames=1))
        f = self.timed_frame()
        state = [True, 1]
        def transition(active):
            state[:] = [active, state[1]+1]
            c.reset()
        ticks = iter(times)
        result = dispatch(c, f, decide(c, f, .01, True, 1), held,
                          transition, lambda: state, output, lambda: next(ticks))
        return c, result

    def test_query_makes_frame_stale(self):
        c, r = self.exercise(lambda: True, [.01, .09, .09])
        self.assertEqual(r['reason'], 'stale_before_move')
        self.assertEqual(c.last_move, -math.inf)

    def test_release_after_query(self):
        c, r = self.exercise(lambda: False, [.01, .02, .02])
        self.assertEqual(r['reason'], 'released_before_move')
        self.assertEqual(c.last_move, -math.inf)

    def test_simulated_send_commits_boundary_time(self):
        c, r = self.exercise(lambda: True, [.01, .02, .025, .026])
        self.assertEqual(r['output_status'], 'simulated')
        self.assertEqual(c.last_move, .025)

    def test_clock_validation_precedes_clamping(self):
        from feedback import frame_age_ms
        for value in [float('nan'), float('inf'), -.006]:
            with self.assertRaises(ValueError):
                frame_age_ms(self.timed_frame(), value)
        self.assertEqual(frame_age_ms(self.timed_frame(), -.004), 0)

    def test_final_boundary_checks_epoch_age_and_send_interval(self):
        from feedback import output_check
        c = Controller(Settings(confirm_frames=1))
        f = self.timed_frame()
        self.assertEqual(output_check(c, f, .08, True, 1)['reason'], 'ready_to_send')
        self.assertEqual(output_check(c, f, .080001, True, 1)['reason'], 'stale_before_move')
        self.assertEqual(output_check(c, f, .01, True, 2)['reason'], 'old_epoch')
        c.commit_move(.005)
        self.assertEqual(output_check(c, f, .01, True, 1)['reason'], 'rate_limited')

    def test_new_stream_fault_during_query_prevents_send(self):
        from feedback import decide, dispatch
        from unittest.mock import Mock
        c = Controller(Settings(confirm_frames=1))
        f = self.timed_frame()
        output = Mock()
        def failed_state():
            raise RuntimeError('detector died during query')
        with self.assertRaises(RuntimeError):
            dispatch(c, f, decide(c, f, .01, True, 1), lambda: True,
                     lambda _: None, failed_state, output, lambda: .02)
        output.move.assert_not_called()
        self.assertEqual(c.last_move, -math.inf)

    def test_failed_output_is_not_committed(self):
        from feedback import decide, dispatch
        from unittest.mock import Mock
        c = Controller(Settings(confirm_frames=1))
        f = self.timed_frame()
        output = Mock(); output.move.side_effect = OSError('disconnected')
        with self.assertRaises(OSError):
            dispatch(c, f, decide(c, f, .01, True, 1), lambda: True,
                     lambda _: None, lambda: (True, 1), output, lambda: .02)
        self.assertEqual(c.last_move, -math.inf)

class ScenarioTests(unittest.TestCase):
    def test_crossing_targets_prefers_continuity_then_reacquires(self):
        c = Controller(Settings(confirm_frames=1))
        first = frame(head=(1080, 500))
        c.step(first, 0)
        closer = frame(head=(970, 500))['detections'][0]
        continuous = frame(head=(1070, 500))['detections'][0]
        result = c.step(dict(frame(1), detections=[closer, continuous]), .03)
        self.assertEqual(result['head'], [1070, 500])
        c.step(dict(frame(2), detections=[]), .06)
        result = c.step(dict(frame(3), detections=[closer, continuous]), .09)
        self.assertEqual(result['head'], [970, 500])

    def test_delayed_feedback_with_seeded_drops(self):
        import random
        rng = random.Random(17)
        c = Controller(Settings(confirm_frames=2, gain_x=.1, gain_y=.1))
        x, y = 1140., 430.
        pending = []
        history = []
        sends = []
        for i in range(300):
            now = i*.01
            for due, dx, dy in list(pending):
                if due <= i:
                    x -= 2*dx; y -= 2*dy
                    pending.remove((due, dx, dy))
            history.append((x, y))
            if i < 2 or rng.random() < .15:
                continue
            observed = history[i-2]
            r = c.step(frame(i, head=observed, age=20), now)
            if r['reason'] == 'move':
                self.assertLessEqual(max(abs(r['dx']), abs(r['dy'])), 32)
                if sends: self.assertGreaterEqual(now-sends[-1], .025-1e-9)
                c.commit_move(now)
                sends.append(now)
                pending.append((i+2, r['dx'], r['dy']))
        self.assertLess(math.hypot(x-960, y-540), 8)


if __name__ == "__main__":
    unittest.main()
