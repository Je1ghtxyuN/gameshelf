import Foundation

@main struct AccountLibraryPersistenceTests {
 static func main()throws {
  struct Failure:Error {var message:String}
  func check(_ value:Bool,_ message:String)throws{if !value{throw Failure(message:message)}}
  func rejects(_ body:()throws->Void)throws{do{try body();throw Failure(message:"expected failure")}catch is Failure{throw Failure(message:"expected failure")}catch{}}
  let fm=FileManager.default,root=fm.temporaryDirectory.appendingPathComponent("AccountLibraryPersistence-"+UUID().uuidString)
  try fm.createDirectory(at:root,withIntermediateDirectories:true);defer{try? fm.removeItem(at:root)}
  let identity=try AccountLibraryIdentity(serverID:"server",accountID:"owner")
  let friend=try AccountLibraryIdentity(serverID:"server",accountID:"friend")
  let login=try AccountLoginStore(root:root)
  try check(try login.load()==nil,"unexpected saved login")
  let record=AccountLoginRecord(connectionID:UUID(),identity:identity,token:"opaque-session-token")
  try login.save(record)
  try check(try AccountLoginStore(root:root).load()?.identity==identity,"login not retained")
  let attrs=try fm.attributesOfItem(atPath:root.appendingPathComponent("account-session.json").path)
  try check((attrs[.posixPermissions] as? NSNumber)?.intValue==0o600,"session permissions not private")
  try login.clear(identity:friend)
  try check(try login.load() != nil,"wrong account cleared current login")
  try login.clear(identity:identity);try check(try login.load()==nil,"logout did not clear login")
  try login.save(AccountLoginRecord(connectionID:UUID(),identity:identity,token:"another-owner-session"))
  try login.forgetRememberedLogin();try check(try login.load()==nil,"new unremembered login left previous owner auto-login active")
  let profile=try AccountLibraryProfile.open(under:root,identity:identity,legacyOwner:nil)
  let outbox=try AccountPlaytimeOutbox(root:profile,identity:identity,deviceID:"device-a")
  let localID=UUID(),canonical=String(repeating:"a",count:32)
  let closed=PlaySession(id:UUID(),gameID:localID,startedAt:Date(timeIntervalSince1970:1),endedAt:Date(timeIntervalSince1970:120001),durationSeconds:120000,interrupted:false)
  try outbox.enqueue(session:closed,canonicalID:canonical)
  try check(outbox.pending.count==2,"long session was not split to bounded events")
  let first=outbox.pending[0]
  try check(outbox.unsubmitted.count==2,"fresh events should be distinguishable from uncertain receipts")
  try outbox.markAttempted(eventID:first.eventID)
  try check(outbox.unsubmitted.count==1,"uncertain receipt was still added to authoritative display total")
  try outbox.enqueue(session:closed,canonicalID:canonical);try check(outbox.pending.count==2,"session duplicated on retry")
  let reload=try AccountPlaytimeOutbox(root:profile,identity:identity,deviceID:"device-a")
  try check(reload.unsubmitted.count==1,"attempt uncertainty was lost on restart")
  try check(reload.pending.map(\.eventID)==outbox.pending.map(\.eventID),"retry event identities changed after restart")
  try reload.acknowledge(eventID:first.eventID)
  try check(reload.pending.count==1,"ack did not commit")
  try reload.enqueue(session:closed,canonicalID:canonical);try check(reload.pending.count==1,"acknowledged session requeued")
  try rejects{_ = try AccountPlaytimeOutbox(root:profile,identity:friend,deviceID:"device-a")}
  let live=PlaySession(id:UUID(),gameID:localID,startedAt:Date(),endedAt:nil,durationSeconds:10,interrupted:false)
  try outbox.enqueue(session:live,canonicalID:canonical);try check(outbox.pending.count==2,"non-final session was uploaded as final")
  print("PASS AccountLibraryPersistence: private remembered session, identity-bound logout, durable idempotent bounded time outbox and account isolation")
 }
}
