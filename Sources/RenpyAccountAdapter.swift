import Foundation
import CryptoKit
import Darwin

struct SharedSaveNamespace:Decodable {
 let key:String
 let relativePath:String
 let adapter:String
 let reviewedProfileID:String
 let revision:Int
 let rulesSHA256:String
 func validate(serverID:String,accountID:String,canonicalID:String,compatibilityKey:String?=nil)throws {
  func sha(_ value:String,_ length:Int)->Bool{value.utf8.count==length && value.utf8.allSatisfy{(48...57).contains($0)||(97...102).contains($0)}}
  let parts=key.split(separator:"/",omittingEmptySubsequences:false).map(String.init)
  guard let compatibilityKey,!compatibilityKey.isEmpty,compatibilityKey.utf8.count<=128 else{throw LibraryError("游戏版本未提供已审核的存档兼容标识。")}
  let compatibilitySHA=SHA256.hash(data:Data(compatibilityKey.utf8)).map{String(format:"%02x",$0)}.joined()
  guard parts.count==4,parts[0]==serverID,parts[1]==accountID,parts[2]==canonicalID,sha(canonicalID,32),parts[3]==compatibilitySHA,
        relativePath==RenpyAccountAdapter.relativePath,adapter=="renpy-savedir-v1",revision>0,
        !reviewedProfileID.isEmpty,reviewedProfileID.utf8.count<=128,
        reviewedProfileID.utf8.allSatisfy({(48...57).contains($0)||(65...90).contains($0)||(97...122).contains($0)||[45,46,95].contains($0)}),sha(rulesSHA256,64)
  else{throw LibraryError("服务器返回的账号存档映射不匹配，未启动游戏。")}
 }
}

/// Only for a valid, fingerprint-reviewed renpy-savedir-v1 account lease.
/// Ordinary local games and unidentified engine builds are never patched.
enum RenpyAccountAdapter {
 static let sourceSHA256="7a84aea6ddccab67c7e004d94cd38092e4a910c15ccb6fabb44aecbde1108427"
 static let patchedSHA256="ba48a4c044ec8d557bbc2f42aab441caaf52480ebeb29f2a9208594b6dc09596"
 static let relativePath=".gameshelf-profile/CanonicalSaves/renpy"
 struct LaunchFields {let arguments:[String];let environment:[String:String]}
 struct Locations {
  let root:URL
  var arguments:[String]{["--savedir",root.path]}
  var environment:[String:String]{[
   "RENPY_PATH_TO_SAVES":root.appendingPathComponent("Auxiliary").path,
   "RENPY_MULTIPERSISTENT":root.appendingPathComponent("MultiPersistent").path,
   "PYTHONDONTWRITEBYTECODE":"1"
  ]}
  var wine:LaunchFields {
   func path(_ url:URL)->String{"Z:"+url.path.replacingOccurrences(of:"/",with:"\\")}
   return LaunchFields(arguments:["--savedir",path(root)],environment:[
    "RENPY_PATH_TO_SAVES":path(root.appendingPathComponent("Auxiliary")),
    "RENPY_MULTIPERSISTENT":path(root.appendingPathComponent("MultiPersistent")),
    "PYTHONDONTWRITEBYTECODE":"1"
   ])
  }
 }
 private static func digest(_ bytes:Data)->String{SHA256.hash(data:bytes).map{String(format:"%02x",$0)}.joined()}
 static func verifyPatchedModule(at path:URL)throws {
  let fd=open(path.path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC)
  guard fd>=0 else{throw LibraryError("无法核对账号专属 Ren’Py 运行模块。")}
  let handle=FileHandle(fileDescriptor:fd,closeOnDealloc:true);defer{try? handle.close()}
  var before=stat(),after=stat()
  guard fstat(fd,&before)==0,(before.st_mode&S_IFMT)==S_IFREG,before.st_size>0,before.st_size<=128*1024 else{throw LibraryError("Ren’Py 运行模块类型或大小不匹配。")}
  let bytes=try handle.read(upToCount:128*1024+1) ?? Data()
  guard fstat(fd,&after)==0,before.st_ino==after.st_ino,before.st_size==after.st_size,before.st_mtimespec.tv_sec==after.st_mtimespec.tv_sec,before.st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec,
        bytes.count==before.st_size,digest(bytes)==patchedSHA256 else{throw LibraryError("Ren’Py 账号存档模块未通过完整性检查。")}
 }
 static func patchSaveLocation(_ original:Data)throws->Data {
  guard original.count<=128*1024 else{throw LibraryError("Ren’Py 存档模块超出适配范围。")}
  let hash=digest(original)
  if hash==patchedSHA256{return original}
  guard hash==sourceSHA256,let text=String(data:original,encoding:.utf8),let start=text.range(of:"    # 2. Game-local savedir.\n"),let end=text.range(of:"    # Scan the location once.\n",range:start.upperBound..<text.endIndex) else{throw LibraryError("Ren’Py 存档模块与已验证版本不匹配。")}
  let patched=Data((text[..<start.lowerBound]+"    # GameShelf account adapter v1: use only the supplied account savedir.\n\n"+text[end.lowerBound...]).utf8)
  guard digest(patched)==patchedSHA256 else{throw LibraryError("Ren’Py 存档适配完整性检查失败。")}
  return patched
 }
 static func locations(view:URL,relativePath:String)throws->Locations {
  guard relativePath==Self.relativePath,view.isFileURL else{throw LibraryError("账号存档命名空间无效。")}
  let base=view.standardizedFileURL
  guard base.path != "/",!base.path.contains("\\"),!base.path.contains(":"),!base.path.contains("\""),!base.path.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains) else{throw LibraryError("账号存档路径无效。")}
  let fm=FileManager.default
  func directory(_ path:URL)throws {
   let info=try fm.attributesOfItem(atPath:path.path)
   guard info[.type] as? FileAttributeType == .typeDirectory else{throw LibraryError("账号存档目录包含链接或异常文件。")}
  }
  try directory(base)
  var path=base
  for piece in relativePath.split(separator:"/"){path.appendPathComponent(String(piece));try directory(path)}
  guard path.resolvingSymlinksInPath().path.hasPrefix(base.resolvingSymlinksInPath().path+"/") else{throw LibraryError("账号存档目录超出个人视图。")}
  for name in ["Auxiliary","MultiPersistent"] {
   let child=path.appendingPathComponent(name)
   if !fm.fileExists(atPath:child.path){try fm.createDirectory(at:child,withIntermediateDirectories:false,attributes:[.posixPermissions:0o700])}
   try directory(child)
  }
  return Locations(root:path)
 }
}
