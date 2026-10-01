#!/usr/bin/env python3
"""Latest-frame capture -> head error -> relative mouse feedback. Dry run by default."""
import argparse,json,os,signal,socket,subprocess,threading,time,hmac,hashlib,uuid,ctypes
from pathlib import Path
from policy import Controller,Settings

ROOT=Path(__file__).resolve().parents[1]

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
    def send(self,kind,dx=0,dy=0,ttl_ms=80):
        self.seq+=1
        payload=json.dumps(dict(session=self.session,seq=self.seq,ts=time.time(),kind=kind,ttl_ms=ttl_ms,dx=dx,dy=dy),separators=(',',':')).encode()
        packet=json.dumps(dict(payload=payload.decode(),mac=hmac.new(self.key,payload,hashlib.sha256).hexdigest())).encode()
        self.sock.sendto(packet,self.dest)
    def move(self,dx,dy,ttl_ms=80):
        self.send('move',dx,dy,ttl_ms)
    def stop(self):
        self.send('stop')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--live',action='store_true',help='Enable actual output; otherwise detection and proposed movements only')
    ap.add_argument('--backend',choices=['mac','udp'],default='mac')
    ap.add_argument('--host',help='Windows receiver LAN IP')
    ap.add_argument('--port',type=int,default=39871)
    ap.add_argument('--activation',choices=['hold-right','auto'],default='hold-right',help='Local Mac gate; Windows receiver also has its own gate')
    ap.add_argument('--model',default='yolo11n-pose-640-fp16.mlpackage')
    ap.add_argument('--crop',type=int,default=960)
    ap.add_argument('--seconds',type=float,default=0,help='Stop after this many seconds; 0 runs until Ctrl+C')
    ap.add_argument('--gain-x',type=float,default=.15)
    ap.add_argument('--gain-y',type=float,default=.15)
    ap.add_argument('--radius',type=float,default=300)
    ap.add_argument('--max-step',type=int,default=32)
    ap.add_argument('--interval-ms',type=float,default=25)
    args=ap.parse_args()
    if not all(__import__('math').isfinite(v) for v in [args.gain_x,args.gain_y,args.radius,args.interval_ms,args.seconds]):ap.error('parameters must be finite')
    if args.max_step<1 or args.max_step>128 or args.radius<=0 or args.interval_ms<10 or args.seconds<0 or args.crop<0:ap.error('invalid bounds')
    if args.backend=='udp' and args.max_step>32:ap.error('UDP receiver accepts at most 32 counts per axis; use --max-step 32 or less')
    if args.backend=='udp' and args.live and not args.host:ap.error('--host required for live udp')
    if args.backend=='udp' and args.activation!='auto':
        print('远端是否按住右键由 Windows 接收器判断；Mac 端持续发送检测到的有效偏移。',flush=True)
    binary=ROOT/'aim/detector';source=ROOT/'aim/detector.swift'
    if not binary.exists() or binary.stat().st_mtime<source.stat().st_mtime:
        subprocess.run(['swiftc','-O',str(source),'-o',str(binary)],check=True)
    logdir=ROOT/'results';logdir.mkdir(exist_ok=True)
    logpath=logdir/(time.strftime('aim-%Y%m%d-%H%M%S')+'.jsonl')
    mouse=None;output=None
    try:
        mouse=MacMouse(args.live) if args.backend=='mac' and args.live else None
        output=(mouse if args.backend=='mac' else UDPOutput(args.host,args.port)) if args.live else None
        run_detector(args,mouse,output,logpath)
    finally:
        if isinstance(output,UDPOutput):output.sock.close()
    print('Stopped. '+str(logpath))

def run_detector(args,mouse,output,logpath):
    binary=ROOT/'aim/detector'
    cfg=Settings(gain_x=args.gain_x,gain_y=args.gain_y,radius=args.radius,max_step=args.max_step,interval_ms=args.interval_ms)
    controller=Controller(cfg)
    # A single replaceable slot: never drain a backlog of obsolete detections.
    condition=threading.Condition();state={'frame':None,'closed':False,'error':None}
    cmd=[str(binary),str((ROOT/args.model).resolve()),str(args.crop),'0','stream','all']
    proc=None
    def reader():
        try:
            for line in proc.stdout:
                frame=json.loads(line)
                with condition:
                    state['frame']=(frame,time.monotonic());condition.notify()
        except Exception as e:
            state['error']=str(e)
        finally:
            with condition:state['closed']=True;condition.notify()
    try:
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,text=True,bufsize=1,start_new_session=True)
        threading.Thread(target=reader,daemon=True).start()
        started=time.monotonic();last_print=0
        print(f'{"LIVE" if args.live else "DRY RUN (鼠标不动)"} | backend={args.backend} | Ctrl+C结束 | log={logpath}',flush=True)
        with logpath.open('w',buffering=65536) as log:
            while not args.seconds or time.monotonic()-started<args.seconds:
                if mouse and mouse.escaped():break
                with condition:
                    condition.wait_for(lambda:state['frame'] is not None or state['closed'],timeout=.05)
                    item=state['frame'];state['frame']=None;closed=state['closed']
                if item is None:
                    if closed:break
                    continue
                frame,received=item;t=time.monotonic()
                active=not args.live or args.backend=='udp' or args.activation=='auto' or mouse.held()
                # macOS Python monotonic and CMClock host time both use the host monotonic clock.
                queue_age_ms=(t-frame['host_start_s'])*1000-frame['callback_to_coords_ms']
                if queue_age_ms < -5: raise RuntimeError('Host clock mismatch; refusing mouse output')
                result=controller.step(frame,t,active,max(0,queue_age_ms))
                result.update(frame=frame['index'],dry_run=not args.live,processing_ms=frame['callback_to_coords_ms'],pts_to_coords_ms=frame['pts_to_coords_ms'],pts_to_controller_ms=frame['pts_to_coords_ms']+max(0,queue_age_ms))
                if isinstance(output,UDPOutput):
                    if result['reason']=='move':
                        ttl_ms=cfg.max_age_ms-result['pts_to_controller_ms']-(time.monotonic()-t)*1000
                        if ttl_ms>0:output.move(result['dx'],result['dy'],ttl_ms)
                        else:output.stop()
                    elif result['reason'] in ('no_target','stale','inactive','aligned','confirming'):output.stop()
                elif result['reason']=='move' and output:output.move(result['dx'],result['dy'])
                log.write(json.dumps(result)+'\n')
                if t-last_print>=.5:
                    print(json.dumps(result,ensure_ascii=False),flush=True);last_print=t
        if state['error']:raise RuntimeError(state['error'])
        if proc.poll() is not None and proc.returncode!=0:raise RuntimeError(f'detector exited {proc.returncode}')
    except KeyboardInterrupt:
        pass
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid,signal.SIGTERM)
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
        if proc is not None and proc.stdout is not None:proc.stdout.close()

if __name__=='__main__':main()
