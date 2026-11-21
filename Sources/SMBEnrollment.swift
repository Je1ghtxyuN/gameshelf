import Foundation
import Darwin

struct SMBEnrollmentStatus: Decodable {
 let state: String
 let share: String?
}

struct SMBEnrollmentChallenge: Decodable {
 let challengeID: String
 let share: String
 let filename: String

 func readProof() throws -> String {
  let hex = Set("0123456789abcdef".utf8)
  guard challengeID.utf8.count == 32, challengeID.utf8.allSatisfy(hex.contains),
        filename == challengeID + ".proof" else { throw LibraryError("服务器返回的 SMB 验证资料无效。") }
  guard let root = try NetworkLibraryMount.existingShareRoot(named: share) else {
   throw LibraryError("请在 Finder 中连接 NAS 的「\(share)」共享，再验证。")
  }
  let file = root.appendingPathComponent(filename)
  let fd = Darwin.open(file.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
  guard fd >= 0 else { throw LibraryError("无法读取 SMB 验证凭证，请检查 Finder 中连接的 NAS 账号。") }
  defer { Darwin.close(fd) }
  var info = stat()
  guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG, info.st_size == 64 else {
   throw LibraryError("SMB 验证凭证无效。")
  }
  let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: false)
  let bytes = try handle.read(upToCount: 65) ?? Data()
  guard bytes.count == 64, bytes.allSatisfy(hex.contains) else { throw LibraryError("SMB 验证凭证无效。") }
  return String(decoding: bytes, as: UTF8.self)
 }
}
