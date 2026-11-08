import Foundation
import CryptoKit

struct AccountLoginRecord:Codable {
 var connectionID:UUID
 var identity:AccountLibraryIdentity
 var token:String
}
/// The user explicitly disabled Keychain prompts. Remember only the revocable
/// bearer session in a private 0600 file; never persist the login password.
final class AccountLoginStore {
 private let file:URL
 init(root:URL)throws{try AccountLibraryDisk.directory(root);file=root.appendingPathComponent("account-session.json")}
 func load()throws->AccountLoginRecord? {
  guard let bytes=try AccountLibraryDisk.regularData(file,limit:16384) else{return nil}
  let attr=try FileManager.default.attributesOfItem(atPath:file.path)
  guard let mode=attr[.posixPermissions] as? NSNumber,mode.intValue&0o077==0 else{throw LibraryError("登录记录权限不安全，请重新登录。")}
  let record=try JSONDecoder().decode(AccountLoginRecord.self,from:bytes);try validate(record);return record
 }
 func save(_ record:AccountLoginRecord)throws {
  try validate(record);try AccountLibraryDisk.write(JSONEncoder().encode(record),to:file)
 }
 /// Called only after the current controller has authenticated a new session.
 func forgetRememberedLogin()throws {
  guard try load() != nil else{return}
  try FileManager.default.removeItem(at:file)
 }
 func clear(identity:AccountLibraryIdentity)throws {
  guard let existing=try load(),existing.identity==identity else{return}
  try FileManager.default.removeItem(at:file)
 }
 private func validate(_ record:AccountLoginRecord)throws {
  _ = try AccountLibraryIdentity(serverID:record.identity.serverID,accountID:record.identity.accountID)
  guard !record.token.isEmpty,record.token.utf8.count<=4096,!record.token.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains) else{throw LibraryError("登录记录无效，未使用。")}
 }
}

struct AccountPlaytimeEvent:Codable,Equatable {
 var eventID:UUID
 var sessionID:UUID
 var canonicalID:String
 var deviceID:String
 var elapsedSeconds:Double
 var endedAt:Double
 // nil denotes an older queue entry whose submission status is uncertain.
 var attempted:Bool?=nil
 var body:[String:Any]{["eventID":eventID.uuidString,"deviceID":deviceID,"elapsedSeconds":elapsedSeconds,"endedAt":endedAt]}
}
final class AccountPlaytimeOutbox {
 private struct Record:Codable {var version=1;var identity:AccountLibraryIdentity;var pending:[AccountPlaytimeEvent]=[];var uploadedSessions:Set<UUID>=[]}
 private let file:URL
 private let deviceID:String
 private var state:Record
 var pending:[AccountPlaytimeEvent]{state.pending}
 var unsubmitted:[AccountPlaytimeEvent]{state.pending.filter{$0.attempted==false}}
 init(root:URL,identity:AccountLibraryIdentity,deviceID:String)throws {
  _ = try AccountLibraryCache(root:root,identity:identity)
  guard !deviceID.isEmpty,deviceID.utf8.count<=128 else{throw LibraryError("设备标识无效。")}
  self.deviceID=deviceID;file=root.appendingPathComponent("playtime-outbox.json")
  if let bytes=try AccountLibraryDisk.regularData(file,limit:8*1024*1024){state=try JSONDecoder().decode(Record.self,from:bytes)}else{state=Record(identity:identity)}
  guard state.version==1,state.identity==identity,state.pending.count<=10000,state.uploadedSessions.count<=100000,Set(state.pending.map(\.eventID)).count==state.pending.count else{throw LibraryError("游玩记录同步队列归属或格式无效。")}
  for event in state.pending {try Self.validate(event)}
 }
 func enqueue(session:PlaySession,canonicalID:String)throws {
  guard let ended=session.endedAt,!state.uploadedSessions.contains(session.id),!state.pending.contains(where:{$0.sessionID==session.id}) else{return}
  guard session.durationSeconds.isFinite,session.durationSeconds>=0,session.durationSeconds<=86400*10000,ended.timeIntervalSince1970.isFinite else{throw LibraryError("游玩时长无效，原记录已保留。")}
  let count=max(1,Int(ceil(session.durationSeconds/86400)))
  guard state.pending.count+count<=10000 else{throw LibraryError("游玩记录等待同步，请连接服务器后重试。")}
  var next=state;var remaining=session.durationSeconds
  for index in 0..<count {
   let key=SHA256.hash(data:Data((state.identity.storageKey+session.id.uuidString+":"+String(index)).utf8)).map{String(format:"%02x",$0)}.joined()
   let id=UUID(uuidString:"\(key.prefix(8))-\(key.dropFirst(8).prefix(4))-\(key.dropFirst(12).prefix(4))-\(key.dropFirst(16).prefix(4))-\(key.dropFirst(20).prefix(12))")!
   let event=AccountPlaytimeEvent(eventID:id,sessionID:session.id,canonicalID:canonicalID,deviceID:deviceID,elapsedSeconds:min(remaining,86400),endedAt:ended.timeIntervalSince1970,attempted:false)
   try Self.validate(event);next.pending.append(event);remaining=max(0,remaining-86400)
  }
  try commit(next)
 }
 func markAttempted(eventID:UUID)throws {
  guard let index=state.pending.firstIndex(where:{$0.eventID==eventID}),state.pending[index].attempted != true else{return}
  var next=state;next.pending[index].attempted=true;try commit(next)
 }
 func acknowledge(eventID:UUID)throws {
  guard let event=state.pending.first(where:{$0.eventID==eventID}) else{return}
  var next=state;next.pending.removeAll{$0.eventID==eventID};next.uploadedSessions.insert(event.sessionID)
  try commit(next)
 }
 private static func validate(_ event:AccountPlaytimeEvent)throws {
  guard event.canonicalID.count==32,event.canonicalID.utf8.allSatisfy({(48...57).contains($0)||(97...102).contains($0)}),!event.deviceID.isEmpty,event.deviceID.utf8.count<=128,event.elapsedSeconds.isFinite,(0...86400).contains(event.elapsedSeconds),event.endedAt.isFinite,(0...253402300799).contains(event.endedAt) else{throw LibraryError("游玩记录同步内容无效。")}
 }
 private func commit(_ next:Record)throws {
  let bytes=try JSONEncoder().encode(next);guard bytes.count<=8*1024*1024 else{throw LibraryError("游玩记录同步队列过大，原记录已保留。")}
  try AccountLibraryDisk.write(bytes,to:file);state=next
 }
}
