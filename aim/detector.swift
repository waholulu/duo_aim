import AVFoundation
import AppKit
import Foundation

final class InferenceGate {
    private let lock = NSLock()
    private var active = false
    private var epoch = 0

    func set(_ active: Bool, epoch: Int) {
        lock.lock(); defer { lock.unlock() }
        self.active = active; self.epoch = epoch
    }
    func snapshot() -> (Bool, Int) {
        lock.lock(); defer { lock.unlock() }
        return (active, epoch)
    }
}

func emit(_ event: [String: Any]) throws {
    var row = event
    row["protocol_version"] = 1
    var data = try JSONSerialization.data(withJSONObject:row)
    data.append(10)
    try FileHandle.standardOutput.write(contentsOf:data)
}

final class StreamDetector: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let vision: VisionCore
    let crop: Double
    let gate: InferenceGate
    let samples: Int
    let done = DispatchSemaphore(value:0)
    var started = false // Accessed only on capture queue.
    var count = 0
    var warmup = 0
    var measured = 0
    var failed = false
    var completed = false
    var lastFrameSize: [Int] = []
    let diagnosticPath = ProcessInfo.processInfo.environment["AIM_DIAGNOSTIC_FRAME"]

    init(vision: VisionCore, crop: Double, gate: InferenceGate, samples: Int) {
        self.vision = vision; self.crop = crop; self.gate = gate; self.samples = samples
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard started, !completed else { return }
        let start = hostNow()
        let index = count; count += 1
        let (active, epoch) = gate.snapshot()
        guard let buffer = CMSampleBufferGetImageBuffer(sample) else { return }
        let dimensions = [CVPixelBufferGetWidth(buffer), CVPixelBufferGetHeight(buffer)]
        if dimensions != lastFrameSize {
            lastFrameSize = dimensions
            let geometry = Geometry(width:Double(dimensions[0]), height:Double(dimensions[1]), crop:crop, size:vision.size)
            fputs("Capture geometry: frame=\(dimensions), crop=\(geometry.cropXYWH)\n", stderr)
        }
        guard active else { return }
        autoreleasepool {
            do {
                let result = try vision.infer(buffer, crop:crop, start:start)
                if warmup < 30 {
                    warmup += 1
                    try emit(["type":"warmup", "gate_epoch":epoch, "completed":warmup, "total":30])
                    return
                }
                let g = result.geometry
                var row: [String: Any] = ["type":"frame", "index":index, "gate_epoch":epoch,
                    "width":Int(g.width), "height":Int(g.height), "crop_xywh":g.cropXYWH,
                    "detections":result.detections.map { $0.mapped(g) }]
                row.merge(result.timings(pts:CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample)))) { _, new in new }
                if measured == 0, let path = diagnosticPath,
                   let image = vision.context.createCGImage(result.original, from:result.original.extent),
                   let png = NSBitmapImageRep(cgImage:image).representation(using:.png, properties:[:]) {
                    try png.write(to:URL(fileURLWithPath:path))
                    try JSONSerialization.data(withJSONObject:row, options:[.prettyPrinted, .sortedKeys])
                        .write(to:URL(fileURLWithPath:path + ".json"))
                }
                try emit(row)
                measured += 1
                if samples > 0 && measured >= samples { completed = true; done.signal() }
            } catch {
                fputs("Detector error: \(error)\n", stderr)
                failed = true; completed = true; done.signal()
            }
        }
    }
}

@main
struct DetectorMain {
    static func main() {
        do { try run() }
        catch { fputs("Detector error: \(error)\n", stderr); exit(1) }
    }
    static func run() throws {
        let args = CommandLine.arguments
        let path = args.count > 1 ? args[1] : "yolo11n-pose.mlpackage"
        let crop = args.count > 2 ? Double(args[2]) ?? -1 : 0
        let samples = args.count > 3 ? Int(args[3]) ?? -1 : 0
        let units = args.count > 5 ? args[5] : "all"
        let gated = args.count > 6 && args[6] == "gated"
        guard crop.isFinite, crop >= 0, samples >= 0, ["all", "cpu", "gpu", "ane"].contains(units) else {
            throw VisionError("Invalid detector arguments")
        }
        let vision = try VisionCore(path:path, units:units)
        let gate = InferenceGate()
        let detector = StreamDetector(vision:vision, crop:crop, gate:gate, samples:samples)
        if gated {
            DispatchQueue.global(qos:.userInteractive).async {
                while let line = readLine() {
                    let fields = line.split(separator:" ")
                    if fields.count == 2, let epoch = Int(fields[1]), epoch >= 0,
                       fields[0] == "0" || fields[0] == "1" {
                        gate.set(fields[0] == "1", epoch:epoch)
                    }
                }
                gate.set(false, epoch:-1)
                detector.done.signal() // Parent gone: release capture instead of waiting forever.
            }
        } else { gate.set(true, epoch:0) }
        let queue = DispatchQueue(label:"capture.inference", qos:.userInteractive)
        let (session, fps) = try startCapture(delegate:detector, queue:queue)
        defer { session.stopRunning() }
        // Serialize readiness before the first result, after capture configuration.
        try queue.sync {
            try emit(["type":"ready", "model_size":vision.size, "warmup_total":30, "requested_fps":fps])
            detector.started = true
        }
        if samples > 0 {
            guard detector.done.wait(timeout:.now()+120) == .success else {
                throw VisionError("Timed out waiting for requested samples")
            }
        } else { detector.done.wait() }
        session.stopRunning()
        let (failed, measured) = queue.sync { (detector.failed, detector.measured) }
        if failed { throw VisionError("Inference stopped") }
        if samples > 0 && measured < samples { throw VisionError("Control input closed before requested samples completed") }
    }
}
