import Foundation
import CryptoKit
import Darwin

/// Authenticated lease evidence, never an executable adapter or environment hint.
/// First bind SharedSaveNamespace to expected server/account/canonical IDs and
/// the selected variant's advertised compatibility key;
/// then validate this proof against that namespace and the selected entry, and
/// verify the mounted export before handing it to an existing launcher.
struct RenpyLaunchProof:Decodable {
 struct Fingerprint:Decodable {
  let path:String
  let role:String
  let sourceSHA256:String
  let exportSHA256:String
  private enum CodingKeys:String,CodingKey{case path,role,sourceSHA256,exportSHA256}
  init(from decoder:Decoder)throws {
   try RenpyProofKeys.check(decoder,allowed:["path","role","sourceSHA256","exportSHA256"])
   let values=try decoder.container(keyedBy:CodingKeys.self)
   path=try values.decode(String.self,forKey:.path);role=try values.decode(String.self,forKey:.role)
   sourceSHA256=try values.decode(String.self,forKey:.sourceSHA256);exportSHA256=try values.decode(String.self,forKey:.exportSHA256)
  }
 }
 let adapter:String
 let platform:String
 let entryPath:String
 let relativeSavePath:String
 let reviewedProfileID:String
 let revision:Int
 let rulesSHA256:String
 let evidenceSHA256:String
 let sourceSignature:String
 let fingerprints:[Fingerprint]
 private enum CodingKeys:String,CodingKey{case adapter,platform,entryPath,relativeSavePath,reviewedProfileID,revision,rulesSHA256,evidenceSHA256,sourceSignature,fingerprints}
 init(from decoder:Decoder)throws {
  try RenpyProofKeys.check(decoder,allowed:["adapter","platform","entryPath","relativeSavePath","reviewedProfileID","revision","rulesSHA256","evidenceSHA256","sourceSignature","fingerprints"])
  let values=try decoder.container(keyedBy:CodingKeys.self)
  adapter=try values.decode(String.self,forKey:.adapter);platform=try values.decode(String.self,forKey:.platform)
  entryPath=try values.decode(String.self,forKey:.entryPath);relativeSavePath=try values.decode(String.self,forKey:.relativeSavePath)
  reviewedProfileID=try values.decode(String.self,forKey:.reviewedProfileID);revision=try values.decode(Int.self,forKey:.revision)
  rulesSHA256=try values.decode(String.self,forKey:.rulesSHA256);evidenceSHA256=try values.decode(String.self,forKey:.evidenceSHA256);sourceSignature=try values.decode(String.self,forKey:.sourceSignature)
  var rows=try values.nestedUnkeyedContainer(forKey:.fingerprints),result:[Fingerprint]=[]
  while !rows.isAtEnd {
   guard result.count<128 else{throw LibraryError("Ren’Py 校验文件数量超出限制。")}
   result.append(try rows.decode(Fingerprint.self))
  }
  fingerprints=result
 }
 func validate(namespace:SharedSaveNamespace,entry:Candidate,platform:String)throws {
  try validateStructure()
  // Namespace key/account/canonical/compatibility binding is validated by the
  // lease owner before this call; proof has no compatibilityKey to re-derive it.
  guard adapter==namespace.adapter,relativeSavePath==namespace.relativePath,
        reviewedProfileID==namespace.reviewedProfileID,revision==namespace.revision,rulesSHA256==namespace.rulesSHA256,
        self.platform==platform,entryPath==entry.path,
        (platform=="macos" && entry.backend == .native) || (platform=="windows" && entry.backend == .renpy)
  else{throw LibraryError("Ren’Py 运行校验与账号映射或所选入口不匹配。")}
 }
 /// Read-only, descriptor-anchored export verification. No scans, patches,
 /// native execution, imports, save-directory creation or environment changes.
 func verify(view:URL)throws {
  try validateStructure()
  try verifyFiles(view:view,paths:fingerprints.map(\.path))
 }
 /// NativeAppCache may rename the copied bundle. Every reviewed role must be
 /// inside the selected source app; nothing outside that prefix may be skipped.
 func verifyCachedApp(_ app:URL,entryPath:String)throws {
  try validateStructure()
  guard platform=="macos",self.entryPath==entryPath else{throw LibraryError("Ren’Py 缓存校验不属于所选原生应用。")}
  let paths:[String]
  if entryPath=="."{paths=fingerprints.map(\.path)}
  else {
   let prefix=entryPath+"/"
   paths=try fingerprints.map{item in
    guard item.path.hasPrefix(prefix),item.path.count>prefix.count else{throw LibraryError("Ren’Py 隐私校验文件位于所选应用之外，不能跳过。")}
    return String(item.path.dropFirst(prefix.count))
   }
  }
  try verifyFiles(view:app,paths:paths)
 }
 /// Verify a fresh NativeAppCache stage BEFORE plist identity rewriting or
 /// codesign. Post-transform reuse is protected by the cache's signed receipt.
 /// The only allowed link is autorun/game, bound to this exact leased source;
 /// only config-role files may be read through that binding, never runtime code.
 func verifyCachedApp(_ stage:URL,entryPath:String,sourceView:URL)throws {
  try validateStructure()
  guard platform=="macos",self.entryPath==entryPath else{throw LibraryError("Ren’Py 缓存校验不属于所选原生应用。")}
  let sourcePrefix=entryPath=="." ? "":entryPath+"/"
  let paths=try fingerprints.map{item -> String in
   guard item.path.hasPrefix(sourcePrefix),item.path.count>sourcePrefix.count else{throw LibraryError("Ren’Py 校验文件位于所选应用之外，不能跳过。")}
   return String(item.path.dropFirst(sourcePrefix.count))
  }
  let payload="Contents/Resources/autorun/game",prefix=payload+"/"
  for (item,path) in zip(fingerprints,paths) where path==payload || path.hasPrefix(prefix) {
   guard item.role=="config",path.hasPrefix(prefix) else{throw LibraryError("Ren’Py payload 中含非 config 的隐私关键代码，暂不支持此缓存布局。")}
  }
  let source=try openDirectory(sourceView);defer{Darwin.close(source)}
  // Repeat ALL source checks, retaining the same descriptor for later config reads.
  try verifyFiles(root:source,paths:fingerprints.map(\.path))
  let cache=try openDirectory(stage);defer{Darwin.close(cache)}
  guard paths.contains(where:{$0.hasPrefix(prefix)}) else{try verifyFiles(root:cache,paths:paths);return}
  let parent=try openRelativeDirectory(root:cache,path:"Contents/Resources/autorun");defer{Darwin.close(parent)}
  let sourcePayload=try openRelativeDirectory(root:source,path:sourcePrefix+payload);defer{Darwin.close(sourcePayload)}
  let expected=sourceView.path+"/"+sourcePrefix+payload
  let before=try payloadLink(parent:parent,expected:expected)
  try checkDirectoryTarget(expected,descriptor:sourcePayload)
  for (item,path) in zip(fingerprints,paths) {
   let actual:String
   if path.hasPrefix(prefix) {
    // Never follow the cache symlink: hash the pinned source payload descriptor.
    actual=try hashFile(root:sourcePayload,path:String(path.dropFirst(prefix.count)),limit:8*1024*1024)
   }else{actual=try hashFile(root:cache,path:path,limit:item.role=="entry" ? 128*1024*1024:8*1024*1024)}
   guard actual==item.exportSHA256 else{throw LibraryError("Ren’Py 缓存导出校验失败："+item.path)}
  }
  try verifyNativeExecutable(root:cache,paths:paths)
  let after=try payloadLink(parent:parent,expected:expected)
  guard before.st_dev==after.st_dev,before.st_ino==after.st_ino,before.st_size==after.st_size,
        before.st_mtimespec.tv_sec==after.st_mtimespec.tv_sec,before.st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec,
        before.st_ctimespec.tv_sec==after.st_ctimespec.tv_sec,before.st_ctimespec.tv_nsec==after.st_ctimespec.tv_nsec else{throw LibraryError("Ren’Py payload 链接在校验期间发生变化。")}
  try checkDirectoryTarget(expected,descriptor:sourcePayload)
 }
 private func verifyFiles(view:URL,paths:[String])throws {
  let root=try openDirectory(view);defer{Darwin.close(root)}
  try verifyFiles(root:root,paths:paths)
 }
 private func verifyFiles(root:Int32,paths:[String])throws {
  guard paths.count==fingerprints.count,paths.allSatisfy(Self.path) else{throw LibraryError("Ren’Py 缓存校验路径不完整或无效。")}
  for (item,path) in zip(fingerprints,paths) {
   let actual=try hashFile(root:root,path:path,limit:item.role=="entry" ? 128*1024*1024:8*1024*1024)
   guard actual==item.exportSHA256 else{throw LibraryError("Ren’Py 导出文件校验失败："+item.path)}
  }
  try verifyNativeExecutable(root:root,paths:paths)
 }
 /// Read the pinned plist itself through the same anchored/no-follow verifier.
 /// A reviewed executable A cannot authorize a plist that actually launches B.
 private func verifyNativeExecutable(root:Int32,paths:[String])throws {
  guard platform=="macos" else{return}
  let info=(entryPath=="." ? "":entryPath+"/")+"Contents/Info.plist"
  guard paths.count==fingerprints.count,
        let metadata=fingerprints.firstIndex(where:{$0.role=="metadata" && $0.path==info}),
        let launch=fingerprints.firstIndex(where:{$0.role=="entry"}) else{throw LibraryError("Ren’Py 原生应用缺少精确 Info.plist 校验证据。")}
  var bytes=Data()
  let digest=try hashFile(root:root,path:paths[metadata],limit:1024*1024,capture: {bytes=$0})
  guard digest==fingerprints[metadata].exportSHA256 else{throw LibraryError("Ren’Py Info.plist 与审核哈希不匹配。")}
  guard let plist=try PropertyListSerialization.propertyList(from:bytes,options:[],format:nil) as? [String:Any],
        let name=plist["CFBundleExecutable"] as? String,!name.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty,
        name.utf8.count<=255,!name.contains("/"),Self.path(name) else{throw LibraryError("Ren’Py Info.plist 未提供安全的 CFBundleExecutable 文件名。")}
  let bundle=String(paths[metadata].dropLast("Contents/Info.plist".count))
  guard paths[launch]==bundle+"Contents/MacOS/"+name else{throw LibraryError("Ren’Py 审核运行入口与 Info.plist 实际启动程序不一致。")}
 }
 private func openDirectory(_ view:URL)throws->Int32 {
  guard view.isFileURL,view.path != "/",view.path.hasPrefix("/"),view.path.utf8.count<=16384,
        !view.path.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains) else{throw LibraryError("Ren’Py 私有视图路径无效。")}
  let parts=view.path.split(separator:"/",omittingEmptySubsequences:true).map(String.init)
  guard !parts.isEmpty,!parts.contains("."),!parts.contains("..") else{throw LibraryError("Ren’Py 私有视图路径超出范围。")}
  var directory=open("/",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC)
  guard directory>=0 else{throw LibraryError("无法核对 Ren’Py 私有视图。")}
  for part in parts {
   let child=openat(directory,part,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC)
   if child<0{Darwin.close(directory);throw LibraryError("Ren’Py 私有视图包含链接或不可用目录。")}
   Darwin.close(directory);directory=child
  }
  return directory
 }
 private func openRelativeDirectory(root:Int32,path:String)throws->Int32 {
  guard Self.path(path) else{throw LibraryError("Ren’Py 相对目录无效。")}
  var directory=dup(root)
  guard directory>=0 else{throw LibraryError("无法核对 Ren’Py 目录。")}
  for part in path.split(separator:"/") {
   let child=openat(directory,String(part),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC)
   if child<0{Darwin.close(directory);throw LibraryError("Ren’Py 目录包含链接或不可用路径。")}
   Darwin.close(directory);directory=child
  }
  return directory
 }
 private func payloadLink(parent:Int32,expected:String)throws->stat {
  var info=stat()
  guard fstatat(parent,"game",&info,AT_SYMLINK_NOFOLLOW)==0,(info.st_mode&S_IFMT)==S_IFLNK else{throw LibraryError("Ren’Py 缓存 payload 必须是已绑定的专属链接。")}
  var buffer=[UInt8](repeating:0,count:32769)
  let count=buffer.withUnsafeMutableBytes{readlinkat(parent,"game",$0.baseAddress!.assumingMemoryBound(to:CChar.self),$0.count)}
  guard count>0,count<buffer.count,String(data:Data(buffer.prefix(count)),encoding:.utf8)==expected else{throw LibraryError("Ren’Py payload 链接未指向精确的租约源目录。")}
  return info
 }
 private func checkDirectoryTarget(_ path:String,descriptor:Int32)throws {
  let current=try openDirectory(URL(fileURLWithPath:path));defer{Darwin.close(current)}
  var expected=stat(),actual=stat()
  guard fstat(descriptor,&expected)==0,fstat(current,&actual)==0,expected.st_dev==actual.st_dev,expected.st_ino==actual.st_ino else{throw LibraryError("Ren’Py payload 的租约源身份发生变化。")}
 }
 private static let privacyRoles:Set<String>=["entry","bootstrap","arguments","main","locations","persistent","tokens","config"]
 private static func sha(_ value:String)->Bool{value.utf8.count==64 && value.utf8.allSatisfy{(48...57).contains($0)||(97...102).contains($0)}}
 private static func path(_ value:String)->Bool {
  guard !value.isEmpty,value.utf8.count<=4096,!value.hasPrefix("/"),!value.contains("\\"),!value.contains(":"),
        !value.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains) else{return false}
  let pieces=value.split(separator:"/",omittingEmptySubsequences:false)
  return pieces.allSatisfy{piece in
   !piece.isEmpty && piece != "." && piece != ".." && piece.folding(options:[.caseInsensitive],locale:Locale(identifier:"en_US_POSIX")) != ".gameshelf-profile"
  }
 }
 private func validateStructure()throws {
  guard adapter=="renpy-savedir-v1",relativeSavePath==RenpyAccountAdapter.relativePath,revision>0,
        !reviewedProfileID.isEmpty,reviewedProfileID.utf8.count<=128,
        reviewedProfileID.utf8.allSatisfy({(48...57).contains($0)||(65...90).contains($0)||(97...122).contains($0)||[45,46,95].contains($0)}),
        Self.sha(rulesSHA256),Self.sha(evidenceSHA256),Self.sha(sourceSignature),
        (platform=="macos" && (entryPath=="." || (Self.path(entryPath) && entryPath.lowercased().hasSuffix(".app")))) ||
        (platform=="windows" && Self.path(entryPath) && entryPath.lowercased().hasSuffix(".exe")),
        (8...128).contains(fingerprints.count) else{throw LibraryError("Ren’Py 运行校验格式或入口无效。")}
  var paths=Set<String>(),roles=Set<String>(),entries:[Fingerprint]=[]
  for item in fingerprints {
   let key=item.path.precomposedStringWithCanonicalMapping.folding(options:[.caseInsensitive],locale:Locale(identifier:"en_US_POSIX"))
   guard Self.path(item.path),paths.insert(key).inserted,Self.privacyRoles.contains(item.role) || item.role=="metadata",
         Self.sha(item.sourceSHA256),Self.sha(item.exportSHA256) else{throw LibraryError("Ren’Py 校验路径、角色或 SHA-256 无效或重复。")}
   roles.insert(item.role)
   if item.role=="entry"{entries.append(item)}
   if item.role=="locations" {
    guard item.sourceSHA256==RenpyAccountAdapter.sourceSHA256,item.exportSHA256==RenpyAccountAdapter.patchedSHA256 else{throw LibraryError("Ren’Py 存档模块尚无受支持的已审核补丁。")}
   }
  }
  guard Self.privacyRoles.isSubset(of:roles),entries.count==1 else{throw LibraryError("Ren’Py 缺少隐私关键文件校验，或存在多个运行入口。")}
  let launch=entries[0]
  guard launch.sourceSHA256==launch.exportSHA256 else{throw LibraryError("Ren’Py 运行入口不能使用未知补丁。")}
  if platform=="windows" {
   guard launch.path==entryPath else{throw LibraryError("Ren’Py EXE 校验不属于所选入口。")}
  }else{
   let prefix=(entryPath=="." ? "":entryPath+"/")+"Contents/MacOS/"
   guard launch.path.hasPrefix(prefix),launch.path.count>prefix.count else{throw LibraryError("Ren’Py 原生运行文件不属于所选应用。")}
   let info=(entryPath=="." ? "":entryPath+"/")+"Contents/Info.plist"
   guard fingerprints.contains(where:{$0.role=="metadata" && $0.path==info}) else{throw LibraryError("Ren’Py 原生应用缺少精确 Info.plist metadata 校验。")}
  }
 }
 private func hashFile(root:Int32,path:String,limit:Int,capture:((Data)->Void)?=nil)throws->String {
  let pieces=path.split(separator:"/").map(String.init)
  var parent=dup(root)
  guard parent>=0 else{throw LibraryError("无法打开 Ren’Py 校验目录。")}
  defer{Darwin.close(parent)}
  for part in pieces.dropLast() {
   let child=openat(parent,part,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC)
   guard child>=0 else{throw LibraryError("Ren’Py 校验文件包含链接或不可用目录。")}
   Darwin.close(parent);parent=child
  }
  let fd=openat(parent,pieces.last!,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC)
  guard fd>=0 else{throw LibraryError("无法读取 Ren’Py 校验文件。")}
  defer{Darwin.close(fd)}
  var before=stat(),after=stat()
  guard fstat(fd,&before)==0,(before.st_mode&S_IFMT)==S_IFREG,before.st_size>=0,before.st_size<=Int64(limit) else{throw LibraryError("Ren’Py 校验文件不是常规文件或超出大小限制。")}
  var hash=SHA256(),total=0,buffer=[UInt8](repeating:0,count:1024*1024),captured=Data()
  while true {
   let count=buffer.withUnsafeMutableBytes{Darwin.read(fd,$0.baseAddress,$0.count)}
   if count<0 && errno==EINTR{continue}
   guard count>=0 else{throw LibraryError("读取 Ren’Py 校验文件失败。")}
   if count==0{break}
   total+=count
   guard total<=limit else{throw LibraryError("Ren’Py 校验文件在读取期间超出限制。")}
   let chunk=Data(buffer.prefix(count));hash.update(data:chunk)
   if capture != nil{captured.append(chunk)}
  }
  guard fstat(fd,&after)==0,before.st_dev==after.st_dev,before.st_ino==after.st_ino,before.st_size==after.st_size,
        before.st_mtimespec.tv_sec==after.st_mtimespec.tv_sec,before.st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec,
        before.st_ctimespec.tv_sec==after.st_ctimespec.tv_sec,before.st_ctimespec.tv_nsec==after.st_ctimespec.tv_nsec,
        before.st_mode==after.st_mode,total==before.st_size else{throw LibraryError("Ren’Py 校验文件在读取期间发生变化。")}
  capture?(captured)
  return hash.finalize().map{String(format:"%02x",$0)}.joined()
 }
}
private struct RenpyProofKeys:CodingKey {
 var stringValue:String
 var intValue:Int?{nil}
 init?(stringValue:String){self.stringValue=stringValue}
 init?(intValue:Int){return nil}
 static func check(_ decoder:Decoder,allowed:Set<String>)throws {
  let values=try decoder.container(keyedBy:Self.self)
  guard Set(values.allKeys.map(\.stringValue))==allowed else{throw LibraryError("Ren’Py 校验字段不完整或包含未知字段。")}
 }
}
