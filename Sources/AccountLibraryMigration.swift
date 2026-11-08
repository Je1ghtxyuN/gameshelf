import Foundation
import CryptoKit

enum AccountLibraryHistoryLinks {
 static func make(visible:[UUID:String],aliases:[String:String],document:AccountLibraryDocument)->[UUID:String] {
  let allowed=Set(document.games.map(\.id))
  var result=visible.filter{allowed.contains($0.value)}
  for (text,canonical) in aliases where allowed.contains(canonical) {
   guard let id=UUID(uuidString:text),text==id.uuidString,result[id]==nil else{continue}
   result[id]=canonical
  }
  return result
 }
}

/// Wire-compatible with the saved shared-scan-locations.json records. A mapping
/// must explicitly identify a configured DiscoveryStore source, never a title.
struct AccountLibraryMigrationRoot:Codable,Equatable {
 let serverID:String
 let rootID:String
 let localPath:String
}
struct AccountLibraryMigrationSelection:Equatable {
 let localID:UUID
 let rootID:String
 let relative:String
 let entry:Candidate
 /// Stable across retries, title changes and input order. Echo this alongside
 /// the local UUID when acknowledging the exact server registration request.
 let selectionID:String
 fileprivate init(localID:UUID,rootID:String,relative:String,entry:Candidate,owner:AccountLibraryIdentity){
  self.localID=localID;self.rootID=rootID;self.relative=relative;self.entry=entry
  let parts=[owner.storageKey,localID.uuidString,rootID,relative,entry.path,entry.backend.rawValue,entry.engine]
  let bytes=Data(parts.map{String($0.utf8.count)+":"+$0}.joined().utf8)
  selectionID=SHA256.hash(data:bytes).map{String(format:"%02x",$0)}.joined()
 }
}
struct AccountLibraryMigrationSkip:Equatable {
 enum Reason:String {case foreignShared,outsideConfiguredRoots,unmappedSource,invalidPath,invalidEntry}
 let localID:UUID
 let reason:Reason
}
struct AccountLibraryMigrationPlan {
 let owner:AccountLibraryIdentity
 let selections:[AccountLibraryMigrationSelection]
 let skipped:[AccountLibraryMigrationSkip]
 private init(owner:AccountLibraryIdentity,selections:[AccountLibraryMigrationSelection],skipped:[AccountLibraryMigrationSkip]){
  self.owner=owner;self.selections=selections;self.skipped=skipped
 }
 /// Read-only path planning. Call off the main thread: SharedScanSelection may
 /// resolve symlinks on a mounted source. No enumeration, probing or publication.
 static func make(owner:AccountLibraryIdentity,savedBinding:PersonalLibraryBinding?,games:[Game],sources:[LibrarySource],mappings:[AccountLibraryMigrationRoot],publicRootIDs:Set<String>)throws->Self {
  try AccountLibraryMigrationValidation.owner(owner,binding:savedBinding)
  guard games.count<=1000,sources.count<=1000,mappings.count<=1000,
        Set(games.map(\.id)).count==games.count,Set(sources.map(\.id)).count==sources.count,
        publicRootIDs.allSatisfy(AccountLibraryMigrationValidation.id) else{throw LibraryError("迁移输入数量或标识无效。")}
  let configured=try Set(sources.map{try AccountLibraryMigrationValidation.directory($0.path)})
  var mapped:[String:String]=[:],reverse:[String:String]=[:]
  for mapping in mappings where mapping.serverID==owner.serverID {
   let local=try AccountLibraryMigrationValidation.directory(mapping.localPath)
   guard configured.contains(local),AccountLibraryMigrationValidation.id(mapping.rootID),publicRootIDs.contains(mapping.rootID),
         mapped[local].map({$0==mapping.rootID}) ?? true,
         reverse[mapping.rootID].map({$0==local}) ?? true else{throw LibraryError("服务器目录映射未配置、已失效或存在歧义。")}
   mapped[local]=mapping.rootID;reverse[mapping.rootID]=local
  }
  let ordered=configured.sorted{a,b in a.count==b.count ? a<b:a.count>b.count}
  func source(for folder:String)->String?{ordered.first{folder==$0 || folder.hasPrefix($0+"/")}}
  var selections:[AccountLibraryMigrationSelection]=[],skipped:[AccountLibraryMigrationSkip]=[]
  for game in games.sorted(by:{$0.id.uuidString<$1.id.uuidString}) {
   func skip(_ reason:AccountLibraryMigrationSkip.Reason){skipped.append(AccountLibraryMigrationSkip(localID:game.id,reason:reason))}
   if let shared=game.shared,shared.serverID != owner.serverID || shared.accountID != owner.accountID{skip(.foreignShared);continue}
   let folder:String,localRoot:String
   do {
    // Original source wins over a prepared native application/runtime copy.
    let original=game.originalFolder.isEmpty ? nil:try AccountLibraryMigrationValidation.directory(game.originalFolder)
    if let original,let root=source(for:original){folder=original;localRoot=root}
    else {
     let current=try AccountLibraryMigrationValidation.directory(game.folder)
     guard let root=source(for:current) else{skip(.outsideConfiguredRoots);continue}
     folder=current;localRoot=root
    }
   }catch{skip(.invalidPath);continue}
   guard let rootID=mapped[localRoot] else{skip(.unmappedSource);continue}
   let platform:String
   switch game.entry.backend{case .native:platform="macos";case .web:platform="web";case .wine,.renpy,.rpgmz:platform="windows"}
   guard AccountLibraryGame.safeEntry(game.entry,platform:platform),
         !game.entry.engine.isEmpty,game.entry.engine.utf8.count<=256,
         !game.entry.engine.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains),
         game.entry.path != "." || (folder as NSString).pathExtension.lowercased()=="app" else{skip(.invalidEntry);continue}
   do {
    let relative=try SharedScanSelection.relative(folder:folder,localRoot:localRoot)
    if game.entry.path != "." {
     // Existing entry symlinks cannot redirect a selection outside its game.
     _ = try SharedScanSelection.relative(folder:folder+"/"+game.entry.path,localRoot:folder)
    }
    selections.append(AccountLibraryMigrationSelection(localID:game.id,rootID:rootID,relative:relative,entry:game.entry,owner:owner))
   }catch{skip(.invalidPath)}
  }
  return Self(owner:owner,selections:selections,skipped:skipped)
 }
}

