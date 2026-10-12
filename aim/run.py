#!/usr/bin/env python3
"""Latest-frame capture -> head error -> relative mouse feedback. Dry run by default."""
import argparse
import ctypes
import hashlib
import hmac
import json
import math
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path
from policy import Controller,Settings
from kmbox import KMBox
from feedback import decide, dispatch

ROOT=Path(__file__).resolve().parents[1]

class StopRequested(Exception):
    """A normal stop discovered while waiting for input or output readiness."""


class MacMouse:
    def __init__(self, live):
        import Quartz as q
        self.q=q
        services=ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        services.AXIsProcessTrusted.argtypes=[]
        services.AXIsProcessTrusted.restype=ctypes.c_bool
        if live and not services.AXIsProcessTrusted():
            raise RuntimeError('请在系统设置→隐私与安全性→辅助功能中授权启动本脚本的终端，再运行。')
    def held(self):
        return self.q.CGEventSourceButtonState(self.q.kCGEventSourceStateCombinedSessionState,self.q.kCGMouseButtonRight)
    def escaped(self):
        return self.q.CGEventSourceKeyState(self.q.kCGEventSourceStateCombinedSessionState,53)
    def move(self,dx,dy):
        q=self.q;p=q.CGEventGetLocation(q.CGEventCreate(None))
        e=q.CGEventCreateMouseEvent(None,q.kCGEventMouseMoved,(p.x+dx,p.y+dy),q.kCGMouseButtonLeft)
        q.CGEventSetIntegerValueField(e,q.kCGMouseEventDeltaX,dx)
        q.CGEventSetIntegerValueField(e,q.kCGMouseEventDeltaY,dy)
        q.CGEventPost(q.kCGHIDEventTap,e)

class UDPOutput:
    def __init__(self,host,port):
        self.key=os.environ.get('AIM_TOKEN','').encode()
        if len(self.key)<32:raise ValueError('远程模式需要两端设置同一个至少32字符的 AIM_TOKEN。')
        self.sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.dest=(host,port);self.seq=0;self.session=str(uuid.uuid4())
    def move(self,dx,dy):
        self.seq+=1
        payload=json.dumps(dict(session=self.session,seq=self.seq,ts=time.time(),dx=dx,dy=dy),separators=(',',':')).encode()
        packet=json.dumps(dict(payload=payload.decode(),mac=hmac.new(self.key,payload,hashlib.sha256).hexdigest())).encode()
        self.sock.sendto(packet,self.dest)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--record-replay', action='store_true', help='Save complete consumed observations for offline replay')
    ap.add_argument('--live',action='store_true',help='Enable actual output; otherwise detection and proposed movements only')
    ap.add_argument('--backend',choices=['kmbox','mac','udp'],default='kmbox')
    ap.add_argument('--serial-port',help='KMBox 串口；默认自动查找唯一的 USB 串口')
    ap.add_argument('--host',help='Windows receiver LAN IP')
    ap.add_argument('--port',type=int,default=39871)
    ap.add_argument('--activation',choices=['hold-left','hold-right','auto'],help='Default: physical left for KMBox, right button for legacy backends')
    ap.add_argument('--model',default='yolo11n-pose-640-fp16.mlpackage')
    ap.add_argument('--crop',type=int,default=960)
    ap.add_argument('--seconds',type=float,default=0,help='Stop after this many seconds; 0 runs until Ctrl+C')
    ap.add_argument('--gain-x',type=float,default=.15)
    ap.add_argument('--gain-y',type=float,default=.15)
    ap.add_argument('--radius',type=float,default=300)
    ap.add_argument('--max-step',type=int,default=32)
    ap.add_argument('--interval-ms',type=float,default=25)
    args=ap.parse_args()
    if args.activation is None:args.activation='hold-left' if args.backend=='kmbox' else 'hold-right'
    if not all(math.isfinite(v) for v in [args.gain_x,args.gain_y,args.radius,args.interval_ms,args.seconds]):ap.error('parameters must be finite')
    if args.max_step<1 or args.max_step>128 or args.radius<=0 or args.interval_ms<10 or args.seconds<0 or not 0<args.crop<=1080:ap.error('invalid bounds')
    if args.radius>args.crop/2:ap.error('--radius must fit inside the center crop')
    if args.backend=='kmbox' and args.activation!='hold-left':ap.error('KMBox requires --activation hold-left')
    if args.backend!='kmbox' and args.activation=='hold-left':ap.error('--activation hold-left requires KMBox')
    if args.backend=='udp' and args.live and not args.host:ap.error('--host required for live udp')
    if args.backend=='udp' and args.activation!='auto':
        print('远端是否按住右键由 Windows 接收器判断；Mac 端持续发送检测到的有效偏移。',flush=True)
    binary = ROOT / 'aim/detector'
    sources = [ROOT / 'aim/VisionCore.swift', ROOT / 'aim/detector.swift']
    if not binary.exists() or any(binary.stat().st_mtime < p.stat().st_mtime for p in sources):
        subprocess.run(['swiftc', '-O', *map(str, sources), '-o', str(binary)], check=True)
    cfg = Settings(gain_x=args.gain_x, gain_y=args.gain_y, radius=args.radius,
                   max_step=args.max_step, interval_ms=args.interval_ms)
    run_session(args, cfg, binary)


