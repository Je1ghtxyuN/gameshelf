import Foundation
import CryptoKit

struct AccountLibraryIdentity:Codable,Equatable {
 let serverID:String
 let accountID:String
 init(serverID:String,accountID:String)throws {
  for value in [serverID,accountID] {
   guard !value.isEmpty,value.utf8.count<=128,!value.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains) else{throw LibraryError("账号身份无效。")}
  }
  self.serverID=serverID;self.accountID=accountID
 }
 var storageKey:String {
  let value=[serverID,accountID].map{String($0.utf8.count)+":"+$0}.joined()
  return SHA256.hash(data:Data(value.utf8)).map{String(format:"%02x",$0)}.joined()
 }
 func gameIdentity(_ gameID:String)->UUID {
  let key=SHA256.hash(data:Data((storageKey+":"+gameID).utf8)).map{String(format:"%02x",$0)}.joined()
  return UUID(uuidString:"\(key.prefix(8))-\(key.dropFirst(8).prefix(4))-\(key.dropFirst(12).prefix(4))-\(key.dropFirst(16).prefix(4))-\(key.dropFirst(20).prefix(12))")!
 }
}
struct AccountLibraryUser:Codable {var id:String;var username:String;var admin:Bool}
struct AccountLibraryCollection:Codable {var id:UUID;var name:String}
struct AccountLibraryVariant:Codable {
 var id:String
 var platform:String
 var version:String?
 var label:String?=nil
 var entries:[Candidate]
 var saveCompatibilityKey:String?
 var available:Bool
}
struct AccountLibrarySelection {let variant:AccountLibraryVariant;let entry:Candidate}
struct AccountLibraryPreferredEntry:Codable {var variantID:String;var entryPath:String}
struct AccountLibraryGame:Codable {
 var id:String
 var title:String
 var revision:Int
 var favorite:Bool
 var collectionIDs:[UUID]
 var playtimeSeconds:Double
 var lastPlayed:Double?
 var coverSHA256:String?
 var variants:[AccountLibraryVariant]
 var preferredEntries:[String:AccountLibraryPreferredEntry]?=nil
 func selection(platform:String)throws->AccountLibrarySelection {
  guard platform=="macos" || platform=="windows" else{throw LibraryError("不支持当前系统。")}
  func rank(_ variant:AccountLibraryVariant)->Int {
   if platform=="windows" {return variant.platform=="windows" ? 0:variant.platform=="web" ? 1:10}
   return variant.platform=="macos" ? 0:variant.platform=="web" ? 1:variant.platform=="windows" ? 2:10
  }
  if let preference=preferredEntries?[platform],let variant=variants.first(where:{$0.id==preference.variantID && $0.platform==platform && $0.available}),let entry=variant.entries.first(where:{$0.path==preference.entryPath && Self.safeEntry($0,platform:platform)}){return AccountLibrarySelection(variant:variant,entry:entry)}
  for variant in variants.enumerated().sorted(by:{let a=rank($0.element),b=rank($1.element);if a != b{return a<b};let ap=preferredEntries?[$0.element.platform]?.variantID==$0.element.id,bp=preferredEntries?[$1.element.platform]?.variantID==$1.element.id;return ap != bp ? ap:$0.offset<$1.offset}).map(\.element) where variant.available && rank(variant)<10 {
   let preference=preferredEntries?[variant.platform]
   let entries=variant.entries.enumerated().sorted{a,b in let ap=preference?.variantID==variant.id && preference?.entryPath==a.element.path,bp=preference?.variantID==variant.id && preference?.entryPath==b.element.path;return ap != bp ? ap:a.offset<b.offset}.map(\.element)
   for entry in entries where Self.safeEntry(entry,platform:variant.platform) {
    return AccountLibrarySelection(variant:variant,entry:entry)
   }
  }
  throw LibraryError("没有适用于当前系统的游戏版本。")
 }
 static func safeEntry(_ entry:Candidate,platform:String)->Bool {
  guard SharedScanSelection.launchable(entry),entry.path.utf8.count<=4096,(try? EntryPolicy.validate(entry)) != nil else{return false}
  if entry.path=="." {return platform=="macos" && entry.backend == .native}
  let pieces=entry.path.split(separator:"/",omittingEmptySubsequences:false)
  guard !entry.path.contains("\\"),!entry.path.contains(":"),!entry.path.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains),pieces.allSatisfy({!$0.isEmpty && $0 != "." && $0 != ".."}) else{return false}
  if platform=="macos" {return entry.backend == .native && entry.path.lowercased().hasSuffix(".app")}
  if platform=="web" {return entry.backend == .web || entry.backend == .rpgmz}
  return platform=="windows" && entry.backend != .native && entry.path.lowercased().hasSuffix(".exe")
 }
}
struct AccountLibraryDocument:Codable {
 var schemaVersion:Int
 var serverID:String
 var user:AccountLibraryUser
 var games:[AccountLibraryGame]
 var collections:[AccountLibraryCollection]
 func validate(identity:AccountLibraryIdentity)throws {
  func id(_ value:String)->Bool{value.count==32 && value.utf8.allSatisfy{(48...57).contains($0)||(97...102).contains($0)}}
  func text(_ value:String,_ limit:Int)->Bool{!value.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty && value.utf8.count<=limit && !value.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains)}
  guard schemaVersion==2,serverID==identity.serverID,user.id==identity.accountID,text(user.username,240),games.count<=1000,collections.count<=200,Set(games.map(\.id)).count==games.count,Set(collections.map(\.id)).count==collections.count else{throw LibraryError("游戏库账号或资料不匹配。")}
  let groups=Set(collections.map(\.id));var editionOwners:[String:String]=[:]
  guard collections.allSatisfy({text($0.name,240)}) else{throw LibraryError("分类资料无效。")}
  for game in games {
   guard id(game.id),text(game.title,1024),game.revision>=0,game.playtimeSeconds.isFinite,(0...31557600000).contains(game.playtimeSeconds),game.lastPlayed.map({$0.isFinite && (0...253402300799).contains($0)}) ?? true,
         game.collectionIDs.count<=200,Set(game.collectionIDs).count==game.collectionIDs.count,Set(game.collectionIDs).isSubset(of:groups),game.variants.count<=100,
         game.coverSHA256.map({$0.count==64 && $0.utf8.allSatisfy{(48...57).contains($0)||(97...102).contains($0)}}) ?? true else{throw LibraryError("游戏资料无效。")}
   if let preferences=game.preferredEntries {
    guard preferences.count<=3 else{throw LibraryError("启动偏好无效。")}
    for (platform,preference) in preferences {
     guard ["macos","windows","web"].contains(platform),id(preference.variantID),preference.entryPath.utf8.count<=4096,!preference.entryPath.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains),game.variants.contains(where:{$0.id==preference.variantID && $0.platform==platform && $0.entries.contains(where:{$0.path==preference.entryPath && AccountLibraryGame.safeEntry($0,platform:platform)})}) else{throw LibraryError("启动偏好与游戏版本不匹配。")}
    }
   }
   var platforms=Set<String>()
   for variant in game.variants {
    guard id(variant.id),(editionOwners[variant.id]==nil || editionOwners[variant.id]==game.id),platforms.insert(variant.id+":"+variant.platform).inserted,["macos","windows","web"].contains(variant.platform),variant.entries.count<=256,variant.version.map({text($0,128)}) ?? true,variant.label.map({text($0,1024)}) ?? true,variant.saveCompatibilityKey.map({text($0,128)}) ?? true else{throw LibraryError("游戏版本资料无效。")}
    editionOwners[variant.id]=game.id
   }
  }
 }
}

