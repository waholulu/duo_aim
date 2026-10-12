import AVFoundation
import AppKit
import Foundation

final class BenchmarkDetector: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let vision: VisionCore
    let crop: Double
    let samples: Int
    let tag: String
    let done = DispatchSemaphore(value:0)
    var started = false
    var count = 0
    var dropped = 0
    var rows = [[String: Any]]()
    var best = Double.infinity
    var bestImage: CGImage?
    var bestRow: [String: Any]?
    var completed = false
    var failed = false

    init(vision: VisionCore, crop: Double, samples: Int, tag: String) {
        self.vision = vision; self.crop = crop; self.samples = samples; self.tag = tag
    }
    func captureOutput(_ output: AVCaptureOutput, didDrop sample: CMSampleBuffer,
                       from connection: AVCaptureConnection) { dropped += 1 }
    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard started, !completed else { return }
        let start = hostNow()
        guard let buffer = CMSampleBufferGetImageBuffer(sample) else { return }
        autoreleasepool {
            do {
                let result = try vision.infer(buffer, crop:crop, start:start)
                let g = result.geometry
                let center = Double(vision.size)/2
                let selected = result.detections.min {
                    pow($0.x-center, 2)+pow($0.y-center, 2) < pow($1.x-center, 2)+pow($1.y-center, 2)
                }
                var row: [String: Any] = ["index":count, "width":Int(g.width), "height":Int(g.height),
                    "people":result.detections.count, "target":false, "head_valid":false, "crop_xywh":g.cropXYWH]
                if let selected {
                    row.merge(selected.mapped(g)) { _, new in new }
                    let head = g.source(selected.hx, selected.hy)
                    row["target"] = true
                    row["head_valid"] = selected.hc >= 0.5 && head[0] >= 0 && head[0] < g.width && head[1] >= 0 && head[1] < g.height
                }
                row.merge(result.timings(pts:CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample)))) { _, new in new }
                if count >= 30 {
                    rows.append(row)
                    let latency = row["callback_to_coords_ms"] as! Double
                    if row["head_valid"] as! Bool && latency < best {
                        best = latency; bestRow = row
                        bestImage = vision.context.createCGImage(result.original, from:result.original.extent)
                    }
                }
                count += 1
                if count % 100 == 0 { print("\(tag) \(count) frames") }
                if rows.count >= samples { completed = true; done.signal() }
            } catch {
                fputs("Benchmark error: \(error)\n", stderr)
                failed = true; completed = true; done.signal()
            }
        }
    }
}

@main
struct BenchmarkMain {
    static func main() {
        do { try run() }
        catch { fputs("Benchmark error: \(error)\n", stderr); exit(1) }
    }
    static func run() throws {
        let args = CommandLine.arguments
        let path = args.count > 1 ? args[1] : "yolo11n-pose.mlpackage"
        let crop = args.count > 2 ? Double(args[2]) ?? -1 : 0
        let samples = args.count > 3 ? Int(args[3]) ?? -1 : 500
        let tag = args.count > 4 ? args[4] : "run"
        let units = args.count > 5 ? args[5] : "all"
        guard crop.isFinite, crop >= 0, samples > 0, ["all", "cpu", "gpu", "ane"].contains(units),
              !tag.contains("/"), !tag.isEmpty else { throw VisionError("Invalid benchmark arguments") }
        let vision = try VisionCore(path:path, units:units)
        let detector = BenchmarkDetector(vision:vision, crop:crop, samples:samples, tag:tag)
        let queue = DispatchQueue(label:"capture.benchmark", qos:.userInteractive)
        let (session, fps) = try startCapture(delegate:detector, queue:queue)
        queue.sync { detector.started = true }
        let status = detector.done.wait(timeout:.now()+120)
        session.stopRunning()
        // Drain callbacks before reading or saving detector state.
        queue.sync { detector.completed = true }
        let metadata: [String: Any] = ["model":path, "model_size":vision.size, "crop":crop,
            "compute_units":units, "warmup":30, "samples":detector.rows.count,
            "dropped_callbacks":detector.dropped, "status":"\(status)", "requested_fps":fps,
            "rows":detector.rows, "vision_core_version":1, "failed":detector.failed]
        try FileManager.default.createDirectory(atPath:"results", withIntermediateDirectories:true)
        try JSONSerialization.data(withJSONObject:metadata, options:[.prettyPrinted, .sortedKeys])
            .write(to:URL(fileURLWithPath:"results/\(tag).json"))
        if let image = detector.bestImage, let row = detector.bestRow {
            try NSBitmapImageRep(cgImage:image).representation(using:.png, properties:[:])!
                .write(to:URL(fileURLWithPath:"results/\(tag)-best.png"))
            try JSONSerialization.data(withJSONObject:row, options:[.prettyPrinted, .sortedKeys])
                .write(to:URL(fileURLWithPath:"results/\(tag)-best.json"))
        }
        guard status == .success, !detector.failed else { throw VisionError("Benchmark incomplete; inspect saved status") }
        print("FINISHED \(tag) samples \(detector.rows.count), dropped \(detector.dropped)")
    }
}
