import Foundation

/// One reading of a Mac's battery, taken from the AppleSmartBattery entry that
/// `ioreg -rn AppleSmartBattery -a` prints as a property list.
public struct BatteryReading: Equatable, Codable {
    public var designCapacity_mAh: Int
    /// Full-charge capacity the gauge measures today (AppleRawMaxCapacity).
    public var fullChargeCapacity_mAh: Int
    public var cycleCount: Int
    /// Cycles the pack is rated for (DesignCycleCount9C); nil if the Mac does not report it.
    public var designCycleCount: Int?
    public var packVoltage_mV: Int
    public var cellVoltages_mV: [Int]
    public var temperature_C: Double
    /// Negative while discharging.
    public var current_mA: Int
    public var isCharging: Bool
    public var externalPower: Bool
    public var permanentFailureStatus: Int
}

public enum ReadingError: Error, Equatable, CustomStringConvertible {
    case notAPropertyList
    case noBatteryEntry
    case missingKey(String)
    case wrongType(String)

    public var description: String {
        switch self {
        case .notAPropertyList: return "input is not a property list"
        case .noBatteryEntry: return "no AppleSmartBattery entry (is this a Mac with a battery?)"
        case .missingKey(let k): return "missing key \(k)"
        case .wrongType(let k): return "key \(k) has the wrong type"
        }
    }
}

extension BatteryReading {
    /// Parses `ioreg -rn AppleSmartBattery -a` output: an array holding one dictionary.
    public static func parse(ioregPlist data: Data) throws -> BatteryReading {
        guard let root = try? PropertyListSerialization.propertyList(from: data, format: nil) else {
            throw ReadingError.notAPropertyList
        }
        let entry: [String: Any]?
        if let list = root as? [[String: Any]] {
            entry = list.first
        } else {
            entry = root as? [String: Any]
        }
        guard let dict = entry else { throw ReadingError.noBatteryEntry }
        return try BatteryReading(entry: dict)
    }

    public init(entry d: [String: Any]) throws {
        designCapacity_mAh = try Self.int(d, "DesignCapacity")
        // Older Intel Macs report the measured capacity as MaxCapacity in mAh;
        // Apple silicon reports MaxCapacity as a percentage and the mAh in AppleRawMaxCapacity.
        if d["AppleRawMaxCapacity"] != nil {
            fullChargeCapacity_mAh = try Self.int(d, "AppleRawMaxCapacity")
        } else {
            fullChargeCapacity_mAh = try Self.int(d, "MaxCapacity")
        }
        cycleCount = try Self.int(d, "CycleCount")
        designCycleCount = d["DesignCycleCount9C"] == nil ? nil : try Self.int(d, "DesignCycleCount9C")
        packVoltage_mV = try Self.int(d, "Voltage")
        temperature_C = Double(try Self.int(d, "Temperature")) / 100.0
        current_mA = d["Amperage"] == nil ? 0 : try Self.int(d, "Amperage")
        isCharging = try Self.bool(d, "IsCharging")
        externalPower = try Self.bool(d, "ExternalConnected")
        permanentFailureStatus = d["PermanentFailureStatus"] == nil ? 0 : try Self.int(d, "PermanentFailureStatus")
        if let bd = d["BatteryData"] as? [String: Any], let cells = bd["CellVoltage"] {
            guard let arr = cells as? [Any] else { throw ReadingError.wrongType("BatteryData.CellVoltage") }
            cellVoltages_mV = try arr.map { v in
                guard let n = v as? NSNumber, !Self.isBool(n) else { throw ReadingError.wrongType("BatteryData.CellVoltage") }
                return n.intValue
            }
        } else {
            cellVoltages_mV = []
        }
    }

    /// ioreg prints signed values such as Amperage as unsigned 64-bit numbers
    /// (-189 mA appears as 18446744073709551427), so read the bits as signed.
    static func int(_ d: [String: Any], _ key: String) throws -> Int {
        guard let v = d[key] else { throw ReadingError.missingKey(key) }
        guard let n = v as? NSNumber, !isBool(n) else { throw ReadingError.wrongType(key) }
        return Int(Int64(bitPattern: n.uint64Value))
    }

    static func bool(_ d: [String: Any], _ key: String) throws -> Bool {
        guard let v = d[key] else { throw ReadingError.missingKey(key) }
        guard let n = v as? NSNumber, isBool(n) else { throw ReadingError.wrongType(key) }
        return n.boolValue
    }

    static func isBool(_ n: NSNumber) -> Bool {
        CFGetTypeID(n) == CFBooleanGetTypeID()
    }
}