enum AccountLibraryDisk {
 static func regularData(_ file:URL,limit:Int)throws->Data? {
  let fm=FileManager.default
  let attr:[FileAttributeKey:Any]
  do{attr=try fm.attributesOfItem(atPath:file.path)}catch let e as NSError where e.domain==NSCocoaErrorDomain && [NSFileNoSuchFileError,NSFileReadNoSuchFileError].contains(e.code){return nil}
  guard attr[.type] as? FileAttributeType == .typeRegular,(attr[.size] as? NSNumber)?.intValue ?? Int.max<=limit else{throw LibraryError("账号资料文件无效，原件已保留。")}
  let bytes=try Data(contentsOf:file);guard bytes.count<=limit else{throw LibraryError("账号资料文件过大。")};return bytes
 }
 static func directory(_ path:URL)throws {
  let fm=FileManager.default
  if let attr=try? fm.attributesOfItem(atPath:path.path){guard attr[.type] as? FileAttributeType == .typeDirectory else{throw LibraryError("账号资料目录无效。")}}
  else{try fm.createDirectory(at:path,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])}
 }
 static func write(_ data:Data,to path:URL)throws {try data.write(to:path,options:.atomic);try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:path.path)}
}
enum AccountLibraryProfile {
 static func open(under root:URL,identity:AccountLibraryIdentity,legacyOwner:AccountLibraryIdentity?)throws->URL {
  let fm=FileManager.default,parent=root.appendingPathComponent("AccountLibraries"),account=parent.appendingPathComponent(identity.storageKey),library=account.appendingPathComponent("Library")
  try AccountLibraryDisk.directory(parent)
  if fm.fileExists(atPath:account.path) {
   try AccountLibraryDisk.directory(account);try AccountLibraryDisk.directory(library)
   guard let bytes=try AccountLibraryDisk.regularData(library.appendingPathComponent("account-identity.json"),limit:4096),try JSONDecoder().decode(AccountLibraryIdentity.self,from:bytes)==identity else{throw LibraryError("账号资料归属不匹配，未修改。")}
   return library
  }
  var source:[String:Data]=[:],prepared:[String:Data]=[:]
  if identity==legacyOwner {
   for name in ["library.json","collections.json","playtime.json"] {if let bytes=try AccountLibraryDisk.regularData(root.appendingPathComponent(name),limit:16*1024*1024){source[name]=bytes}}
   let games=try source["library.json"].map{try JSONDecoder().decode([Game].self,from:$0)} ?? []
   let retained=games.filter{$0.shared==nil || ($0.shared?.serverID==identity.serverID && $0.shared?.accountID==identity.accountID)}
   let ids=Set(retained.map(\.id));let encoder=JSONEncoder();encoder.outputFormatting=[.prettyPrinted,.sortedKeys]
   prepared["library.json"]=try encoder.encode(retained)
   if let raw=source["collections.json"] {
    var groups=try JSONDecoder().decode([GameCollection].self,from:raw)
    for i in groups.indices{groups[i].gameIDs=groups[i].gameIDs.filter{ids.contains($0)}}
    prepared["collections.json"]=try encoder.encode(groups)
   }
   if let raw=source["playtime.json"] {
    guard var value=try JSONSerialization.jsonObject(with:raw) as? [String:Any],value["version"] as? Int==1,let entries=value["entries"] as? [[String:Any]] else{throw LibraryError("原游玩记录无法读取，未迁移。")}
    value["entries"]=try entries.filter{entry in
     guard let session=entry["session"] as? [String:Any],let text=session["gameID"] as? String,let id=UUID(uuidString:text) else{throw LibraryError("原游玩记录无效，未迁移。")}
     return ids.contains(id)
    }
    prepared["playtime.json"]=try JSONSerialization.data(withJSONObject:value,options:[.sortedKeys])
   }
  }
  let stage=parent.appendingPathComponent(".preparing-"+UUID().uuidString),stageLibrary=stage.appendingPathComponent("Library")
  try AccountLibraryDisk.directory(stageLibrary)
  do {
   try AccountLibraryDisk.write(JSONEncoder().encode(identity),to:stageLibrary.appendingPathComponent("account-identity.json"))
   for (name,bytes) in prepared{try AccountLibraryDisk.write(bytes,to:stageLibrary.appendingPathComponent(name))}
   if !source.isEmpty {
    let backup=stage.appendingPathComponent("MigrationBackup");try AccountLibraryDisk.directory(backup)
    for (name,bytes) in source{try AccountLibraryDisk.write(bytes,to:backup.appendingPathComponent(name))}
   }
   _ = try LibraryStore(root:stageLibrary)
   try fm.moveItem(at:stage,to:account)
  }catch{try? fm.removeItem(at:stage);throw error}
  return library
 }
}
final class AccountLibraryCache {
 private let root:URL
 private let identity:AccountLibraryIdentity
 init(root:URL,identity:AccountLibraryIdentity)throws {
  self.root=root;self.identity=identity
  guard let bytes=try AccountLibraryDisk.regularData(root.appendingPathComponent("account-identity.json"),limit:4096),try JSONDecoder().decode(AccountLibraryIdentity.self,from:bytes)==identity else{throw LibraryError("账号缓存归属不匹配。")}
 }
 func load()throws->AccountLibraryDocument? {
  guard let data=try AccountLibraryDisk.regularData(root.appendingPathComponent("account-library.json"),limit:4*1024*1024) else{return nil}
  let value=try JSONDecoder().decode(AccountLibraryDocument.self,from:data);try value.validate(identity:identity);return value
 }
 func save(_ value:AccountLibraryDocument)throws {
  try value.validate(identity:identity)
  let data=try JSONEncoder().encode(value);guard data.count<=4*1024*1024 else{throw LibraryError("账号游戏库资料过大。")}
  try AccountLibraryDisk.write(data,to:root.appendingPathComponent("account-library.json"))
 }
}