/// Acknowledgment from an exact directory/entry registration, NOT a title match.
/// Before constructing this receipt the caller must hash the EXACT source entry
/// using SharedEntryFingerprint.hash(folder:entry:), send entrySHA256 to selected
/// registration, require entryFingerprintsVerified == true, and verify returned
/// rootID/relative/path/backend. This pure model does not hash files or perform
/// registration. The caller obtains variantID/canonicalID from that authenticated
/// response chain; the informational engine label may be re-probed by the server.
struct AccountLibraryMigrationResolution {
 let localID:UUID
 let selectionID:String
 let canonicalID:String
 let variantID:String
}

/// Local-only, identity-bound, controller-compatible UUID -> canonical-ID links.
/// Use on one serial owner. Each apply reloads disk so sequential writers/retries
/// cannot overwrite newer links held by another instance. Originals stay intact.
final class AccountLibraryMigrationManifest {
 private let root:URL
 private let owner:AccountLibraryIdentity
 private let binding:PersonalLibraryBinding
 private var file:URL{root.appendingPathComponent("game-links.json")}
 init(profileRoot:URL,owner:AccountLibraryIdentity,savedBinding:PersonalLibraryBinding?)throws {
  try AccountLibraryMigrationValidation.owner(owner,binding:savedBinding)
  root=profileRoot;self.owner=owner;binding=savedBinding!
  try checkIdentity()
  _ = try load()
 }
 /// Untrusted structural data only. Runtime activation must use loadApprovedAliases.
 func load()throws->[UUID:String] {
  try checkIdentity()
  guard let bytes=try AccountLibraryDisk.regularData(file,limit:512*1024) else{return [:]}
  let raw=try JSONDecoder().decode([String:String].self,from:bytes)
  guard raw.count<=1000 else{throw LibraryError("迁移映射过多，原记录已保留。")}
  var result:[UUID:String]=[:]
  for (text,canonical) in raw {
   guard let local=UUID(uuidString:text),text==local.uuidString,AccountLibraryMigrationValidation.id(canonical) else{throw LibraryError("迁移映射标识无效，原记录已保留。")}
   result[local]=canonical
  }
  return result
 }
 /// Validates the complete batch before any write. Partial acknowledgments are
 /// allowed; missing selections stay unlinked for the next explicit retry.
 func validate(plan:AccountLibraryMigrationPlan,resolutions:[AccountLibraryMigrationResolution],document:AccountLibraryDocument)throws->[UUID:String] {
  try checkIdentity()
  guard plan.owner==owner,resolutions.count<=1000,Set(resolutions.map(\.localID)).count==resolutions.count else{throw LibraryError("迁移确认账号或标识不匹配。")}
  try document.validate(identity:owner)
  let current=try load();var next=current
  for receipt in resolutions {
   guard let selected=plan.selections.first(where:{$0.localID==receipt.localID}),selected.selectionID==receipt.selectionID,
         AccountLibraryMigrationValidation.id(receipt.canonicalID),AccountLibraryMigrationValidation.id(receipt.variantID),
         let game=document.games.first(where:{$0.id==receipt.canonicalID}),
         game.variants.contains(where:{$0.id==receipt.variantID && $0.entries.contains(where:{$0.path==selected.entry.path && $0.backend==selected.entry.backend}) && AccountLibraryGame.safeEntry(selected.entry,platform:$0.platform)}),
         current[receipt.localID].map({$0==receipt.canonicalID}) ?? true else{throw LibraryError("迁移确认与精确目录、入口或账号游戏库不匹配。")}
   next[receipt.localID]=receipt.canonicalID
  }
  guard next.count<=1000 else{throw LibraryError("迁移映射过多，未修改。")}
  return next
 }
 /// No unguarded mutator: capabilities are minted only by the adoption gate.
 @discardableResult func apply(plan:AccountLibraryMigrationPlan,resolutions:[AccountLibraryMigrationResolution],document:AccountLibraryDocument,permits:[AccountOwnerTransitionPermit])throws->[UUID:String] {
  let next=try validate(plan:plan,resolutions:resolutions,document:document)
  try AccountOwnerTransitionGate.commitAliases(root:root,owner:owner,current:load(),next:next,plan:plan,resolutions:resolutions,permits:permits)
  return next
 }
 private func checkIdentity()throws {
  try AccountLibraryMigrationValidation.owner(owner,binding:binding)
  guard root.isFileURL,let kind=(try? FileManager.default.attributesOfItem(atPath:root.path))?[.type] as? FileAttributeType,kind == .typeDirectory,
        let bytes=try AccountLibraryDisk.regularData(root.appendingPathComponent("account-identity.json"),limit:4096),
        try JSONDecoder().decode(AccountLibraryIdentity.self,from:bytes)==owner else{throw LibraryError("迁移目标账号目录不匹配，未修改。")}
 }
}
private enum AccountLibraryMigrationValidation {
 static func owner(_ owner:AccountLibraryIdentity,binding:PersonalLibraryBinding?)throws {
  _ = try AccountLibraryIdentity(serverID:owner.serverID,accountID:owner.accountID)
  guard let binding,binding.serverID==owner.serverID,binding.accountID==owner.accountID else{throw LibraryError("仅原绑定账号可迁移本机游戏库。")}
 }
 static func id(_ value:String)->Bool{value.count==32 && value.utf8.allSatisfy{(48...57).contains($0)||(97...102).contains($0)}}
 static func directory(_ path:String)throws->String {
  guard path.hasPrefix("/"),path.utf8.count<=4096,!path.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains),
        !path.split(separator:"/").contains(where:{$0==".." || $0=="."}) else{throw LibraryError("迁移源目录无效。")}
  // Foundation may canonicalize an existing /private/tmp root to /tmp while
  // leaving a nonexistent child unchanged. Resolve one existing ancestor and
  // append the missing suffix so offline/planned paths use the same spelling.
  let fm=FileManager.default
  var ancestor=URL(fileURLWithPath:path).standardizedFileURL,suffix:[String]=[]
  while !fm.fileExists(atPath:ancestor.path) && ancestor.path != "/" {
   if let attributes=try? fm.attributesOfItem(atPath:ancestor.path),attributes[.type] as? FileAttributeType == .typeSymbolicLink{throw LibraryError("迁移目录包含失效链接。")}
   suffix.append(ancestor.lastPathComponent);ancestor.deleteLastPathComponent()
  }
  var resolved=ancestor.resolvingSymlinksInPath()
  for component in suffix.reversed(){resolved.appendPathComponent(component)}
  let canonical=resolved.path
  _ = try SharedScanSelection.relative(folder:canonical,localRoot:canonical)
  return canonical
 }
}
