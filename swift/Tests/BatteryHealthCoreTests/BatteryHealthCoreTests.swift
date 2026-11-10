import Foundation
import XCTest

@testable import BatteryHealthCore

/// Fixtures: macbook_real.plist is a real reading from my MacBook (saved with
/// `battery-health --save-fixture`, so serials and IDs are stripped). The others
/// are that reading with one fault planted, so each test knows the right answer.
final class BatteryHealthCoreTests: XCTestCase {

    func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: nil, subdirectory: "Fixtures"))
        return try Data(contentsOf: url)
    }

    func reading(_ name: String) throws -> BatteryReading {
        try BatteryReading.parse(ioregPlist: try fixture(name))
    }

    /// The real reading as a dictionary, for tests that change one key in code.
    func realEntry() throws -> [String: Any] {
        let list = try PropertyListSerialization.propertyList(from: try fixture("macbook_real.plist"), format: nil)
        return try XCTUnwrap((list as? [[String: Any]])?.first)
    }

    // MARK: parsing

    func testRealReadingParses() throws {
        let r = try reading("macbook_real.plist")
        XCTAssertEqual(r.designCapacity_mAh, 4629)
        XCTAssertEqual(r.fullChargeCapacity_mAh, 4389)
        XCTAssertEqual(r.cycleCount, 110)
        XCTAssertEqual(r.designCycleCount, 1000)
        XCTAssertEqual(r.cellVoltages_mV, [3976, 3975, 3977])
        XCTAssertEqual(r.packVoltage_mV, 11928)
        XCTAssertEqual(r.temperature_C, 30.25, accuracy: 0.001)
        XCTAssertTrue(r.isCharging)
        XCTAssertTrue(r.externalPower)
    }

    func testNegativeCurrentPrintedAsUnsigned64IsReadAsSigned() throws {
        // ioreg prints -189 mA as 18446744073709551427
        let r = try reading("macbook_discharging.plist")
        XCTAssertEqual(r.current_mA, -189)
        XCTAssertFalse(r.isCharging)
    }

    func testMissingDesignCapacityIsAnError() {
        XCTAssertThrowsError(try reading("missing_design_capacity.plist")) { error in
            XCTAssertEqual(error as? ReadingError, .missingKey("DesignCapacity"))
        }
    }

    func testNotAPlistIsAnError() {
        XCTAssertThrowsError(try reading("not_a_plist.txt")) { error in
            XCTAssertEqual(error as? ReadingError, .notAPropertyList)
        }
    }

    func testEmptyIoregOutputMeansNoBattery() throws {
        let empty = try PropertyListSerialization.data(fromPropertyList: [Any](), format: .xml, options: 0)
        XCTAssertThrowsError(try BatteryReading.parse(ioregPlist: empty)) { error in
            XCTAssertEqual(error as? ReadingError, .noBatteryEntry)
        }
    }

    func testWrongTypeIsAnError() throws {
        var e = try realEntry()
        e["CycleCount"] = "110"
        XCTAssertThrowsError(try BatteryReading(entry: e)) { error in
            XCTAssertEqual(error as? ReadingError, .wrongType("CycleCount"))
        }
        e = try realEntry()
        e["IsCharging"] = 1  // a number where a boolean belongs
        XCTAssertThrowsError(try BatteryReading(entry: e)) { error in
            XCTAssertEqual(error as? ReadingError, .wrongType("IsCharging"))
        }
    }

    func testIntelStyleReadingWithoutRawMaxUsesMaxCapacity() throws {
        var e = try realEntry()
        e.removeValue(forKey: "AppleRawMaxCapacity")
        e["MaxCapacity"] = 4100
        XCTAssertEqual(try BatteryReading(entry: e).fullChargeCapacity_mAh, 4100)
    }

    // MARK: verdicts

    func testRealReadingIsHealthy() throws {
        let report = evaluate(try reading("macbook_real.plist"))
        XCTAssertEqual(report.verdict, .ok)
        XCTAssertEqual(report.healthPercent, 94.8)
        XCTAssertEqual(report.cyclesUsedPercent, 11.0)
        XCTAssertEqual(report.dataProblems, [])
        XCTAssertEqual(report.warnings, [])
        XCTAssertEqual(report.exitCode, 0)
    }

    func testWornPackIsBelowEndOfLife() throws {
        let report = evaluate(try reading("worn_pack.plist"))
        XCTAssertEqual(report.verdict, .serviceRecommended)
        XCTAssertEqual(report.healthPercent, 76.7)
        XCTAssertEqual(report.warnings.count, 2)
        guard report.warnings.count == 2 else { return }
        XCTAssertTrue(report.warnings[0].contains("below the 80% end-of-life line"))
        XCTAssertTrue(report.warnings[1].contains("past its rated 1000 cycles"))
        XCTAssertEqual(report.exitCode, 1)
    }

    func testEndOfLifeBoundaryIsEightyPercent() throws {
        var r = try reading("macbook_real.plist")
        r.designCapacity_mAh = 5000
        r.fullChargeCapacity_mAh = 4000  // exactly 80%: not yet end of life
        XCTAssertEqual(evaluate(r).verdict, .ok)
        r.fullChargeCapacity_mAh = 3999
        XCTAssertEqual(evaluate(r).verdict, .serviceRecommended)
    }

    func testImpossibleTemperatureRejectsTheReading() throws {
        let report = evaluate(try reading("bad_temperature.plist"))
        XCTAssertEqual(report.verdict, .rejected)
        XCTAssertNil(report.healthPercent, "a rejected reading must not be scored")
        XCTAssertEqual(report.dataProblems, ["temperature out of range (310.0 C)"])
        XCTAssertEqual(report.exitCode, 2)
    }

    func testDeadCellRejectsTheReading() throws {
        let report = evaluate(try reading("dead_cell.plist"))
        XCTAssertEqual(report.verdict, .rejected)
        XCTAssertEqual(report.dataProblems.count, 2)
        guard report.dataProblems.count == 2 else { return }
        XCTAssertTrue(report.dataProblems[0].hasPrefix("cell voltage out of range"))
        XCTAssertTrue(report.dataProblems[1].hasPrefix("pack voltage 11928 mV does not match"))
    }

    func testEachDataCheckCatchesItsPlantedFault() throws {
        let base = try reading("macbook_real.plist")
        let cases: [(String, (inout BatteryReading) -> Void)] = [
            ("design capacity is 0 mAh", { $0.designCapacity_mAh = 0 }),
            ("full-charge capacity is -5 mAh", { $0.fullChargeCapacity_mAh = -5 }),
            ("over 110% of design", { $0.fullChargeCapacity_mAh = 5200 }),
            ("cycle count is negative", { $0.cycleCount = -1 }),
            ("temperature out of range (-20.0 C)", { $0.temperature_C = -20 }),
            ("no cell voltages", { $0.cellVoltages_mV = [] }),
            ("cell voltage out of range (4700 mV)", { $0.cellVoltages_mV = [3976, 3975, 4700]; $0.packVoltage_mV = 12651 }),
            ("does not match its cells", { $0.packVoltage_mV = 9000 }),
        ]
        for (expected, plant) in cases {
            var r = base
            plant(&r)
            let problems = dataProblems(r)
            XCTAssertEqual(problems.count, 1, "\(expected): got \(problems)")
            XCTAssertTrue(problems.first?.contains(expected) ?? false, "\(expected): got \(problems)")
            XCTAssertEqual(evaluate(r).verdict, .rejected)
        }
    }

    func testWarningsDoNotRejectTheReading() throws {
        var r = try reading("macbook_real.plist")
        r.cellVoltages_mV = [3800, 3975, 3977]
        r.packVoltage_mV = 11752
        r.permanentFailureStatus = 4
        let report = evaluate(r)
        XCTAssertEqual(report.verdict, .serviceRecommended)
        XCTAssertNotNil(report.healthPercent)
        XCTAssertEqual(report.warnings, ["battery reports a permanent failure (status 4)", "cells out of balance by 177 mV"])
    }

    // MARK: fixtures stay clean

    func testSanitizerDropsSerialsAndIdentifiers() throws {
        var e = try realEntry()
        e["Serial"] = "F8Y0000000000000"
        e["ManufacturerData"] = Data([1, 2, 3])
        e["AdapterDetails"] = ["AdapterID": 1234]
        e["BatteryData"] = ["CellVoltage": [3976, 3975, 3977], "Serial": "F8Y0000000000000", "Qmax": [1, 2, 3]]
        let clean = sanitizedEntry(e)
        XCTAssertNil(clean["Serial"])
        XCTAssertNil(clean["ManufacturerData"])
        XCTAssertNil(clean["AdapterDetails"])
        let bd = try XCTUnwrap(clean["BatteryData"] as? [String: Any])
        XCTAssertEqual(Array(bd.keys), ["CellVoltage"])
        XCTAssertEqual(try BatteryReading(entry: clean), try BatteryReading(entry: e))
    }

    func testCommittedFixturesHoldNoSerialNumbers() throws {
        let dir = try XCTUnwrap(Bundle.module.url(forResource: "Fixtures", withExtension: nil))
        for name in try FileManager.default.contentsOfDirectory(atPath: dir.path) {
            let text = String(decoding: try Data(contentsOf: dir.appendingPathComponent(name)), as: UTF8.self)
            XCTAssertFalse(text.contains("Serial"), "\(name) contains a serial number key")
        }
    }
}
