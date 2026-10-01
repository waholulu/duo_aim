# 明天测试：采集卡画面 → 人物头部 → 鼠标相对移动

已准备原生低延迟检测器和鼠标反馈控制脚本。默认只检测、记录建议位移，不移动鼠标。当前网页无合适人物也可以启动验证连接。

## 1. 先看检测结果

在项目目录的终端执行，或双击 `start_aim.command`：

```sh
./start_aim.command
```

终端每半秒显示一次 `head`（采集像素坐标）、`error`（相对画面中心偏移）、`dx/dy`（建议鼠标相对位移）。`no_target` 表示当前没有合格头部，`confirming` 表示等待连续帧确认，`move` 表示建议移动，`aligned` 表示已接近中心。完整记录保存在 `results/aim-日期-时间.jsonl`。

默认：960×960中心裁剪、640×640 FP16 YOLO11n-pose、人物置信度≥0.4、鼻尖置信度≥0.65、中心半径300像素、连续2帧确认。鼻尖是头部代理点，不是头部轮廓中心。当前通用模型此前未能可靠识别烟雾中的游戏人物，因此明天首先验证它能稳定检出目标。

可限时试运行：`./start_aim.command --seconds 10`。Ctrl+C退出。

## 2A. 控制这台 Mac 上的视角

```sh
# 按住右键时修正，松开立即停止
./start_aim.command --live

# 用户启动后，只要检测到合格头部就自动修正
./start_aim.command --live --activation auto
```

实际输出需要为启动脚本的终端授予macOS“辅助功能”权限；脚本会检查权限。Esc或Ctrl+C结束。请先进入待测试的游戏/应用，再启用实际输出。脚本不识别哪个窗口是游戏；网页中的人像如果符合筛选条件也可能成为目标。

## 2B. 控制另一台 Windows 电脑

**HDMI采集卡仅提供视频，不能替另一台电脑移动鼠标。** 为此准备了一个Windows接收器，只需Python 3标准库。把 `windows_receiver.py` 复制到目标电脑。

两台机器设置相同随机口令。Mac可以先生成：

```sh
.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'
```

Windows PowerShell：

```powershell
$env:AIM_TOKEN="把刚生成的口令填到这里"
# 先只接收、打印，不移动
python windows_receiver.py --bind 0.0.0.0
# 确认链路后，重新启动为按住右键控制
python windows_receiver.py --bind 0.0.0.0 --live
# 若要检测到即自动修正，额外加 --activation auto
```

Mac终端：

```sh
export AIM_TOKEN='同一个口令'
./start_aim.command --live --backend udp --host WINDOWS的局域网IP
```

Windows端的右键/自动模式决定是否实际移动；Mac不需要按住右键。UDP端口39871，若防火墙阻止，需允许局域网入站。两机时钟需自动同步：接收器拒收超过150ms或明显来自未来的包，无法同步时可能不移动。每个接收器运行只接受一个发送会话，重启Mac发送器时也重启接收器。消息带HMAC认证、序号和时效检查，不存储鼠标指令供稍后执行。

Windows Esc或Ctrl+C停止；Mac退出会停止发包。Windows接口尚未在目标机器实测。游戏如果不接受普通系统合成鼠标输入，需要针对实际输入设备另行接入；此版本未实现USB HID硬件协议，也不保证所有游戏兼容。

## 3. 调整视角速度

这不是把桌面光标放在头部坐标上，而是反复发送相对鼠标移动，让游戏视角把头部移到画面中心。`dx = 水平像素误差 × gain_x`，`dy`同理。画面像素不是鼠标计数，无法在未知游戏灵敏度时保证一次移动正好对齐。

默认每25ms最多发送一次，每轴最多32计数，误差3像素内停动。先从默认0.15开始：

```sh
./start_aim.command --live --gain-x 0.15 --gain-y 0.15 --max-step 32
```

- 向目标移动太慢：逐步提高gain。
- 来回越过头部：降低gain，或增加 `--interval-ms`，等待上一次动作反映到采集画面。
- 游戏启用了反转视角：对应轴使用负gain。
- 检测范围可调：`--radius 250`。想试更低计算延迟可指定 `--model yolo11n-pose-320-fp16.mlpackage`，小人物识别可能更差。

目标消失、置信度不足、帧过期或停用时不发送新移动。只保留最新检测；同一帧不会重复执行。不会点击或开火。

## 已验证与待验证

- 已编译Swift检测器；已在当前采集卡上无鼠标输出运行。
- 单元测试覆盖移动方向、中心筛选、连续帧确认、死区、步幅限制、重复/过期帧、丢失目标、反馈收敛模拟、UDP鉴权和重放拒绝。
- 尚未在游戏中执行真实鼠标移动；人物识别质量、游戏灵敏度、输入兼容性和最终对齐效果留待明天验证。
- 现有2.24ms是上一轮检测计算最小值，不是这套鼠标反馈闭环的完成时间。日志中 `pts_to_controller_ms` 包含检测后排队时间，仍不含鼠标到游戏响应再到采集卡的完整闭环延迟。

代码：`detector.swift`负责采集和YOLO推理，`policy.py`负责目标筛选和反馈计算，`run.py`负责启动/日志/本机或网络输出，`windows_receiver.py`负责Windows鼠标输入。

接口参考：[Apple鼠标相对位移字段](https://developer.apple.com/documentation/coregraphics/cgeventfield/mouseeventdeltax)、[Microsoft MOUSEINPUT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput)。