def run_session(args, cfg, binary, clock=time.monotonic):
    from contextlib import ExitStack
    from runtime import DetectorSession
    from recording import metadata, relative_frame, write_jsonl
    controller = Controller(cfg)
    host_clock = clock
    origin = host_clock()
    clock = lambda: host_clock() - origin
    started = clock()
    logpath = ROOT / 'results' / (time.strftime('aim-%Y%m%d-%H%M%S') + f'-{os.getpid()}.jsonl')
    with ExitStack() as stack:
        logpath.parent.mkdir(exist_ok=True)
        log = stack.enter_context(logpath.open('w', buffering=65536))
        replay = stack.enter_context(logpath.with_suffix('.replay.jsonl').open('w', buffering=65536)) if args.record_replay else None
        def write(row):
            write_jsonl(log, row)
        def record(row):
            if replay is not None:
                write_jsonl(replay, row)
        try:
            model = (ROOT / args.model).resolve()
            info = metadata(args, cfg, model)
            write(info); record(info)
            mouse = MacMouse(args.live) if args.backend == 'mac' else None
            box = stack.enter_context(KMBox(args.serial_port)) if args.backend == 'kmbox' else None
            output = None
            if args.live:
                output = box if box else mouse if mouse else UDPOutput(args.host, args.port)
                if isinstance(output, UDPOutput):
                    stack.callback(output.sock.close)
            def held():
                return box.physical_left() if box else args.backend == 'udp' or args.activation == 'auto' or mouse.held()
            cmd = [str(binary), str((ROOT / args.model).resolve()), str(args.crop), '0', 'stream', 'all', 'gated']
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    text=True, bufsize=1, start_new_session=True)
            session = DetectorSession(proc, clock)
            stack.callback(session.close)
            # A stalled detector must not block the controller on a full gate pipe.
            os.set_blocking(proc.stdin.fileno(), False)
            session.start_reader()
            last_poll = last_print = -float('inf')
            shown_ready = False
            shown_warmup = 0
            print(f'{"LIVE" if args.live else "DRY RUN"} | backend={args.backend} | log={logpath}', flush=True)
            def transition(active):
                previous = session.active
                session.transition(active, controller, record)
                if previous != active:
                    print('识别开启' if active else '识别暂停', flush=True)
            def output_state():
                # The last button query (or recording I/O) may cross the run limit.
                if args.seconds and clock()-started >= args.seconds:
                    raise StopRequested('duration_reached')
                if mouse and mouse.escaped():
                    raise StopRequested('escape')
                session.check()
                return session.active, session.epoch

            while not args.seconds or clock()-started < args.seconds:
                session.check()
                if session.ready is None:
                    session.take()
                    continue
                if not shown_ready:
                    print('检测器就绪，等待激活；首次激活后预热 30 次推理。', flush=True)
                    write(dict(session.ready, at_s=clock()))
                    shown_ready = True
                if session.warmup != shown_warmup:
                    shown_warmup = session.warmup
                    print(f'预热 {shown_warmup}/30', flush=True)
                if mouse and mouse.escaped():
                    break
                if clock()-last_poll >= .01:
                    transition(held())
                    last_poll = clock()
                frame = session.take()
                session.check()
                if frame is None:
                    continue
                frame = relative_frame(frame, origin)
                now = clock()
                result = decide(controller, frame, now, session.active, session.epoch)
                record(dict(type='observation', frame=frame, at_s=now, active=session.active, epoch=session.epoch, decision=result))
                def observe(transaction):
                    record(dict(type='output', frame_index=frame['index'], **transaction))
                result = dispatch(controller, frame, result, held, transition,
                                  output_state, output, clock, observe)
                result.update(type='decision', at_s=now, preprocess_ms=frame['preprocess_ms'],
                              inference_ms=frame['inference_ms'], postprocess_ms=frame['postprocess_ms'], width=frame['width'], height=frame['height'],
                              center=[frame['width']/2, frame['height']/2], crop_xywh=frame['crop_xywh'],
                              frame=frame['index'], dry_run=not args.live,
                              processing_ms=frame['callback_to_coords_ms'],
                              pts_to_coords_ms=frame['pts_to_coords_ms'],
                              pts_to_controller_ms=max(0, (now-frame['pts_s'])*1000))
                write(result)
                if now-last_print >= .5:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    last_print = now
        except StopRequested as stopped:
            stop = dict(type='stop', at_s=clock(), reason=str(stopped))
            write(stop); record(stop)
        except KeyboardInterrupt:
            stop = dict(type='stop', at_s=clock(), reason='keyboard_interrupt')
            write(stop); record(stop)
        except BaseException as error:
            fault = dict(type='fault', at_s=clock(), error=str(error))
            write(fault); record(fault)
            raise
        else:
            stop = dict(type='stop', at_s=clock(), reason='completed')
            write(stop); record(stop)
    print('Stopped. ' + str(logpath))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        import sys
        print(f'运行失败：{error}', file=sys.stderr)
        raise SystemExit(1)
