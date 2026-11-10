// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "BatteryHealth",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "battery-health", targets: ["battery-health"]),
        .library(name: "BatteryHealthCore", targets: ["BatteryHealthCore"]),
    ],
    targets: [
        .target(name: "BatteryHealthCore"),
        .executableTarget(name: "battery-health", dependencies: ["BatteryHealthCore"]),
        .testTarget(
            name: "BatteryHealthCoreTests",
            dependencies: ["BatteryHealthCore"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
