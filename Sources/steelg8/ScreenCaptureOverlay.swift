import Cocoa

class ScreenCaptureOverlay {
    var onCapture: ((CGImage) -> Void)?
    var onCancel: (() -> Void)?

    func show() {
        let tmpPath = NSTemporaryDirectory() + "ocr_capture_\(ProcessInfo.processInfo.processIdentifier).png"

        // Use macOS built-in screencapture (no permissions needed)
        // -i: interactive selection, -s: selection mode, -x: no sound
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        task.arguments = ["-i", "-x", tmpPath]

        task.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                if process.terminationStatus == 0,
                   FileManager.default.fileExists(atPath: tmpPath),
                   let image = NSImage(contentsOfFile: tmpPath),
                   let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) {
                    self?.onCapture?(cgImage)
                } else {
                    self?.onCancel?()
                }
                // Cleanup
                try? FileManager.default.removeItem(atPath: tmpPath)
            }
        }

        do {
            try task.run()
        } catch {
            NSLog("steelg8: screencapture 启动失败: \(error)")
        }
    }
}
