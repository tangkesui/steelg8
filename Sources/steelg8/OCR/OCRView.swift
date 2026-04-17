import SwiftUI
import UniformTypeIdentifiers

struct OCRView: View {
    @StateObject private var engine = OCREngine()
    @State private var selectedImage: NSImage?
    @State private var isDragOver = false

    var body: some View {
        HSplitView {
            // 左侧：图片区域
            VStack(spacing: 12) {
                Text("图片输入").font(.headline)

                ZStack {
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(
                            isDragOver ? Color.accentColor : Color.secondary.opacity(0.3),
                            style: StrokeStyle(lineWidth: 2, dash: [8])
                        )
                        .background(
                            RoundedRectangle(cornerRadius: 12)
                                .fill(isDragOver ? Color.accentColor.opacity(0.05) : Color.clear)
                        )

                    if let image = selectedImage {
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .padding(8)
                    } else {
                        VStack(spacing: 8) {
                            Image(systemName: "doc.text.image")
                                .font(.system(size: 40))
                                .foregroundColor(.secondary)
                            Text("拖拽图片到此处")
                                .foregroundColor(.secondary)
                            Text("或点击下方按钮选择文件")
                                .font(.caption)
                                .foregroundColor(.secondary.opacity(0.7))
                        }
                    }
                }
                .onDrop(of: [.fileURL], isTargeted: $isDragOver) { providers in
                    handleDrop(providers: providers)
                }

                HStack {
                    Button("选择图片") {
                        selectImage()
                    }

                    if selectedImage != nil {
                        Button("识别文字") {
                            if let image = selectedImage,
                               let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) {
                                engine.recognizeText(from: cgImage)
                            }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(engine.isProcessing)

                        Button("清除") {
                            selectedImage = nil
                            engine.recognizedText = ""
                            engine.errorMessage = nil
                        }
                    }
                }
            }
            .padding()
            .frame(minWidth: 300)

            // 右侧：识别结果
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("识别结果").font(.headline)
                    Spacer()
                    if !engine.recognizedText.isEmpty {
                        Button("复制") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(engine.recognizedText, forType: .string)
                        }
                    }
                }

                if engine.isProcessing {
                    HStack {
                        ProgressView().controlSize(.small)
                        Text("正在识别...")
                    }
                }

                if let error = engine.errorMessage {
                    Text(error)
                        .foregroundColor(.red)
                        .font(.caption)
                }

                TextEditor(text: $engine.recognizedText)
                    .font(.body)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .overlay(alignment: .topLeading) {
                        if engine.recognizedText.isEmpty {
                            Text("识别结果将显示在这里")
                                .foregroundColor(.secondary)
                                .padding(8)
                                .allowsHitTesting(false)
                        }
                    }
            }
            .padding()
            .frame(minWidth: 300)
        }
    }

    private func selectImage() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg, .tiff, .bmp, .pdf]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false

        if panel.runModal() == .OK, let url = panel.url {
            loadImage(from: url)
        }
    }

    private func loadImage(from url: URL) {
        if let image = NSImage(contentsOf: url) {
            selectedImage = image
            engine.recognizeText(from: url)
        }
    }

    private func handleDrop(providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }

        provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { data, _ in
            guard let data = data as? Data,
                  let url = URL(dataRepresentation: data, relativeTo: nil) else { return }

            DispatchQueue.main.async {
                loadImage(from: url)
            }
        }
        return true
    }
}