/// Recover the projection as a unit before comparing local metadata with a
/// server baseline. A crash between the two cache files must never look like a
/// user edit that reverts newer metadata on the server.
enum AccountLibraryProjection {
 private struct Pending:Codable {
  var identity:AccountLibraryIdentity
  var document:AccountLibraryDocument
  var games:[Game]
  var collections:[GameCollection]
 }
 static func recover(store:LibraryStore,identity:AccountLibraryIdentity)throws {
  let file=store.root.appendingPathComponent("projection-pending.json")
  guard let bytes=try AccountLibraryDisk.regularData(file,limit:16*1024*1024) else{return}
  let pending=try JSONDecoder().decode(Pending.self,from:bytes)
  guard pending.identity==identity else{throw LibraryError("账号投影恢复身份不匹配，原件已保留。")}
  try pending.document.validate(identity:identity)
  try store.replaceAccountLibrary(games:pending.games,collections:pending.collections)
  try AccountLibraryCache(root:store.root,identity:identity).save(pending.document)
  try FileManager.default.removeItem(at:file)
 }
 static func commit(_ document:AccountLibraryDocument,games:[Game],collections:[GameCollection],store:LibraryStore,identity:AccountLibraryIdentity)throws {
  try document.validate(identity:identity)
  let bytes=try JSONEncoder().encode(Pending(identity:identity,document:document,games:games,collections:collections))
  guard bytes.count<=16*1024*1024 else{throw LibraryError("账号游戏库投影过大。")}
  try AccountLibraryDisk.write(bytes,to:store.root.appendingPathComponent("projection-pending.json"))
  try recover(store:store,identity:identity)
 }
}
