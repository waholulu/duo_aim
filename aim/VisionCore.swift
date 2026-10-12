import AVFoundation
import CoreML
import CoreImage
import Foundation

func hostNow() -> Double {
    CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock()))
}

struct VisionError: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}

struct Geometry {
    let width: Double
    let height: Double
    let cw: Double
    let ch: Double
    let ox: Double
    let oy: Double
    let scale: Double
    let px: Double
    let py: Double

    init(width: Double, height: Double, crop: Double, size: Int) {
        self.width = width; self.height = height
        cw = crop > 0 ? min(crop, width) : width
        ch = crop > 0 ? min(crop, height) : height
        ox = (width-cw)/2; oy = (height-ch)/2
        scale = Double(size)/max(cw, ch)
        px = (Double(size)-cw*scale)/2; py = (Double(size)-ch*scale)/2
    }

    var cropXYWH: [Double] { [ox, oy, cw, ch] }
    func source(_ x: Double, _ y: Double) -> [Double] {
        [(x-px)/scale+ox, (y-py)/scale+oy]
    }
    func model(_ x: Double, _ y: Double) -> [Double] {
        [(x-ox)*scale+px, (y-oy)*scale+py]
    }
}

struct Detection {
    var x: Double; var y: Double; var w: Double; var h: Double; var conf: Double
    var hx: Double; var hy: Double; var hc: Double

    func mapped(_ geometry: Geometry) -> [String: Any] {
        ["confidence": conf, "head_confidence": hc,
         "head_xy": geometry.source(hx, hy),
         "person_center": geometry.source(x, y),
         "bbox_xyxy": geometry.source(x-w/2, y-h/2) + geometry.source(x+w/2, y+h/2)]
    }
}

func iou(_ a: Detection, _ b: Detection) -> Double {
    let w = max(0, min(a.x+a.w/2, b.x+b.w/2)-max(a.x-a.w/2, b.x-b.w/2))
    let h = max(0, min(a.y+a.h/2, b.y+b.h/2)-max(a.y-a.h/2, b.y-b.h/2))
    return w*h / max(1e-9, a.w*a.h+b.w*b.h-w*h)
}

func validateImageInput(count: Int, width: Int, height: Int) throws {
    guard count == 1, width > 0, width == height else {
        throw VisionError("Expected one square image input")
    }
}

func validateTensor(shape: [Int], dataType: MLMultiArrayDataType) throws {
    guard dataType == .float32, shape.count == 3, shape[0] == 1,
          shape[1] == 56, shape[2] > 0 else {
        throw VisionError("Expected Float32 pose tensor [1,56,N], got \(shape) / \(dataType)")
    }
}

func decodePose(_ array: MLMultiArray) throws -> [Detection] {
    try validateTensor(shape: array.shape.map(\.intValue), dataType: array.dataType)
    let s1 = array.strides[1].intValue, s2 = array.strides[2].intValue
    guard s1 > 0, s2 > 0 else { throw VisionError("Unsupported tensor strides") }
    let values = array.dataPointer.assumingMemoryBound(to: Float.self)
    func val(_ channel: Int, _ item: Int) -> Double { Double(values[channel*s1+item*s2]) }
    var candidates: [Detection] = []
    for j in 0..<array.shape[2].intValue where val(4, j) >= 0.25 {
        let v = (0..<8).map { val($0, j) }
        guard v.allSatisfy(\.isFinite), v[2] > 0, v[3] > 0,
              v[4] <= 1, v[7] >= 0, v[7] <= 1 else { continue }
        candidates.append(Detection(x:v[0], y:v[1], w:v[2], h:v[3], conf:v[4], hx:v[5], hy:v[6], hc:v[7]))
    }
    return nonMaximumSuppression(candidates)
}

func nonMaximumSuppression(_ candidates: [Detection]) -> [Detection] {
    var kept: [Detection] = []
    for detection in candidates.sorted(by: { $0.conf > $1.conf }) {
        if !kept.contains(where: { iou(detection, $0) > 0.45 }) { kept.append(detection) }
    }
    return kept
}

struct VisionResult {
    let geometry: Geometry
    let detections: [Detection]
    let original: CIImage
    let start: Double
    let prep: Double
    let infer: Double

    func timings(pts: Double) -> [String: Any] {
        let end = hostNow() // Include caller coordinate mapping and selection.
        return ["callback_to_coords_ms": (end-start)*1000, "preprocess_ms": (prep-start)*1000,
         "inference_ms": (infer-prep)*1000, "postprocess_ms": (end-infer)*1000,
         "pts_to_callback_ms": (start-pts)*1000, "pts_to_coords_ms": (end-pts)*1000,
         "host_start_s": start, "pts_s": pts]
    }
}

final class VisionCore {
    let model: MLModel
    let inputName: String
    let outputName: String
    let size: Int
    let context = CIContext(options: [.cacheIntermediates: false])
    let buffer: CVPixelBuffer

