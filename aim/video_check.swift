import AVFoundation
import Foundation

func finish(_ result: [String: Any], code: Int32 = 0) -> Never {
    if let data = try? JSONSerialization.data(withJSONObject: result) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([10]))
    }
    exit(code)
}

let arguments = CommandLine.arguments
let deviceName = arguments.count > 1 ? arguments[1] : "USB3.0 capture"
let duration = arguments.count > 2 ? Double(arguments[2]) ?? 3 : 3
guard duration.isFinite && duration >= 1 && duration <= 60 else {
    finish(["ok": false, "error": "检查时长必须为 1–60 秒"], code: 1)
}
guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else {
    finish(["ok": false, "error": "摄像头权限未授权，请在系统设置中授权后再检查"], code: 1)
}
let devices = AVCaptureDevice.DiscoverySession(
    deviceTypes: [.external], mediaType: .video, position: .unspecified).devices
guard let device = devices.first(where: { $0.localizedName == deviceName }) else {
    finish(["ok": false, "error": "未找到采集卡：\(deviceName)",
            "devices": devices.map { $0.localizedName }], code: 1)
}

class VideoProbe: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    var frames = 0
    var firstTime = 0.0
    var lastTime = 0.0
    var width = 0
    var height = 0
    var totalAge = 0.0
    var ageSamples = 0

    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let buffer = CMSampleBufferGetImageBuffer(sample) else { return }
        let time = CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock()))
        if frames == 0 { firstTime = time }
        lastTime = time
        frames += 1
        width = CVPixelBufferGetWidth(buffer)
        height = CVPixelBufferGetHeight(buffer)
        let age = (time - CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))) * 1000
        if age.isFinite && age >= 0 {
            totalAge += age
            ageSamples += 1
        }
    }
}

let session = AVCaptureSession()
let output = AVCaptureVideoDataOutput()
let probe = VideoProbe()
let queue = DispatchQueue(label: "video.healthcheck")
do {
    let input = try AVCaptureDeviceInput(device: device)
    guard session.canAddInput(input), session.canAddOutput(output) else {
        finish(["ok": false, "error": "无法建立采集会话"], code: 1)
    }
    session.beginConfiguration()
    session.addInput(input)
    output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
    output.alwaysDiscardsLateVideoFrames = true
    output.setSampleBufferDelegate(probe, queue: queue)
    session.addOutput(output)
    session.commitConfiguration()
    session.startRunning()
    defer { session.stopRunning() }
    // This capture card resets the rate at startup; configure after startRunning.
    try device.lockForConfiguration()
    let formats = device.formats.filter {
        let dimensions = CMVideoFormatDescriptionGetDimensions($0.formatDescription)
        return dimensions.width == 1920 && dimensions.height == 1080
    }
    if let format = formats.max(by: {
        ($0.videoSupportedFrameRateRanges.map(\.maxFrameRate).max() ?? 0) <
        ($1.videoSupportedFrameRateRanges.map(\.maxFrameRate).max() ?? 0)
    }), let range = format.videoSupportedFrameRateRanges.max(by: { $0.maxFrameRate < $1.maxFrameRate }) {
        device.activeFormat = format
        device.activeVideoMinFrameDuration = range.minFrameDuration
        device.activeVideoMaxFrameDuration = range.minFrameDuration
    }
    device.unlockForConfiguration()
    // Discard startup frames, then measure only the requested interval.
    Thread.sleep(forTimeInterval: 0.2)
    queue.sync {
        probe.frames = 0
        probe.totalAge = 0
        probe.ageSamples = 0
    }
    Thread.sleep(forTimeInterval: duration)
    session.stopRunning()
    var result: [String: Any] = [:]
    queue.sync {
        let elapsed = probe.lastTime - probe.firstTime
        result = ["ok": probe.frames >= 2, "device": device.localizedName,
                  "frames": probe.frames, "width": probe.width, "height": probe.height,
                  "fps": elapsed > 0 ? Double(probe.frames - 1) / elapsed : 0]
        if probe.ageSamples > 0 { result["pts_age_ms"] = probe.totalAge / Double(probe.ageSamples) }
        if probe.frames < 2 { result["error"] = "采集会话已打开，但未收到连续视频帧" }
    }
    finish(result, code: probe.frames >= 2 ? 0 : 1)
} catch {
    session.stopRunning()
    finish(["ok": false, "error": "视频检查失败：\(error.localizedDescription)"], code: 1)
}
