import Cocoa
import Carbon

@MainActor
class HotkeyManager {
    static let shared = HotkeyManager()
    var onHotkey: (() -> Void)?

    private var hotkeyRef: EventHotKeyRef?

    func start() {
        // Install Carbon event handler
        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))

        let handler: EventHandlerUPP = { _, event, _ -> OSStatus in
            DispatchQueue.main.async {
                HotkeyManager.shared.onHotkey?()
            }
            return noErr
        }

        InstallEventHandler(GetApplicationEventTarget(), handler, 1, &eventType, nil, nil)

        // Register Cmd+Shift+D
        // D = kVK_ANSI_D = 0x02
        let hotkeyID = EventHotKeyID(signature: OSType(0x4F435244), id: 1) // "OCRD"
        let modifiers: UInt32 = UInt32(cmdKey | shiftKey)

        let status = RegisterEventHotKey(
            UInt32(kVK_ANSI_D),
            modifiers,
            hotkeyID,
            GetApplicationEventTarget(),
            0,
            &hotkeyRef
        )

        if status == noErr {
            NSLog("steelg8: ✅ 全局快捷键 Cmd+Shift+D 已注册 (Carbon)")
        } else {
            NSLog("steelg8: ⚠️ 快捷键注册失败，错误码: \(status)")
        }
    }

    func stop() {
        if let ref = hotkeyRef {
            UnregisterEventHotKey(ref)
            hotkeyRef = nil
        }
    }
}
