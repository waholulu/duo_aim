#!/usr/bin/env python3
"""Quick video and KMBox B+/B Pro connection checks; no mouse input injection."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "aim"))
from kmbox import KMBox


def check_video(device, seconds):
    source = ROOT / "aim/video_check.swift"
    binary = ROOT / "results/video_check"
    try:
        binary.parent.mkdir(exist_ok=True)
        if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
            compiled = subprocess.run(
                ["swiftc", "-O", str(source), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            if compiled.returncode:
                return {"ok": False, "error": "视频检查程序编译失败：" + compiled.stderr.strip()}
        process = subprocess.run(
            [str(binary), device, str(seconds)], capture_output=True,
            text=True, timeout=seconds + 15)
        result = json.loads(process.stdout)
        if process.returncode != 0:
            result["ok"] = False
        return result
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "视频检查超时，未能完成采集"}
    except (OSError, ValueError) as error:
        return {"ok": False, "error": f"视频检查失败：{error}"}


def check_kmbox(port, seconds):
    try:
        with KMBox(port) as box:
            response = box.command("km.version()", timeout=1)
            version = " ".join(line.strip() for line in response.splitlines()
                               if line.strip() not in ("", "km.version()", ">>>"))
            if "kmbox" not in version.lower():
                return {"ok": False, "error": "串口回应的版本信息无法确认为 KMBox"}
            previous = None
            presses = releases = samples = 0
            started = time.monotonic()
            while time.monotonic() - started < seconds:
                held = box.physical_left()
                samples += 1
                if held != previous:
                    print(f'KMBox 左键：{"按下" if held else "松开"}', flush=True)
                    if previous is not None:
                        presses += int(held)
                        releases += int(not held)
                    previous = held
                time.sleep(0.01)
            return {"ok": True, "port": box.port, "version": version,
                    "samples": samples, "presses": presses, "releases": releases}
    except BlockingIOError:
        return {"ok": False, "error": "KMBox 串口正被其他控制脚本占用，请先停止该脚本"}
    except (OSError, RuntimeError, ValueError) as error:
        return {"ok": False, "error": f"KMBox 检查失败：{error}"}


def main():
    parser = argparse.ArgumentParser(description="快速检查采集卡视频和 KMBox；仅查询，不移动或点击")
    parser.add_argument("--seconds", type=float, default=3, help="检测时长，默认 3 秒，范围 1–60")
    parser.add_argument("--serial-port", help="默认自动查找唯一的 USB 串口")
    parser.add_argument("--device", default="USB3.0 capture")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 60:
        parser.error("--seconds 必须为 1–60 秒")
    print(f"检查视频和 KMBox（约 {args.seconds:g} 秒，首次会编译视频检查程序）。", flush=True)
    print("期间可以点击接在 KMBox 上的鼠标左键，观察按下/松开是否出现。", flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        video_task = pool.submit(check_video, args.device, args.seconds)
        box_task = pool.submit(check_kmbox, args.serial_port, args.seconds)
        video = video_task.result()
        box = box_task.result()
    if video["ok"]:
        print(f'视频正常：{video["width"]}×{video["height"]}，收到 {video["frames"]} 帧，'
              f'回调帧率 {video["fps"]:.1f} FPS。')
        print("收到视频帧不代表 HDMI 信号内容或独立画面刷新率已验证。")
    else:
        print("视频异常：" + video["error"])
    if box["ok"]:
        print(f'KMBox 正常：{box["version"]}；左键按下 {box["presses"]} 次、松开 {box["releases"]} 次。')
    else:
        print("KMBox 异常：" + box["error"])
    report = ROOT / "results/device-check.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps({"video": video, "kmbox": box}, ensure_ascii=False, indent=2) + "\n")
    print("检查记录：" + str(report))
    return 0 if video["ok"] and box["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
