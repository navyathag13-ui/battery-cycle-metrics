import Foundation

/// The same rules the Python pipeline applies to NASA's cells, applied to a Mac:
/// check the reading first and only score it if it passes, then call the battery
/// worn out once it holds less than 80% of its design capacity.
public enum Limits {
    /// End of life, as in battery_metrics/analysis.py (EOL_FRACTION = 0.8).
    public static let endOfLifeFraction = 0.80
    /// Same range as battery_metrics/checks.py.
    public static let temperatureRange_C = -10.0...80.0
    /// A Li-ion cell outside this is a bad reading or a damaged cell.
    public static let cellVoltageRange_mV = 2500...4500
    /// Cells in one pack should sit within this of each other.
    public static let maxCellSpread_mV = 100
    /// Pack voltage should equal the sum of its cells, within this.
    public static let packVsCellsTolerance_mV = 250
    /// A new pack can read a little above design; far above means a bad reading.
    public static let maxCapacityOverDesign = 1.10
}

public enum Verdict: String, Codable {
    case ok = "OK"
    case serviceRecommended = "SERVICE RECOMMENDED"
    case rejected = "READING REJECTED"
}

public struct HealthReport: Codable, Equatable {
    public var verdict: Verdict
    /// Full-charge capacity as a percentage of design; nil when the reading was rejected.
    public var healthPercent: Double?
    public var cycleCount: Int
    public var cyclesUsedPercent: Double?
    /// Data checks that failed. Any of these means the reading is not scored.
    public var dataProblems: [String]
    /// Real conditions worth a look on a reading that passed the data checks.
    public var warnings: [String]

    public var exitCode: Int32 {
        switch verdict {
        case .ok: return 0
        case .serviceRecommended: return 1
        case .rejected: return 2
        }
    }
}

public func dataProblems(_ r: BatteryReading) -> [String] {
    var p: [String] = []
    if r.designCapacity_mAh <= 0 { p.append("design capacity is \(r.designCapacity_mAh) mAh") }
    if r.fullChargeCapacity_mAh <= 0 { p.append("full-charge capacity is \(r.fullChargeCapacity_mAh) mAh") }
    if r.designCapacity_mAh > 0,
       Double(r.fullChargeCapacity_mAh) > Double(r.designCapacity_mAh) * Limits.maxCapacityOverDesign {
        p.append("full-charge capacity \(r.fullChargeCapacity_mAh) mAh is over 110% of design \(r.designCapacity_mAh) mAh")
    }
    if r.cycleCount < 0 { p.append("cycle count is negative") }
    if !Limits.temperatureRange_C.contains(r.temperature_C) {
        p.append(String(format: "temperature out of range (%.1f C)", r.temperature_C))
    }
    if r.cellVoltages_mV.isEmpty {
        p.append("no cell voltages")
    } else {
        let bad = r.cellVoltages_mV.filter { !Limits.cellVoltageRange_mV.contains($0) }
        if !bad.isEmpty { p.append("cell voltage out of range (\(bad.map(String.init).joined(separator: ", ")) mV)") }
        let sum = r.cellVoltages_mV.reduce(0, +)
        if abs(sum - r.packVoltage_mV) > Limits.packVsCellsTolerance_mV {
            p.append("pack voltage \(r.packVoltage_mV) mV does not match its cells (sum \(sum) mV)")
        }
    }
    return p
}

public func warnings(_ r: BatteryReading) -> [String] {
    var w: [String] = []
    if r.permanentFailureStatus != 0 {
        w.append("battery reports a permanent failure (status \(r.permanentFailureStatus))")
    }
    if let spread = r.cellVoltages_mV.max().flatMap({ hi in r.cellVoltages_mV.min().map { hi - $0 } }),
       spread > Limits.maxCellSpread_mV {
        w.append("cells out of balance by \(spread) mV")
    }
    if let rated = r.designCycleCount, rated > 0, r.cycleCount >= rated {
        w.append("past its rated \(rated) cycles")
    }
    return w
}

public func evaluate(_ r: BatteryReading) -> HealthReport {
    let problems = dataProblems(r)
    let cyclesUsed = r.designCycleCount.flatMap { $0 > 0 ? 100.0 * Double(r.cycleCount) / Double($0) : nil }
    if !problems.isEmpty {
        return HealthReport(verdict: .rejected, healthPercent: nil, cycleCount: r.cycleCount,
                            cyclesUsedPercent: cyclesUsed, dataProblems: problems, warnings: [])
    }
    let health = 100.0 * Double(r.fullChargeCapacity_mAh) / Double(r.designCapacity_mAh)
    var w = warnings(r)
    if health < Limits.endOfLifeFraction * 100 {
        w.insert(String(format: "holds %.1f%% of design capacity, below the 80%% end-of-life line", health), at: 0)
    }
    let verdict: Verdict = w.isEmpty ? .ok : .serviceRecommended
    return HealthReport(verdict: verdict, healthPercent: (health * 10).rounded() / 10, cycleCount: r.cycleCount,
                        cyclesUsedPercent: cyclesUsed.map { ($0 * 10).rounded() / 10 }, dataProblems: [], warnings: w)
}

/// Keys kept when saving a reading as a test fixture. Everything else (serial
/// numbers, manufacture data, adapter IDs) is dropped.
public let fixtureKeys: Set<String> = [
    "DesignCapacity", "AppleRawMaxCapacity", "MaxCapacity", "NominalChargeCapacity", "CycleCount",
    "DesignCycleCount9C", "Voltage", "Temperature", "Amperage", "IsCharging", "ExternalConnected",
    "FullyCharged", "PermanentFailureStatus", "CurrentCapacity", "AppleRawCurrentCapacity",
]

public func sanitizedEntry(_ d: [String: Any]) -> [String: Any] {
    var out = d.filter { fixtureKeys.contains($0.key) }
    if let bd = d["BatteryData"] as? [String: Any], let cells = bd["CellVoltage"] {
        out["BatteryData"] = ["CellVoltage": cells]
    }
    return out
}
