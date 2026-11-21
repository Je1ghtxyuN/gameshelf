import Foundation
@main struct DirectNASBindingTests {
 static func main() throws {
  let fm = FileManager.default
  let root = URL(fileURLWithPath: NSTemporaryDirectory()).resolvingSymlinksInPath().appendingPathComponent(UUID().uuidString)
  defer { try? fm.removeItem(at: root) }
  let app = root.appendingPathComponent("Fixture.app")
  let executable = app.appendingPathComponent("Contents/MacOS/Fixture")
  let plist = app.appendingPathComponent("Contents/Info.plist")
  try fm.createDirectory(at: executable.deletingLastPathComponent(), withIntermediateDirectories: true)
  try Data([0xcf, 0xfa, 0xed, 0xfe]).write(to: executable)
  try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: executable.path)
  try PropertyListSerialization.data(fromPropertyList: ["CFBundleExecutable": "Fixture"], format: .xml, options: 0).write(to: plist)
  let nested = Candidate(path: "Fixture.app", backend: .native, engine: "fixture")
  let dot = Candidate(path: ".", backend: .native, engine: "fixture")
  let selected = try DirectNASBinding.inspectBundle(folder: root, entry: nested)
  let atRoot = try DirectNASBinding.inspectBundle(folder: app, entry: dot)
  guard selected.app.path == app.path, selected.fingerprints == atRoot.fingerprints else { fatalError("Native entry mapping") }
  func reject(_ fn: () throws -> Void) throws {
   do { try fn() } catch { return }
   throw LibraryError("Expected direct NAS refusal")
  }
  let owner = try AccountLibraryIdentity(serverID: "fixture", accountID: "owner")
  let key = DirectNASBinding.Key(identity: owner, canonicalID: "game", variantID: "variant", entryPath: nested.path, entryBackend: nested.backend)
  try reject { _ = try DirectNASBinding.capture(key: key, folder: root, entry: nested) }
  try reject { _ = try DirectNASBinding.inspectBundle(folder: root, entry: Candidate(path: "../Fixture.app", backend: .native, engine: "fixture")) }
  try reject { _ = try DirectNASBinding.inspectBundle(folder: root, entry: Candidate(path: "Play.exe", backend: .wine, engine: "fixture")) }
  let outside = root.appendingPathComponent("Other")
  try fm.moveItem(at: executable, to: outside)
  try fm.createSymbolicLink(at: executable, withDestinationURL: outside)
  try reject { _ = try DirectNASBinding.inspectBundle(folder: root, entry: nested) }
  let rootID = String(repeating: "a", count: 32)
  let source = AccountLibraryNASSource(rootID: rootID, relative: "Fixture.app")
  let folder = try DirectNASRoot.confinedURL(root: root, relative: source.relative, type: .typeDirectory)
  guard folder.path == app.path else { fatalError("NAS root relative mapping") }
  let rootFolder = try DirectNASRoot.confinedURL(root: root, relative: ".", type: .typeDirectory)
  guard rootFolder.path == root.path else { fatalError("NAS source at root") }
  try reject { _ = try DirectNASRoot.confinedURL(root: root, relative: "../Other", type: .typeRegular) }
  try reject { _ = try DirectNASRoot.confinedURL(root: root, relative: "Fixture.app/Contents/MacOS/Fixture", type: .typeRegular) }
  try reject { try DirectNASRoot.bind(rootID: rootID, folder: root, profile: root) }
  try reject { _ = try DirectNASRoot.folder(rootID: "../escape", profile: root) }
  let exe = root.appendingPathComponent("Play.exe")
  let windows = Candidate(path: "Play.exe", backend: .wine, engine: "fixture")
  var pe = Data(repeating: 0, count: 88)
  pe[0] = 0x4d; pe[1] = 0x5a; pe[60] = 64
  pe[64] = 0x50; pe[65] = 0x45; pe[68] = 0x64; pe[69] = 0x86
  try pe.write(to: exe)
  let checked = try DirectNASBinding.inspectEntry(folder: root, entry: windows)
  guard checked.url.path == exe.path, checked.fingerprints.count == 1 else { fatalError("Wine entry mapping") }
  let direct = try DirectNASRoot.launchEntry(in: root, entry: windows)
  guard direct.path == exe.path else { fatalError("Root-based Windows entry mapping") }
  let alias = root.appendingPathComponent("Alias.exe")
  try fm.createSymbolicLink(at: alias, withDestinationURL: exe)
  try reject { _ = try DirectNASRoot.launchEntry(in: root, entry: Candidate(path: "Alias.exe", backend: .wine, engine: "fixture")) }
  try reject { _ = try DirectNASRoot.launchEntry(in: root, entry: Candidate(path: "../Play.exe", backend: .wine, engine: "fixture")) }
  pe[68] = 0x64; pe[69] = 0xaa
  try pe.write(to: exe)
  try reject { _ = try DirectNASBinding.inspectEntry(folder: root, entry: windows) }
  try reject { _ = try DirectNASRoot.launchEntry(in: root, entry: windows) }
  print("PASS nested and dot .app mapping, x64 PE validation, local-disk refusal, unsupported backend, traversal and executable symlink rejection")
 }
}
