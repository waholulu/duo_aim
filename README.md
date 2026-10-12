# 采集卡 → YOLO Pose 延迟测试

本地 macOS AVFoundation + Core ML 测试，不经过网络。只做采集、检测和测量。

## 运行

```sh
swiftc -O aim/VisionCore.swift benchmark.swift -o benchmark
./benchmark yolo11n-pose-320-fp16.mlpackage 960 1000 my-run all
```

参数依次为：Core ML 模型路径、中心裁剪边长（0 表示全图）、预热后样本数、结果名称、计算设备（all/gpu/ane/cpu）。输入设备按名称 `USB3.0 capture` 选择。模型与 Python 依赖已在本目录，依赖版本见 `requirements-lock.txt`。

每轮丢弃前 30 帧作为预热；结果写入 `results/<名称>.json`。检测到可信鼻尖时另外保存最快有效帧的 PNG 和 JSON。`summarize.py results/my-run.json` 汇总指定实验；`annotate.py results/my-run-best.json` 生成对应结果的坐标叠加图。

输入取 1920×1080，设备启动后设置最高可用帧率。必须在 `startRunning()` 后设置，否则此设备在此次测试中会被系统重置为 25 FPS。驱动宣称的 240.004 FPS 不等于实测处理帧率，更不代表信号源有同样多的独立画面。`alwaysDiscardsLateVideoFrames=true` 防止应用排队处理旧帧。同步推理在串行采集回调内完成。

YOLO11n-pose 提供人框和 COCO 人体关键点。按人框中心到图像中心的距离选最近检出人物，人物置信度阈值 0.25，NMS IoU 0.45。头部代理点使用鼻尖，置信度至少 0.5 且坐标在画面内才标记有效；这不是头部轮廓中心。坐标已还原为采集画面像素，以左上角为原点，x 向右，y 向下。全图检测若仅检出角落主播，也会选中主播，不能把它算作中心游戏人物检测成功。中心 960×960 裁剪用于排除角落主播。

计时采用同一主机时钟 `CMClockGetHostTimeClock()`：

- `callback_to_coords_ms`：回调入口至解码、NMS、选人、坐标映射完成，含图像预处理、同步 Core ML 推理和后处理。
- `pts_to_callback_ms`：驱动帧 PTS 至应用回调。含驱动/缓冲影响，但 PTS 与 HDMI 源显示时刻未校准。
- `pts_to_coords_ms`：PTS 至输出坐标（前两项之和）。**不是源屏幕变化到坐标的物理端到端延迟**。
- 初次模型编译/加载、预热、日志落盘、结果图片保存不计入坐标延迟。图片保存发生在计时结束后，可能影响后续帧的调度。

真正屏幕到坐标延迟需要在 HDMI 信号源显示可对时标记，或以高速摄影同时记录源屏幕与检测反馈；当前设备回调无法推导全部 HDMI/采集卡延迟。

`results/INVALID-stride-*` 是原型阶段数组索引错误的废弃结果，禁止用于结论；正式版本使用真实张量 strides 与指针读取，并通过坐标叠加和 PyTorch 参考推理复核。

模型说明：[Ultralytics Pose](https://docs.ultralytics.com/tasks/pose/)，[Core ML 导出](https://docs.ultralytics.com/integrations/coreml/)。

## 鼠标反馈脚本

入口：`./start_aim.command`。默认通过 KMBox B+/B Pro 读取实体鼠标左键；仅按住左键时识别画面中心 960×960 像素。默认只记录建议移动，`--live` 才向 KMBox 发送相对移动。启动方法、参数和限制见 [aim/README.md](aim/README.md)。原有延迟测量脚本保持独立。

## 快速检查视频和 KMBox

双击 `check_devices.command`，或在项目目录执行 `./check_devices.command`。默认检查 3 秒，显示采集分辨率、收到的帧数、回调帧率、KMBox 固件信息和物理鼠标左键状态。需要更多时间点击鼠标时执行 `./check_devices.command --seconds 10`。此检查只查询设备，不启动 YOLO 或发送鼠标移动、点击。运行前先停止其他占用 KMBox 的控制脚本。

可通过 `--serial-port /dev/cu.usbserial-120` 和 `--device 'USB3.0 capture'` 指定设备。两路检查独立进行，失败时仍会显示另一路的结果；最新记录保存到 `results/device-check.json`，任一路异常时退出码为 1。收到帧只能证明视频数据可获取，不能单独证明 HDMI 信号内容正确或画面持续变化。

## 从 GitHub 克隆后准备环境

运行环境为 Apple Silicon macOS（Swift 命令行工具、Python 3.12）。仓库不包含虚拟环境、模型权重、Core ML 模型、采集画面或运行记录。克隆后先安装依赖并生成模型：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python - <<'PYTHON'
from pathlib import Path
import shutil
from ultralytics import YOLO
for size in (320, 640):
    source = YOLO("yolo11n-pose.pt").export(
        format="coreml", imgsz=size, quantize=16, nms=False
    )
    shutil.copytree(source, f"yolo11n-pose-{size}-fp16.mlpackage", dirs_exist_ok=True)
Path("results").mkdir(exist_ok=True)
PYTHON
./start_aim.command
```

首次导出会下载模型；连接采集卡和 KMBox、授予摄像头访问权限后即可运行。默认不移动鼠标，按住接在 KMBox 上的实体鼠标左键时才识别。

## 共享视觉核心与验证

`aim/VisionCore.swift` 是 benchmark 和流式检测器共用的裁剪、推理、张量解码、NMS 与坐标还原实现。模型契约为单个方形图像输入和单个 Float32 `[1,56,N]` 姿态输出；不支持的模型在加载时明确报错。两种入口的目标选择仍不同：benchmark 保留人框中心最近者和鼻尖置信度 0.5 的测量语义，运行策略另有目标半径、置信度、连续确认与位置连续性条件，不能直接将 benchmark 的有效头部数量当作实际控制成功率。

```sh
.venv/bin/python -m unittest discover -s tests
mkdir -p results
swiftc -O aim/VisionCore.swift tests/vision_tests.swift -o results/vision-tests
./results/vision-tests
# 可选：使用本地模型与合成图像验证推理，不打开设备
./results/vision-tests yolo11n-pose-320-fp16.mlpackage yolo11n-pose-640-fp16.mlpackage
```

实验辅助工具现在接受明确输入路径：

```sh
.venv/bin/python summarize.py results/my-run.json --output results/my-summary.json
.venv/bin/python annotate.py results/my-run-best.json
.venv/bin/python validate.py results/capture.png --models yolo11n-pose.pt --crop 960
.venv/bin/python check_models.py results/capture.png --models yolo11n-pose.pt yolo11s-pose.pt
```

`validate.py` 和 `check_models.py` 仅对指定图片运行参考推理，模型不存在时 Ultralytics 可能下载权重。`summarize.py` 拒绝 `INVALID-` 原型结果，空或单样本实验不会产生无效统计值。运行记录与手动标定见 [aim/README.md](aim/README.md)。
