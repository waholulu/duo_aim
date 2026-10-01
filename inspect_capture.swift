import AVFoundation
import Foundation
let devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.external], mediaType: .video, position: .unspecified).devices
for d in devices {
 print("DEVICE \(d.localizedName) ID \(d.uniqueID)")
 for f in d.formats { let s = CMVideoFormatDescriptionGetDimensions(f.formatDescription); print("  \(s.width)x\(s.height) \(f.videoSupportedFrameRateRanges)") }
}
print("AUTH \(AVCaptureDevice.authorizationStatus(for: .video).rawValue)")
