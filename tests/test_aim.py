import hashlib,hmac,json,math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'aim'))
from policy import Controller,Settings
from windows_receiver import Validator

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

if __name__=='__main__':unittest.main()
