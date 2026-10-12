import CoreML
import CoreVideo
import Foundation

@main
struct VisionTests {
    static func main() throws {
        var checks = 0
        func check(_ condition: Bool, _ message: String) {
            precondition(condition, message)
            checks += 1
        }
        for (width, height, crop) in [(1920.0,1080.0,960.0), (1280,720,0), (720,1280,960), (1919,1079,800)] {
            let g = Geometry(width:width, height:height, crop:crop, size:640)
            for point in [[g.ox,g.oy], [width/2,height/2], [g.ox+g.cw,g.oy+g.ch]] {
                let model = g.model(point[0], point[1])
                let restored = g.source(model[0], model[1])
                check(abs(restored[0]-point[0]) < 1e-8 && abs(restored[1]-point[1]) < 1e-8, "Geometry roundtrip")
            }
        }
        for shape in [[1,55,10], [2,56,10], [1,56,0], [56,10]] {
            do { try validateTensor(shape:shape, dataType:.float32); fatalError("Accepted invalid shape") }
            catch { checks += 1 }
        }
        do { try validateTensor(shape:[1,56,10], dataType:.double); fatalError("Accepted double") }
        catch { checks += 1 }
        for (count, width, height) in [(2,640,640), (1,640,320), (1,0,0)] {
            do { try validateImageInput(count:count, width:width, height:height); fatalError("Accepted invalid image input") }
            catch { checks += 1 }
        }
        // Deliberately padded channel and item strides, outside arr.count storage span.
        let pointer = UnsafeMutablePointer<Float>.allocate(capacity:56*8)
        pointer.initialize(repeating:0, count:56*8)
        let array = try MLMultiArray(dataPointer:UnsafeMutableRawPointer(pointer), shape:[1,56,2],
            dataType:.float32, strides:[448,8,2], deallocator: { raw in
                raw.assumingMemoryBound(to:Float.self).deinitialize(count:56*8)
                raw.deallocate()
            })
        for (channel, value) in [100.0,120,40,60,0.9,100,110,0.8].enumerated() {
            pointer[channel*8] = Float(value)
            pointer[channel*8+2] = Float(value)
        }
        pointer[4*8+2] = 0.7
        let decoded = try decodePose(array)
        check(decoded.count == 1, "NMS should suppress overlapping lower-confidence detection")
        check(decoded[0].hx == 100 && decoded[0].hy == 110, "Stride decoding")
        let g = Geometry(width:1920, height:1080, crop:960, size:640)
        let a = decoded[0].mapped(g)
        check(a["head_xy"] as! [Double] == [630,225], "Shared head coordinate mapping")
        check(a["bbox_xyxy"] as! [Double] == [600,195,660,285], "Shared box coordinate mapping")
        var distant = decoded[0]; distant.x += 200
        check(nonMaximumSuppression([decoded[0], distant]).count == 2, "NMS distinct targets")
        for path in CommandLine.arguments.dropFirst() {
            let core = try VisionCore(path:path, units:"cpu")
            var pixelBuffer: CVPixelBuffer?
            let status = CVPixelBufferCreate(kCFAllocatorDefault, 1920, 1080, kCVPixelFormatType_32BGRA,
                [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &pixelBuffer)
            check(status == kCVReturnSuccess, "Allocate synthetic image")
            CVPixelBufferLockBaseAddress(pixelBuffer!, [])
            memset(CVPixelBufferGetBaseAddress(pixelBuffer!)!, 114,
                   CVPixelBufferGetBytesPerRow(pixelBuffer!)*1080)
            CVPixelBufferUnlockBaseAddress(pixelBuffer!, [])
            let result = try core.infer(pixelBuffer!, crop:960, start:hostNow())
            check(result.geometry.cropXYWH == [480,60,960,960], "Actual model geometry")
            let rows = result.detections.map { $0.mapped(result.geometry) }
            check(JSONSerialization.isValidJSONObject(rows), "Serializable shared coordinates")
            print("Synthetic model inference passed: \(path), size=\(core.size)")
        }
        print("Vision tests passed: \(checks)")
    }
}
