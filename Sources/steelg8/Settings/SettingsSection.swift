import SwiftUI

/// SwiftUI 设置容器左侧的分组。Phase 12.2 引入；2026-05-08 模型组重构：
/// - 供应商与模型 → 供应商管理（瘦身）
/// - 模型画像与定价 → 并入模型管理
/// - 新增模型管理（默认模型 + 默认选择 + 排序）
/// - 新增路由设置（默认供应商 + fallback 顺序，占位实现）
enum SettingsSection: String, Identifiable, CaseIterable, Hashable {
    case general
    case topbar
    case soul
    case userMemory
    case providersAdmin
    case modelAdmin
    case router
    case rag
    case runtimeCost
    case runtimeHealth
    case runtimeIndex
    case runtimeRAG
    case runtimeLog

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general:         return "基础"
        case .topbar:          return "顶栏显示"
        case .soul:            return "Soul（L1 人格）"
        case .userMemory:      return "User Memory（L2 画像）"
        case .providersAdmin:  return "供应商管理"
        case .modelAdmin:      return "模型管理"
        case .router:          return "路由设置"
        case .rag:             return "RAG 管理"
        case .runtimeCost:     return "费用"
        case .runtimeHealth:   return "体检"
        case .runtimeIndex:    return "索引"
        case .runtimeRAG:      return "RAG"
        case .runtimeLog:      return "日志"
        }
    }

    var systemImage: String {
        switch self {
        case .general:         return "gearshape"
        case .topbar:          return "rectangle.topthird.inset.filled"
        case .soul:            return "person.crop.circle.badge.questionmark"
        case .userMemory:      return "person.text.rectangle"
        case .providersAdmin:  return "server.rack"
        case .modelAdmin:      return "cube.box"
        case .router:          return "arrow.triangle.branch"
        case .rag:             return "doc.text.magnifyingglass"
        case .runtimeCost:     return "dollarsign.circle"
        case .runtimeHealth:   return "stethoscope"
        case .runtimeIndex:    return "tray.full"
        case .runtimeRAG:      return "doc.text.magnifyingglass"
        case .runtimeLog:      return "list.bullet.rectangle"
        }
    }

    /// 左侧三段：通用 / 模型 / 运行状态
    enum Group: String, CaseIterable, Identifiable {
        case common, model, runtime

        var id: String { rawValue }

        var title: String {
            switch self {
            case .common:  return "通用"
            case .model:   return "模型"
            case .runtime: return "运行状态"
            }
        }

        var sections: [SettingsSection] {
            switch self {
            case .common:  return [.general, .topbar, .soul, .userMemory]
            case .model:   return [.providersAdmin, .modelAdmin, .router, .rag]
            case .runtime: return [.runtimeCost, .runtimeHealth, .runtimeIndex, .runtimeRAG, .runtimeLog]
            }
        }
    }
}
