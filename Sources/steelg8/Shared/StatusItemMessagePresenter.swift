import AppKit
import Foundation

@MainActor
final class StatusItemMessagePresenter {
    private var restoreTask: Task<Void, Never>?

    func present(_ message: String, on statusItem: NSStatusItem?, duration: TimeInterval = 2.5) {
        guard let button = statusItem?.button else {
            return
        }

        restoreTask?.cancel()

        let originalImage = button.image
        let originalTitle = button.title

        button.image = nil
        button.title = "  \(message)"

        restoreTask = Task {
            let delay = UInt64(max(duration, 0) * 1_000_000_000)
            try? await Task.sleep(nanoseconds: delay)
            guard !Task.isCancelled else { return }
            button.title = originalTitle
            button.image = originalImage
        }
    }
}
