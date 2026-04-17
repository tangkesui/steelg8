@preconcurrency import Vision
import AppKit
import Foundation

@MainActor
class SilentOCREngine {
    func recognizeAndCopy(from cgImage: CGImage) {
        let request = VNRecognizeTextRequest { [weak self] request, error in
            DispatchQueue.main.async {
                if let error = error {
                    self?.showNotification("OCR 失败: \(error.localizedDescription)")
                    return
                }

                guard let observations = request.results as? [VNRecognizedTextObservation] else {
                    self?.showNotification("未识别到文字")
                    return
                }

                let text = observations.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")

                if text.isEmpty {
                    self?.showNotification("未识别到文字")
                } else {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(text, forType: .string)
                    self?.showNotification("已复制 \(text.count) 个字符")
                }
            }
        }

        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
        request.usesLanguageCorrection = true

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
            do {
                try handler.perform([request])
            } catch {
                DispatchQueue.main.async {
                    self?.showNotification("OCR 失败: \(error.localizedDescription)")
                }
            }
        }
    }

    private func showNotification(_ body: String) {
        let statusItem = AppController.shared.statusItem
        if let button = statusItem?.button {
            let original = button.image
            button.title = "  \(body)"
            button.image = nil

            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                button.title = ""
                button.image = original
            }
        }
    }
}
