import Cocoa
import Carbon

/// 轻量多快捷键管理器。外部按 id 注册 handler，按下时按 id 分派。
///
/// 内部用 Carbon RegisterEventHotKey（不需要 Accessibility 权限）。
@MainActor
final class HotkeyManager {
    static let shared = HotkeyManager()

    struct Binding {
        let keyCode: UInt32       // kVK_ANSI_X
        let modifiers: UInt32     // Carbon bitmask: cmdKey | shiftKey | ...
    }

    /// 默认回调：兼容旧写法 `onHotkey = { ... }` 指向 capture-ocr
    var onHotkey: (() -> Void)? {
        get { handlers["capture-ocr"] }
        set { handlers["capture-ocr"] = newValue }
    }

    private var handlers: [String: () -> Void] = [:]
    private var refs: [String: EventHotKeyRef] = [:]
    private var idToKey: [UInt32: String] = [:]
    private var nextID: UInt32 = 1
    private var installed = false

    /// 注册或替换一个 id 对应的快捷键。id 与 HotkeyRegistry 的字符串 id 一致。
    func register(id: String, binding: Binding, handler: @escaping () -> Void) {
        installHandlerIfNeeded()
        handlers[id] = handler

        // 先释放旧的
        if let existing = refs.removeValue(forKey: id) {
            UnregisterEventHotKey(existing)
        }

        let hotkeyID = EventHotKeyID(
            signature: OSType(0x53454547),   // "STEE"（任意 4 字节签名即可）
            id: nextID
        )
        idToKey[nextID] = id
        nextID += 1

        var ref: EventHotKeyRef?
        let status = RegisterEventHotKey(
            binding.keyCode,
            binding.modifiers,
            hotkeyID,
            GetApplicationEventTarget(),
            0,
            &ref
        )
        if status == noErr, let ref {
            refs[id] = ref
            NSLog("steelg8: ✅ 快捷键 \(id) 已注册")
        } else {
            NSLog("steelg8: ⚠️ 快捷键 \(id) 注册失败，status=\(status)")
        }
    }

    /// 兼容旧 API
    func start() {
        register(
            id: "capture-ocr",
            binding: .init(keyCode: UInt32(kVK_ANSI_D), modifiers: UInt32(cmdKey | shiftKey)),
            handler: { [weak self] in self?.handlers["capture-ocr"]?() }
        )
    }

    func stop() {
        for (_, ref) in refs {
            UnregisterEventHotKey(ref)
        }
        refs.removeAll()
    }

    // MARK: - private

    private func installHandlerIfNeeded() {
        guard !installed else { return }
        installed = true

        var eventType = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )
        let handler: EventHandlerUPP = { _, event, _ -> OSStatus in
            var id = EventHotKeyID()
            GetEventParameter(
                event,
                EventParamName(kEventParamDirectObject),
                EventParamType(typeEventHotKeyID),
                nil,
                MemoryLayout<EventHotKeyID>.size,
                nil,
                &id
            )
            DispatchQueue.main.async {
                if let key = HotkeyManager.shared.idToKey[id.id] {
                    HotkeyManager.shared.handlers[key]?()
                }
            }
            return noErr
        }

        InstallEventHandler(
            GetApplicationEventTarget(),
            handler,
            1,
            &eventType,
            nil,
            nil
        )
    }
}
