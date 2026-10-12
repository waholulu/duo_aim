"""Pure feedback controller. Capture pixels are NOT mouse counts."""
from dataclasses import dataclass
import math

@dataclass
class Settings:
    radius: float = 300.0
    person_conf: float = 0.4
    head_conf: float = 0.65
    gain_x: float = 0.15
    gain_y: float = 0.15
    max_step: int = 32
    deadzone: float = 3.0
    max_age_ms: float = 80.0
    interval_ms: float = 25.0
    confirm_frames: int = 2
    track_radius: float = 120.0

class Controller:
    def __init__(self, settings=None):
        self.cfg = settings or Settings()
        self.last_index = -1
        self.last_move = -math.inf
        self.last_seen = -math.inf
        self.previous = None
        self.streak = 0

    def reset(self):
        self.previous = None
        self.streak = 0

    def commit_move(self, sent_at):
        """Commit only a successful real or simulated send, using its start time."""
        if not math.isfinite(sent_at) or sent_at < self.last_move:
            raise ValueError('Invalid send timestamp')
        self.last_move = sent_at

    def step(self, frame, now, active=True, queue_age_ms=0, age_ms=None):
        c = self.cfg
        result = {'reason': 'no_target', 'dx': 0, 'dy': 0, 'head': None}
        idx = frame['index']
        if idx <= self.last_index:
            return dict(result, reason='duplicate_or_reordered')
        self.last_index = idx
        age = frame['pts_to_coords_ms'] + queue_age_ms if age_ms is None else age_ms
        if not math.isfinite(age) or age < 0 or age > c.max_age_ms:
            self.reset()
            return dict(result, reason='stale')
        if not active:
            self.reset()
            return dict(result, reason='inactive')
        if now-self.last_seen > c.max_age_ms/1000:
            self.reset()
        self.last_seen = now
        w, h = frame['width'], frame['height']
        candidates = []
        for d in frame['detections']:
            x, y = d['head_xy']
            box = d['bbox_xyxy']
            values = [x,y,*box,d['confidence'],d['head_confidence']]
            if not all(math.isfinite(v) for v in values):
                continue
            if not (c.person_conf <= d['confidence'] <= 1 and c.head_conf <= d['head_confidence'] <= 1):
                continue
            if not (0 <= x < w and 0 <= y < h and box[0] <= x <= box[2] and box[1] <= y <= box[3]):
                continue
            distance = math.hypot(x-w/2, y-h/2)
            if distance <= c.radius:
                candidates.append((distance, d))
        if not candidates:
            self.reset()
            return result
        # Prefer continuity while that person remains in the central acquisition area.
        d = min(candidates, key=lambda pair: pair[0])[1]
        if self.previous is not None:
            matched = min(candidates, key=lambda pair: math.dist(pair[1]['head_xy'],self.previous))[1]
            if math.dist(matched['head_xy'], self.previous) <= c.track_radius:
                d = matched
        x,y = d['head_xy']
        self.streak = self.streak+1 if self.previous is not None and math.dist((x,y),self.previous)<=c.track_radius else 1
        self.previous = (x,y)
        result.update(head=[x,y], confidence=d['head_confidence'], error=[x-w/2,y-h/2])
        if self.streak < c.confirm_frames:
            return dict(result,reason='confirming')
        if now-self.last_move < c.interval_ms/1000:
            return dict(result,reason='rate_limited')
        ex,ey = result['error']
        dx = round(ex*c.gain_x) if abs(ex)>c.deadzone else 0
        dy = round(ey*c.gain_y) if abs(ey)>c.deadzone else 0
        dx = max(-c.max_step,min(c.max_step,dx))
        dy = max(-c.max_step,min(c.max_step,dy))
        if dx == 0 and dy == 0:
            return dict(result,reason='aligned')
        return dict(result,reason='move',dx=dx,dy=dy)
