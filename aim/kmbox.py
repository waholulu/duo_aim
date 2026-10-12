"""Serial control for the KMBox B+/B Pro REPL."""

import fcntl
import glob
import os
import re
import select
import termios
import time


class KMBox:
    @staticmethod
    def resolve_port(port=None):
        if port is not None:
            return port
        ports = sorted(glob.glob('/dev/cu.usbserial*'))
        if not ports:
            raise FileNotFoundError('未发现 KMBox USB 串口，请检查连接到 Mac 的 USB 线')
        if len(ports) != 1:
            raise ValueError('发现多个 USB 串口，请用 --serial-port 指定：' + ', '.join(ports))
        return ports[0]

    def __init__(self, port=None):
        port = self.resolve_port(port)
        self.port = port
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.old_settings = None
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.old_settings = termios.tcgetattr(self.fd)
            settings = termios.tcgetattr(self.fd)
            settings[0] = settings[1] = settings[3] = 0
            settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            settings[4] = settings[5] = termios.B115200
            settings[6][termios.VMIN] = settings[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
            termios.tcflush(self.fd, termios.TCIFLUSH)
            self.command("\x03", timeout=1)
        except BaseException:
            self.close()
            raise

    def command(self, expression, timeout=0.5):
        if "\r" in expression or "\n" in expression:
            raise ValueError("Expected one KMBox expression")
        deadline = time.monotonic() + timeout
        data = (expression + "\r\n").encode("ascii")
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [self.fd], [], remaining)[1]:
                raise TimeoutError("KMBox write timed out")
            written = os.write(self.fd, data)
            if written <= 0:
                raise ConnectionError("KMBox write failed")
            data = data[written:]
        response = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                raise TimeoutError("KMBox response timed out")
            chunk = os.read(self.fd, 8192)
            if not chunk:
                raise ConnectionError("KMBox disconnected")
            response.extend(chunk)
            if len(response) > 65536:
                raise RuntimeError("KMBox response too large")
            if response.endswith(b">>> "):
                result = response.decode("ascii", errors="replace")
                if "Traceback" in result or "Error:" in result:
                    raise RuntimeError(result)
                return result

    def physical_left(self):
        response = self.command("km.left()", timeout=0.25)
        values = [line.strip() for line in response.splitlines()]
        states = [int(line) for line in values if re.fullmatch(r"[0-3]", line)]
        if len(states) != 1:
            raise ValueError(f"Unexpected KMBox left-button response: {response!r}")
        return bool(states[0] & 1)

    def move(self, dx, dy):
        if type(dx) is not int or type(dy) is not int or max(abs(dx), abs(dy)) > 128:
            raise ValueError("KMBox movement out of bounds")
        self.command(f"km.move({dx},{dy})")

    def close(self):
        if self.fd is not None:
            try:
                if self.old_settings is not None:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.old_settings)
            finally:
                os.close(self.fd)
                self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
