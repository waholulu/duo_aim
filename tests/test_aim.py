import hashlib,hmac,json,math,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'aim'))
from policy import Controller,Settings
import windows_receiver
from windows_receiver import Validator,oversized_datagram
import run

def frame(i=0,head=(1060,490),age=10,confidence=.9):
 return dict(index=i,width=1920,height=1080,pts_to_coords_ms=age,detections=[dict(head_xy=head,head_confidence=confidence,confidence=.9,bbox_xyxy=[head[0]-40,head[1]-30,head[0]+40,head[1]+200])])

class PolicyTests(unittest.TestCase):
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
  c=Controller(Settings(confirm_frames=1));c.step(frame(),0)
  self.assertEqual(c.step(frame(1),.001)['reason'],'rate_limited')
  c.step(dict(frame(2),detections=[]),.1);self.assertIsNone(c.previous)
 def test_confirmation_resets_after_feed_gap(self):
  c=Controller();c.step(frame(),0)
  self.assertEqual(c.step(frame(1),1)["reason"],"confirming")
 def test_closed_loop_reduces_error(self):
  c=Controller();x,y=1150.,420.
  for i in range(150):
   r=c.step(frame(i,head=(x,y)),i*.03)
   x-=r['dx']*2;y-=r['dy']*2
  self.assertLess(math.hypot(x-960,y-540),6)

class PacketTests(unittest.TestCase):
 key=b'x'*32
 def packet(self,**changes):
  p=dict(session='test',seq=1,ts=100.,kind='move',ttl_ms=80,dx=3,dy=-2);p.update(changes)
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
 def test_remaining_frame_lifetime(self):
  self.assertIsNotNone(Validator(self.key).decode(self.packet(ttl_ms=10),100.005))
  self.assertIsNone(Validator(self.key).decode(self.packet(ttl_ms=10),100.02))
  self.assertIsNone(Validator(self.key).decode(self.packet(ttl_ms=100),100.))
 def test_stop_supersedes_older_move(self):
  v=Validator(self.key)
  self.assertEqual(v.decode(self.packet(kind='stop',dx=0,dy=0,seq=2),100.)['kind'],'stop')
  self.assertIsNone(v.decode(self.packet(seq=1),100.))
  self.assertIsNone(Validator(self.key).decode(self.packet(kind='stop',dx=1),100.))
 def test_sender_and_receiver_share_packet_contract(self):
  sock=mock.Mock()
  with mock.patch.object(run.socket,'socket',return_value=sock),mock.patch.object(run.time,'time',return_value=100.),mock.patch.dict(run.os.environ,{'AIM_TOKEN':'x'*32}):
   sender=run.UDPOutput('127.0.0.1',39871)
   sender.move(3,-2,10)
   sender.stop()
  first=sock.sendto.call_args_list[0].args[0]
  second=sock.sendto.call_args_list[1].args[0]
  v=Validator(self.key)
  self.assertEqual(v.decode(first,100.005)['kind'],'move')
  self.assertEqual(v.decode(second,100.005)['kind'],'stop')
 def test_oversized_datagram_error(self):
  self.assertTrue(oversized_datagram(OSError(10040,'too long')))
  self.assertFalse(oversized_datagram(OSError(9,'bad file descriptor')))
 def test_receiver_continues_after_oversized_packets_in_both_paths(self):
  sock=mock.Mock()
  sock.recvfrom.side_effect=[OSError(10040,'too long'),(self.packet(),('sender',1)),OSError(10040,'too long'),BlockingIOError(),KeyboardInterrupt()]
  with mock.patch.object(windows_receiver.socket,'socket',return_value=sock),mock.patch.object(windows_receiver.time,'time',return_value=100.),mock.patch.object(windows_receiver.time,'monotonic',return_value=1.),mock.patch.object(sys,'argv',['windows_receiver.py']),mock.patch.dict(windows_receiver.os.environ,{'AIM_TOKEN':'x'*32}):
   windows_receiver.main()
  self.assertEqual(sock.recvfrom.call_count,5)
  sock.close.assert_called_once()

class ProcessTests(unittest.TestCase):
 def test_detector_is_reaped_when_log_open_fails(self):
  args=SimpleNamespace(gain_x=.15,gain_y=.15,radius=300,max_step=32,interval_ms=25,model='missing',crop=960,live=False,backend='mac')
  proc=mock.Mock(pid=123,stdout=mock.Mock())
  proc.poll.return_value=None
  with tempfile.TemporaryDirectory() as tmp:
   with mock.patch.object(run.subprocess,'Popen',return_value=proc),mock.patch.object(run.threading.Thread,'start'),mock.patch.object(run.os,'killpg') as kill:
    with self.assertRaises(FileNotFoundError):
     run.run_detector(args,None,None,Path(tmp)/'missing'/'log.jsonl')
  kill.assert_called_once()
  proc.wait.assert_called_once()
  proc.stdout.close.assert_called_once()

if __name__=='__main__':unittest.main()
