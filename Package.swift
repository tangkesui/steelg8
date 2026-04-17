// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "steelg8",
    platforms: [.macOS(.v14)],
    products: [
        .executable(
            name: "steelg8",
            targets: ["steelg8"]
        )
    ],
    targets: [
        .executableTarget(
            name: "steelg8",
            path: "Sources/steelg8"
        )
    ]
)
