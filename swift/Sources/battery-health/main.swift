import BatteryHealthCore
import Foundation

let usage = """
usage: battery-health [--file reading.plist] [--json] [--save-fixture out.plist]

Reads this Mac's battery from `ioreg -rn AppleSmartBattery -a` (read-only),
checks the reading, then reports capacity against design.
  --file PATH          read a saved ioreg plist instead of this Mac
  --json               print the report as JSON
  --save-fixture PATH  save the reading with serials and IDs stripped, for tests
Exit code: 0 OK, 1 service recommended, 2 reading rejected or unreadable.
"""

var file: String?
var json = false
var fixtureOut: String?
var args = CommandLine.arguments.dropFirst().makeIterator()
while let a = args.next() {
    switch a {
    case "--file": file = args.next()
    case "--json": json = true
    case "--save-fixture": fixtureOut = args.next()
    case "-h", "--help": print(usage); exit(0)
    default: FileHandle.standardError.write("unknown argument \(a)\n\(usage)\n".data(using: .utf8)!); exit(2)
    }
}

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write("battery-health: \(msg)\n".data(using: .utf8)!)
    exit(2)
}

func readIoreg() -> Data {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/sbin/ioreg")
    p.arguments = ["-rn", "AppleSmartBattery", "-a"]
    let pipe = Pipe()
    p.standardOutput = pipe
    do { try p.run() } catch { fail("could not run ioreg: \(error)") }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return data
}

let data: Data
if let file {
    guard let d = FileManager.default.contents(atPath: file) else { fail("cannot read \(file)") }
    data = d
} else {
    data = readIoreg()
}

if let fixtureOut {
    guard let list = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [[String: Any]],
          let entry = list.first else { fail(ReadingError.noBatteryEntry.description) }
    let out = try PropertyListSerialization.data(fromPropertyList: [sanitizedEntry(entry)], format: .xml, options: 0)
    try out.write(to: URL(fileURLWithPath: fixtureOut))
    print("saved \(fixtureOut) (\(sanitizedEntry(entry).count) keys kept, serials and IDs dropped)")
}

let reading: BatteryReading
do { reading = try BatteryReading.parse(ioregPlist: data) } catch { fail("\(error)") }
let report = evaluate(reading)

if json {
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted, .sortedKeys]
    print(String(data: try enc.encode(report), encoding: .utf8)!)
} else {
    print("Battery: \(report.verdict.rawValue)")
    if let h = report.healthPercent {
        print(String(format: "  capacity  %d of %d mAh design (%.1f%%)", reading.fullChargeCapacity_mAh,
                     reading.designCapacity_mAh, h))
    }
    let rated = reading.designCycleCount.map { " of \($0) rated" } ?? ""
    print("  cycles    \(reading.cycleCount)\(rated)")
    print(String(format: "  pack      %.2f V, cells %@ mV, %.1f C, %d mA%@", Double(reading.packVoltage_mV) / 1000,
                 reading.cellVoltages_mV.map(String.init).joined(separator: "/"), reading.temperature_C,
                 reading.current_mA, reading.isCharging ? ", charging" : ""))
    for p in report.dataProblems { print("  data check failed: \(p)") }
    for w in report.warnings { print("  warning: \(w)") }
}
exit(report.exitCode)
