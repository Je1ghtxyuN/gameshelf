import Foundation
import CryptoKit
import Darwin

struct DirectNASBinding: Codable, Equatable {
 let key: Key
 let folder: String
 private let mountSHA256: String
 private let fingerprints: [String]

 struct Key: Codable, Equatable {
  let identity: AccountLibraryIdentity
  let canonicalID: String
  let variantID: String
  let entryPath: String
  let entryBackend: Backend

  fileprivate var fileName: String {
   let fields = [canonicalID, variantID, entryPath, entryBackend.rawValue].map { "\($0.utf8.count):\($0)" }.joined()
   return DirectNASBinding.sha(fields) + ".json"
  }
 }

 static func capture(key: Key, folder: URL, entry: Candidate) throws -> Self {
  guard key.entryPath == entry.path, key.entryBackend == entry.backend else { throw LibraryError("所选游戏入口已变更。") }
  let folder = folder.standardizedFileURL
  let mount = try mountIdentity(folder)
  let inspected = try inspectEntry(folder: folder, entry: entry)
  guard try mountIdentity(folder) == mount else { throw LibraryError("NAS 挂载已变更。") }
  return Self(key: key, folder: folder.path, mountSHA256: mount, fingerprints: inspected.fingerprints)
 }

 func verifiedEntry(entry: Candidate) throws -> URL {
  let directory = URL(fileURLWithPath: folder, isDirectory: true)
  guard try Self.capture(key: key, folder: directory, entry: entry) == self else {
   throw LibraryError("NAS 挂载或游戏入口已变更，请重新选择资料夹。")
  }
  return Self.entryURL(folder: directory, entry: entry)
 }

 static func load(_ key: Key, profile: URL) throws -> Self? {
  let file = try bindingDirectory(profile).appendingPathComponent(key.fileName)
  guard let data = try AccountLibraryDisk.regularData(file, limit: 16384) else { return nil }
  let value = try JSONDecoder().decode(Self.self, from: data)
  guard value.key == key, value.folder.hasPrefix("/") else { throw LibraryError("本机 NAS 绑定身份不匹配。") }
  return value
 }

 func save(profile: URL) throws {
  _ = try AccountLibraryCache(root: profile, identity: key.identity)
  let directory = try Self.bindingDirectory(profile)
  try AccountLibraryDisk.directory(directory)
  try AccountLibraryDisk.write(JSONEncoder().encode(self), to: directory.appendingPathComponent(key.fileName))
 }

 private static func bindingDirectory(_ profile: URL) throws -> URL {
  let directory = profile.appendingPathComponent("DirectNASBindings", isDirectory: true)
  if let attributes = try? FileManager.default.attributesOfItem(atPath: directory.path),
     attributes[.type] as? FileAttributeType != .typeDirectory {
   throw LibraryError("本机 NAS 绑定目录无效。")
  }
  return directory
 }

 private static func entryURL(folder: URL, entry: Candidate) -> URL {
  entry.path == "." ? folder : folder.appendingPathComponent(entry.path, isDirectory: true)
 }

 static func supports(_ entry: Candidate, platform: String) -> Bool {
  if platform == "macos" { return AccountLibraryGame.safeEntry(entry, platform: platform) }
  return platform == "windows" && [Backend.wine, .renpy, .rpgmz].contains(entry.backend) &&
   AccountLibraryGame.safeEntry(entry, platform: platform)
 }

 static func inspectEntry(folder: URL, entry: Candidate) throws -> (url: URL, fingerprints: [String]) {
  if entry.backend == .native {
   let bundle = try inspectBundle(folder: folder, entry: entry)
   return (bundle.app, bundle.fingerprints)
  }
  let executable = try DirectNASRoot.launchEntry(in: folder, entry: entry)
  return (executable, [try SharedEntryFingerprint.hash(folder: folder, entry: entry)])
 }

 fileprivate static func verifyPE(_ executable: URL) throws {
  let file = try FileHandle(forReadingFrom: executable)
  defer { try? file.close() }
  let header = Array(try file.read(upToCount: 64) ?? Data())
  guard header.count == 64, header[0] == 0x4d, header[1] == 0x5a else {
   throw LibraryError("绑定目录中的 Windows 入口不是有效的 PE 程序。")
  }
  let offset = (0..<4).reduce(UInt64(0)) { $0 | UInt64(header[60+$1]) << (8*$1) }
  guard (64...1_048_576).contains(offset) else { throw LibraryError("Windows 入口的 PE 标头无效。") }
  try file.seek(toOffset: offset)
  let pe = Array(try file.read(upToCount: 6) ?? Data())
  guard pe.count == 6, Array(pe.prefix(4)) == [0x50,0x45,0,0],
        [0x014c,0x8664].contains(Int(pe[4]) | Int(pe[5]) << 8) else {
   throw LibraryError("NAS 直启只支持 x86 或 x64 Windows 程序。")
  }
 }