    init(path: String, units: String) throws {
        let config = MLModelConfiguration()
        config.computeUnits = units == "cpu" ? .cpuOnly : units == "gpu" ? .cpuAndGPU : units == "ane" ? .cpuAndNeuralEngine : .all
        let compiled = try MLModel.compileModel(at: URL(fileURLWithPath: path))
        model = try MLModel(contentsOf: compiled, configuration: config)
        let inputs = model.modelDescription.inputDescriptionsByName
        let outputs = model.modelDescription.outputDescriptionsByName
        guard inputs.count == 1, let input = inputs.first,
              input.value.type == .image, let image = input.value.imageConstraint else {
            throw VisionError("Expected one square image input")
        }
        try validateImageInput(count:inputs.count, width:image.pixelsWide, height:image.pixelsHigh)
        guard outputs.count == 1, let output = outputs.first,
              output.value.type == .multiArray, let tensor = output.value.multiArrayConstraint else {
            throw VisionError("Expected one pose tensor output")
        }
        try validateTensor(shape: tensor.shape.map(\.intValue), dataType: tensor.dataType)
        inputName = input.key; outputName = output.key; size = image.pixelsWide
        var allocated: CVPixelBuffer?
        let status = CVPixelBufferCreate(kCFAllocatorDefault, size, size, kCVPixelFormatType_32BGRA,
            [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &allocated)
        guard status == kCVReturnSuccess, let allocated else { throw VisionError("Cannot allocate model image buffer") }
        buffer = allocated
    }

    func infer(_ pixelBuffer: CVPixelBuffer, crop: Double, start: Double) throws -> VisionResult {
        let geometry = Geometry(width: Double(CVPixelBufferGetWidth(pixelBuffer)),
                                height: Double(CVPixelBufferGetHeight(pixelBuffer)), crop: crop, size: size)
        let original = CIImage(cvPixelBuffer: pixelBuffer)
        let g = geometry
        let cropped = original.cropped(to: CGRect(x:g.ox, y:g.oy, width:g.cw, height:g.ch))
            .transformed(by: CGAffineTransform(translationX:-g.ox, y:-g.oy))
            .transformed(by: CGAffineTransform(scaleX:g.scale, y:g.scale))
            .transformed(by: CGAffineTransform(translationX:g.px, y:g.py))
        let background = CIImage(color: CIColor(red:114/255, green:114/255, blue:114/255))
            .cropped(to: CGRect(x:0, y:0, width:size, height:size))
        context.render(cropped.composited(over:background), to:buffer)
        let prep = hostNow()
        let input = try MLDictionaryFeatureProvider(dictionary:[inputName: MLFeatureValue(pixelBuffer:buffer)])
        let prediction = try model.prediction(from:input)
        let infer = hostNow()
        guard let array = prediction.featureValue(for:outputName)?.multiArrayValue else {
            throw VisionError("Missing pose output \(outputName)")
        }
        let detections = try decodePose(array)
        return VisionResult(geometry:geometry, detections:detections, original:original,
                            start:start, prep:prep, infer:infer)
    }
}

// This device resets frame rate during startRunning; configure afterwards.
func startCapture(delegate: AVCaptureVideoDataOutputSampleBufferDelegate,
                  queue: DispatchQueue) throws -> (AVCaptureSession, Double) {
    guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else {
        throw VisionError("Camera permission required")
    }
    guard let device = AVCaptureDevice.DiscoverySession(deviceTypes:[.external], mediaType:.video,
        position:.unspecified).devices.first(where: { $0.localizedName == "USB3.0 capture" }) else {
        throw VisionError("USB3.0 capture not found")
    }
    let session = AVCaptureSession()
    let output = AVCaptureVideoDataOutput()
    let input = try AVCaptureDeviceInput(device:device)
    guard session.canAddInput(input), session.canAddOutput(output) else {
        throw VisionError("Cannot configure capture session")
    }
    session.beginConfiguration()
    session.addInput(input)
    output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32BGRA]
    output.alwaysDiscardsLateVideoFrames = true
    output.setSampleBufferDelegate(delegate, queue:queue)
    session.addOutput(output)
    session.commitConfiguration()
    session.startRunning()
    do {
        try device.lockForConfiguration()
        defer { device.unlockForConfiguration() }
        let formats = device.formats.filter {
            let d = CMVideoFormatDescriptionGetDimensions($0.formatDescription)
            return d.width == 1920 && d.height == 1080
        }
        guard let format = formats.max(by: {
            ($0.videoSupportedFrameRateRanges.map(\.maxFrameRate).max() ?? 0) <
            ($1.videoSupportedFrameRateRanges.map(\.maxFrameRate).max() ?? 0)
        }), let range = format.videoSupportedFrameRateRanges.max(by: { $0.maxFrameRate < $1.maxFrameRate }) else {
            throw VisionError("No supported 1920x1080 capture format")
        }
        device.activeFormat = format
        device.activeVideoMinFrameDuration = range.minFrameDuration
        device.activeVideoMaxFrameDuration = range.minFrameDuration
        return (session, range.maxFrameRate)
    } catch {
        session.stopRunning()
        throw error
    }
}
