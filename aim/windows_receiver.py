#!/usr/bin/env python3
"""Windows relative mouse receiver, standard library only; dry run by default."""
import argparse,ctypes,hashlib,hmac,json,math,os,socket,time

class Validator:
    def __init__(self,key,max_step=32):
        self.key=key;self.max_step=max_step;self.session=None;self.seq=-1
    def decode(self,packet,now):
        try:
            envelope=json.loads(packet)
            raw=envelope['payload'].encode()
            if not hmac.compare_digest(hmac.new(self.key,raw,hashlib.sha256).hexdigest(),envelope['mac']):return None
            p=json.loads(raw)
            if type(p['seq']) is not int or p['seq']<0:return None
            if type(p['session']) is not str or not p['session'] or len(p['session'])>64:return None
            if self.session is not None and p['session']!=self.session:return None
            if p['seq']<=self.seq:return None
            if not math.isfinite(p['ts']) or not (-.05<=now-p['ts']<=.15):return None
            for k in ('dx','dy'):
                if type(p[k]) is not int or abs(p[k])>self.max_step:return None
            self.session=p['session'];self.seq=p['seq']
            return p
        except (KeyError,ValueError,TypeError,AttributeError,OverflowError):return None

class WindowsMouse:
    def __init__(self):
        if os.name!='nt':raise RuntimeError('Live mouse output requires Windows.')
        from ctypes import wintypes as w
        ULONG_PTR=ctypes.c_size_t
        class MOUSEINPUT(ctypes.Structure):
            _fields_=[('dx',w.LONG),('dy',w.LONG),('mouseData',w.DWORD),('dwFlags',w.DWORD),('time',w.DWORD),('dwExtraInfo',ULONG_PTR)]
        class KEYBDINPUT(ctypes.Structure):
            _fields_=[('wVk',w.WORD),('wScan',w.WORD),('dwFlags',w.DWORD),('time',w.DWORD),('dwExtraInfo',ULONG_PTR)]
        class HARDWAREINPUT(ctypes.Structure):
            _fields_=[('uMsg',w.DWORD),('wParamL',w.WORD),('wParamH',w.WORD)]
        class U(ctypes.Union):_fields_=[('mi',MOUSEINPUT),('ki',KEYBDINPUT),('hi',HARDWAREINPUT)]
        class INPUT(ctypes.Structure):_fields_=[('type',w.DWORD),('u',U)]
        self.INPUT=INPUT;self.MOUSEINPUT=MOUSEINPUT
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.user.SendInput.argtypes=[w.UINT,ctypes.POINTER(INPUT),ctypes.c_int]
        self.user.SendInput.restype=w.UINT
        self.user.GetAsyncKeyState.argtypes=[ctypes.c_int]
        self.user.GetAsyncKeyState.restype=ctypes.c_short
    def pressed(self,key):return bool(self.user.GetAsyncKeyState(key)&0x8000)
    def move(self,dx,dy):
        event=self.INPUT();event.type=0
        event.u.mi=self.MOUSEINPUT(dx,dy,0,0x0001,0,0)
        if self.user.SendInput(1,ctypes.byref(event),ctypes.sizeof(event))!=1:
            raise ctypes.WinError(ctypes.get_last_error())

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bind',default='127.0.0.1')
    ap.add_argument('--port',type=int,default=39871)
    ap.add_argument('--live',action='store_true')
    ap.add_argument('--activation',choices=['hold-right','auto'],default='hold-right')
    args=ap.parse_args()
    key=os.environ.get('AIM_TOKEN','').encode()
    if len(key)<32:ap.error('Set AIM_TOKEN to the same random 32+ character value on both machines.')
    validator=Validator(key);mouse=WindowsMouse() if args.live else None
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind((args.bind,args.port));sock.settimeout(.05)
    print(f'{"LIVE" if args.live else "DRY RUN"} {args.bind}:{args.port}; Esc or Ctrl+C stops. Sender restart requires receiver restart.',flush=True)
    last_move=0;last_print=0
    try:
        while True:
            if mouse and mouse.pressed(0x1B):break
            try:packet,_=sock.recvfrom(4096)
            except socket.timeout:continue
            p=validator.decode(packet,time.time())
            # Drop queued old movement requests. Apply at most the newest valid one.
            sock.setblocking(False)
            try:
                for _ in range(128):
                    try:packet,_=sock.recvfrom(4096)
                    except BlockingIOError:break
                    newer=validator.decode(packet,time.time())
                    if newer is not None:p=newer
            finally:sock.settimeout(.05)
            if p is None:continue
            active=not mouse or args.activation=='auto' or mouse.pressed(0x02)
            if mouse and mouse.pressed(0x1B):break
            t=time.monotonic()
            if active and t-last_move>=.01 and -.05<=time.time()-p['ts']<=.15:
                if mouse:mouse.move(p['dx'],p['dy'])
                last_move=t
                if t-last_print>.5:print(p,flush=True);last_print=t
    except KeyboardInterrupt:pass
    finally:sock.close()

if __name__=='__main__':main()