 static func inspectBundle(folder: URL, entry: Candidate) throws -> (app: URL, fingerprints: [String]) {
  guard AccountLibraryGame.safeEntry(entry, platform: "macos") else {
   throw LibraryError("NAS 直启仅支持可直接运行的 macOS .app 版本。")
  }
  let app = entryURL(folder: folder, entry: entry)
  guard app.pathExtension.lowercased() == "app" else { throw LibraryError("请选择包含已选入口的游戏资料夹。") }
  let prefix = entry.path == "." ? "" : entry.path + "/"
  let plistPath = prefix + "Contents/Info.plist"
  let plistHash = try SharedEntryFingerprint.hash(folder: folder, entry: entry)
  let plist = folder.appendingPathComponent(plistPath)
  guard let bytes = try AccountLibraryDisk.regularData(plist, limit: 1024 * 1024),
        SHA256.hash(data: bytes).map({ String(format: "%02x", $0) }).joined() == plistHash,
        let info = try PropertyListSerialization.propertyList(from: bytes, format: nil) as? [String: Any],
        let name = info["CFBundleExecutable"] as? String, !name.isEmpty, name != ".", name != "..",
        !name.contains("/"), !name.contains("\\"),
        !name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else {
   throw LibraryError("macOS 游戏入口的 Info.plist 无效。")
  }
  let executablePath = prefix + "Contents/MacOS/" + name
  let executable = folder.appendingPathComponent(executablePath)
  let executableHash = try SharedEntryFingerprint.hash(folder: folder, entry: Candidate(path: executablePath, backend: .native, engine: "macOS"))
  guard access(executable.path, X_OK) == 0, try NativeAppIdentity.isMachO(executable) else {
   throw LibraryError("NAS 上的 macOS 游戏入口不可直接执行。")
  }
  return (app, [plistHash, executableHash])
 }

 fileprivate static func mountIdentity(_ folder: URL) throws -> String {
  var info = stat(), filesystem = statfs()
  guard folder.isFileURL, folder.path == folder.resolvingSymlinksInPath().path,
        lstat(folder.path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
        statfs(folder.path, &filesystem) == 0 else {
   throw LibraryError("NAS 游戏资料夹不可访问，或路径包含符号链接。")
  }
  let type = string(&filesystem.f_fstypename)
  guard ["smbfs", "nfs", "afpfs"].contains(type), filesystem.f_flags & UInt32(MNT_LOCAL) == 0 else {
   throw LibraryError("请选择已挂载的 NAS 游戏资料夹。")
  }
  return sha([type, string(&filesystem.f_mntonname), string(&filesystem.f_mntfromname)]
   .map { "\($0.utf8.count):\($0)" }.joined())
 }

 private static func string<T>(_ tuple: inout T) -> String {
  withUnsafeBytes(of: &tuple) { String(decoding: $0.prefix(while: { $0 != 0 }), as: UTF8.self) }
 }

 private static func sha(_ text: String) -> String {
  SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
 }
}

enum DirectNASRoot {
 static func folder(rootID: String, profile: URL) throws -> URL? {
  let file = try bindingFile(rootID: rootID, profile: profile)
  guard let data = try AccountLibraryDisk.regularData(file, limit: 16384) else { return nil }
  let path = try JSONDecoder().decode(String.self, from: data)
  guard path.hasPrefix("/"), !path.utf8.contains(0) else { throw LibraryError("NAS 根目录记录无效。") }
  return URL(fileURLWithPath: path, isDirectory: true)
 }

 static func bind(rootID: String, folder: URL, profile: URL) throws {
  let file = try bindingFile(rootID: rootID, profile: profile)
  let directory = folder.standardizedFileURL
  _ = try DirectNASBinding.mountIdentity(directory)
  try AccountLibraryDisk.directory(file.deletingLastPathComponent())
  try AccountLibraryDisk.write(JSONEncoder().encode(directory.path), to: file)
 }

 static func gameFolder(source: AccountLibraryNASSource, profile: URL) throws -> URL {
  guard source.isValid else { throw LibraryError("NAS 游戏来源无效。") }
  guard let root = try folder(rootID: source.rootID, profile: profile) else {
   throw LibraryError("请在「设置 → 游戏库」选择已挂载的 NAS 根目录。")
  }
  let mount = try DirectNASBinding.mountIdentity(root)
  let game = try confinedURL(root: root, relative: source.relative, type: .typeDirectory)
  guard try DirectNASBinding.mountIdentity(game) == mount else { throw LibraryError("游戏所在的 NAS 挂载已变更。") }
  return game
 }

 static func launchEntry(in folder: URL, entry: Candidate) throws -> URL {
  guard DirectNASBinding.supports(entry, platform: "windows") else {
   throw LibraryError("NAS 直启不支持此游戏入口。")
  }
  let executable = try confinedURL(root: folder, relative: entry.path, type: .typeRegular)
  try DirectNASBinding.verifyPE(executable)
  return executable
 }

 static func confinedURL(root: URL, relative: String, type: FileAttributeType) throws -> URL {
  guard AccountLibraryNASSource.validRelativePath(relative), root.isFileURL else {
   throw LibraryError("NAS 游戏路径无效。")
  }
  let path = relative == "." ? root : root.appendingPathComponent(relative)
  let attrs = try FileManager.default.attributesOfItem(atPath: path.path)
  guard path.path == path.resolvingSymlinksInPath().path, attrs[.type] as? FileAttributeType == type else {
   throw LibraryError("NAS 游戏路径不可访问或包含符号链接。")
  }
  return path
 }

 private static func bindingFile(rootID: String, profile: URL) throws -> URL {
  guard AccountLibraryNASSource.validRootID(rootID) else { throw LibraryError("NAS 根目录标识无效。") }
  let directory = profile.appendingPathComponent("DirectNASRoots", isDirectory: true)
  if let attributes = try? FileManager.default.attributesOfItem(atPath: directory.path),
     attributes[.type] as? FileAttributeType != .typeDirectory {
   throw LibraryError("本机 NAS 根目录记录无效。")
  }
  return directory.appendingPathComponent(rootID + ".json")
 }
}
