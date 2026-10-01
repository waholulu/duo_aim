import AVFoundation
import AppKit
import CoreML
import Foundation

func now() -> Double { CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock())) }
let args = CommandLine.arguments
let modelPath = args.count > 1 ? args[1] : "yolo11n-pose.mlpackage"
let cropSize = args.count > 2 ? Double(args[2])! : 0
let samples = args.count > 3 ? Int(args[3])! : 0
let tag = args.count > 4 ? args[4] : "run"
let units = args.count > 5 ? args[5] : "all"
let config = MLModelConfiguration()
config.computeUnits = units == "cpu" ? .cpuOnly : units == "gpu" ? .cpuAndGPU : units == "ane" ? .cpuAndNeuralEngine : .all
let compiled = try MLModel.compileModel(at: URL(fileURLWithPath: modelPath))
let model = try MLModel(contentsOf: compiled, configuration: config)
let inputName = model.modelDescription.inputDescriptionsByName.keys.first!
let size = model.modelDescription.inputDescriptionsByName[inputName]!.imageConstraint!.pixelsWide
fputs("Model ready: \(size) px\n", stderr)
let done = DispatchSemaphore(value: 0)
struct Detection {
 var x: Double; var y: Double; var w: Double; var h: Double; var conf: Double
 var hx: Double; var hy: Double; var hc: Double
}
func iou(_ a: Detection, _ b: Detection) -> Double {
 let w = max(0, min(a.x+a.w/2,b.x+b.w/2)-max(a.x-a.w/2,b.x-b.w/2))
 let h = max(0, min(a.y+a.h/2,b.y+b.h/2)-max(a.y-a.h/2,b.y-b.h/2))
 return w*h / max(1e-9,a.w*a.h+b.w*b.h-w*h)
}
class Detector: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
 let context = CIContext(options: [.cacheIntermediates: false])
 var buffer: CVPixelBuffer!
 var count = 0; var dropped = 0
 var measured = 0


 var completed = false
 var failed = false
 override init() {
  super.init()
  CVPixelBufferCreate(kCFAllocatorDefault,size,size,kCVPixelFormatType_32BGRA,[kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary,&buffer)
 }
 func captureOutput(_ output: AVCaptureOutput, didDrop sb: CMSampleBuffer, from connection: AVCaptureConnection) { dropped += 1 }
 func captureOutput(_ output: AVCaptureOutput, didOutput sb: CMSampleBuffer, from connection: AVCaptureConnection) {
  if completed { return }
  autoreleasepool {
   let start = now()

   guard let pb = CMSampleBufferGetImageBuffer(sb) else { return }
   let width = Double(CVPixelBufferGetWidth(pb)), height = Double(CVPixelBufferGetHeight(pb))
   let cw = cropSize > 0 ? min(cropSize,width) : width
   let ch = cropSize > 0 ? min(cropSize,height) : height
   let ox = (width-cw)/2, oy = (height-ch)/2
   let scale = Double(size)/max(cw,ch)
   let px = (Double(size)-cw*scale)/2, py = (Double(size)-ch*scale)/2
   let original = CIImage(cvPixelBuffer: pb)
   let cropped = original.cropped(to: CGRect(x:ox,y:oy,width:cw,height:ch)).transformed(by:CGAffineTransform(translationX:-ox,y:-oy)).transformed(by:CGAffineTransform(scaleX:scale,y:scale)).transformed(by:CGAffineTransform(translationX:px,y:py))
   let bg = CIImage(color: CIColor(red:114/255,green:114/255,blue:114/255)).cropped(to:CGRect(x:0,y:0,width:size,height:size))
   context.render(cropped.composited(over:bg),to:buffer)
   let prep = now()
   do {
    let input = try MLDictionaryFeatureProvider(dictionary:[inputName: MLFeatureValue(pixelBuffer:buffer)])
    let prediction = try model.prediction(from:input)
    let infer = now()
    let arr = prediction.featureValue(for:prediction.featureNames.first!)!.multiArrayValue!
    guard arr.dataType == .float32, arr.shape.count == 3, arr.shape[1].intValue == 56 else { throw NSError(domain:"Unsupported YOLO output",code:1) }
    let n = arr.shape[2].intValue
    let s1 = arr.strides[1].intValue, s2 = arr.strides[2].intValue
    let values = arr.dataPointer.bindMemory(to: Float.self, capacity: arr.count)
    func val(_ c: Int,_ j: Int) -> Double { Double(values[c*s1+j*s2]) }
    var candidates = [Detection]()
    for j in 0..<n where val(4,j) >= 0.25 {
     candidates.append(Detection(x:val(0,j),y:val(1,j),w:val(2,j),h:val(3,j),conf:val(4,j),hx:val(5,j),hy:val(6,j),hc:val(7,j)))
    }
    candidates.sort { $0.conf > $1.conf }
    var kept = [Detection]()
    for d in candidates { if !kept.contains(where:{ iou(d,$0)>0.45 }) { kept.append(d) } }
    let detections: [[String: Any]] = kept.map { d in
     ["confidence": d.conf, "head_confidence": d.hc,
      "head_xy": [(d.hx-px)/scale+ox,(d.hy-py)/scale+oy],
      "person_center": [(d.x-px)/scale+ox,(d.y-py)/scale+oy],
      "bbox_xyxy": [(d.x-d.w/2-px)/scale+ox,(d.y-d.h/2-py)/scale+oy,(d.x+d.w/2-px)/scale+ox,(d.y+d.h/2-py)/scale+oy]]
    }
    var row: [String: Any] = ["index":count,"width":Int(width),"height":Int(height),"detections":detections]
    let end = now(), pts = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sb))
    row["callback_to_coords_ms"] = (end-start)*1000
    row["preprocess_ms"] = (prep-start)*1000
    row["inference_ms"] = (infer-prep)*1000
    row["postprocess_ms"] = (end-infer)*1000
    row["pts_to_callback_ms"] = (start-pts)*1000
    row["pts_to_coords_ms"] = (end-pts)*1000
    row["host_start_s"] = start; row["pts_s"] = pts
    if count >= 30 {
     measured += 1
     let data = try JSONSerialization.data(withJSONObject:row)
     FileHandle.standardOutput.write(data)
     FileHandle.standardOutput.write(Data([10]))
    }
    count += 1
    if samples > 0 && measured >= samples { completed = true; done.signal() }
   } catch { fputs("Detector error: \(error)\n", stderr); failed = true; completed = true; done.signal() }
  }
 }
}
guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else { fatalError("Camera permission required") }
let device = AVCaptureDevice.DiscoverySession(deviceTypes:[.external],mediaType:.video,position:.unspecified).devices.first { $0.localizedName == "USB3.0 capture" }!
let session = AVCaptureSession()
session.beginConfiguration()
session.addInput(try AVCaptureDeviceInput(device:device))
let output = AVCaptureVideoDataOutput()
output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32BGRA]
output.alwaysDiscardsLateVideoFrames = true
let detector = Detector()
output.setSampleBufferDelegate(detector,queue:DispatchQueue(label:"capture.inference",qos:.userInteractive))
session.addOutput(output)
session.commitConfiguration()
session.startRunning()
try device.lockForConfiguration()
let formats = device.formats.filter { let d = CMVideoFormatDescriptionGetDimensions($0.formatDescription); return d.width==1920 && d.height==1080 }
device.activeFormat = formats.max { $0.videoSupportedFrameRateRanges.map(\.maxFrameRate).max()! < $1.videoSupportedFrameRateRanges.map(\.maxFrameRate).max()! }!
let range = device.activeFormat.videoSupportedFrameRateRanges.max { $0.maxFrameRate < $1.maxFrameRate }!
device.activeVideoMinFrameDuration = range.minFrameDuration
device.activeVideoMaxFrameDuration = range.minFrameDuration
device.unlockForConfiguration()
fputs("Capture ready; mode=\(device.activeFormat.formatDescription)\n",stderr)
if samples > 0 { _ = done.wait(timeout:.now()+120) } else { done.wait() }
session.stopRunning()

if detector.failed { exit(1) }
