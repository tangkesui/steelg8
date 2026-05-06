import SwiftUI

/// 通用占位子页。Phase 12 Track A 未完成页面先用这个让导航走通。
struct PlaceholderPage: View {
    let title: String
    let phaseHint: String

    var body: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "hammer")
                .font(.system(size: 32))
                .foregroundStyle(.secondary)
            Text(title)
                .font(.title3)
            Text(phaseHint)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
