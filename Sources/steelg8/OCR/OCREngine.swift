@preconcurrency import Vision
import AppKit
import Foundation

@MainActor
class OCREngine: ObservableObject {
    @Published var recognizedText: String = ""
    @Published var isProcessing: Bool = false
    @Published var errorMessage: String?

    func recognizeText(from url: URL) {
        guard let nsImage = NSImage(contentsOf: url),
              let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            errorMessage = "无法加载图片"
            return
        }
        recognizeText(from: cgImage)
    }

    func recognizeText(from cgImage: CGImage) {
        isProcessing = true
        recognizedText = ""
        errorMessage = nil

        let request = VNRecognizeTextRequest { [weak self] request, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.isProcessing = false

                if let error = error {
                    self.errorMessage = "识别失败: \(error.localizedDescription)"
                    return
                }

                guard let observations = request.results as? [VNRecognizedTextObservation] else {
                    self.errorMessage = "无识别结果"
                    return
                }

                let text = observations.compactMap { observation in
                    observation.topCandidates(1).first?.string
                }.joined(separator: "\n")

                self.recognizedText = text.isEmpty ? "未识别到文字" : text
            }
        }

        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
        request.usesLanguageCorrection = true

        DispatchQueue.global(qos: .userInitiated).async {
            let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
            do {
                try handler.perform([request])
            } catch {
                DispatchQueue.main.async {
                    self.isProcessing = false
                    self.errorMessage = "处理失败: \(error.localizedDescription)"
                }
            }
        }
    }
}
