import AVFoundation
import AppKit
import Foundation
if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
 let s = DispatchSemaphore(value: 0)
 AVCaptureDevice.requestAccess(for: .video) { ok in print("Camera access: \(ok)"); s.signal() }
 s.wait()
}
guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else { fatalError("Camera access denied") }
let device = AVCaptureDevice.DiscoverySession(deviceTypes: [.external], mediaType: .video, position: .unspecified).devices.first { $0.localizedName == "USB3.0 capture" }!
let session = AVCaptureSession()
session.beginConfiguration()
session.addInput(try AVCaptureDeviceInput(device: device))
try device.lockForConfiguration()
let formats = device.formats.filter { let d = CMVideoFormatDescriptionGetDimensions($0.formatDescription); return d.width == 1280 && d.height == 720 }
device.activeFormat = formats.max { a,b in a.videoSupportedFrameRateRanges.map(\.maxFrameRate).max()! < b.videoSupportedFrameRateRanges.map(\.maxFrameRate).max()! }!
let fps = device.activeFormat.videoSupportedFrameRateRanges.map(\.maxFrameRate).max()!
device.activeVideoMinFrameDuration = device.activeFormat.videoSupportedFrameRateRanges.max { $0.maxFrameRate < $1.maxFrameRate }!.minFrameDuration
device.activeVideoMaxFrameDuration = device.activeVideoMinFrameDuration
device.unlockForConfiguration()
let output = AVCaptureVideoDataOutput()
output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
output.alwaysDiscardsLateVideoFrames = true
let done = DispatchSemaphore(value: 0)
class Delegate: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
 var count = 0
 var first = 0.0
 let ctx = CIContext()
 func captureOutput(_ output: AVCaptureOutput, didOutput sb: CMSampleBuffer, from connection: AVCaptureConnection) {
  count += 1
  let now = CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock()))
  if count == 1 { first = now }
  if count == 30, let pb = CMSampleBufferGetImageBuffer(sb) {
   let ci = CIImage(cvPixelBuffer: pb)
   let cg = ctx.createCGImage(ci, from: ci.extent)!
   let rep = NSBitmapImageRep(cgImage: cg)
   try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "results/capture.png"))
  }
  if count == 300 { print("frames=\(count) observed_fps=\(299/(now-first)) pts_age_ms=\((now-CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sb)))*1000)"); done.signal() }
 }
}
let delegate = Delegate()
output.setSampleBufferDelegate(delegate, queue: DispatchQueue(label: "capture", qos: .userInteractive))
session.addOutput(output)
session.commitConfiguration()
print("starting \(device.localizedName) fps \(fps)")
session.startRunning()
let result = done.wait(timeout: .now() + 20)
session.stopRunning()
print("done \(result)")
