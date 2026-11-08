import AppKit
import Darwin

/// Desired-behavior regressions against an isolated snapshot of the actual App.
/// The loopback service orchestrates lost ACKs, permission revocation and a held
/// refresh response. No installed App startup or game launch is involved.
@main struct MacIntegrationReviewTests {
 @MainActor static func main()throws {
  setbuf(stdout,nil)
  UserDefaults.standard.setVolatileDomain(["AutoOnlineCover":false],forName:UserDefaults.argumentDomain)
  _ = NSApplication.shared
  let app=App()
  guard app.root.path.hasPrefix("/private/tmp/"),let address=ProcessInfo.processInfo.environment["ACCOUNT_REVIEW_URL"] else{fatalError("Temporary review fixture required")}
  func check(_ value:Bool,_ message:String){if !value{fatalError(message)}}
  func wait(_ ready:()->Bool){let deadline=Date().addingTimeInterval(10);while !ready(),Date()<deadline{RunLoop.main.run(until:Date().addingTimeInterval(0.01))};check(ready(),"Review fixture timed out")}
  func spin(_ seconds:Double=0.3){RunLoop.main.run(until:Date().addingTimeInterval(seconds))}
  func control(_ body:[String:Any]=[:])throws->[String:Any]{
   var result:Result<Data,Error>?
   var req=URLRequest(url:URL(string:address+"/review/control")!);req.httpMethod="POST";req.httpBody=try JSONSerialization.data(withJSONObject:body)
   URLSession.shared.dataTask(with:req){data,_,error in result=error.map{.failure($0)} ?? .success(data!)}.resume()
   wait{result != nil}
   return try JSONSerialization.jsonObject(with:result!.get()) as! [String:Any]
  }
  app.store=try LibraryStore(root:app.root.appendingPathComponent("SignedOut"))
  app.window=NSWindow(contentRect:NSRect(x:0,y:0,width:1180,height:780),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
  let conn=SharedConnection(name:"Review fixture",address:address,serverID:"fixture-server",accountID:"owner",username:"Owner",isAdministrator:true)
  try SharedConnectionStore(root:app.root).save(conn)
  func login(){app.didAuthenticateAccount(connection:conn,token:"owner-token",rememberLogin:false);wait{app.accountLibrary?.active==true};spin()}
  login()
  check(app.store.games.count==2,"fixture must load both real projected games")
  let id=app.store.games.first{$0.title=="Server game"}!.id
  let laterID=app.store.games.first{$0.title=="Later game"}!.id
  try app.playtime!.begin(gameID:id,at:Date(timeIntervalSince1970:1000),uptime:0)
  try app.playtime!.end(gameID:id,at:Date(timeIntervalSince1970:1010),uptime:10)
  app.accountLibrary!.queueClosedSessions()
  // Enqueue A first, then another game, without relying on dictionary order.
  try app.playtime!.begin(gameID:laterID,at:Date(timeIntervalSince1970:2000),uptime:20)
  try app.playtime!.end(gameID:laterID,at:Date(timeIntervalSince1970:2007),uptime:27)
  app.accountLibrary!.queueClosedSessions()
  check(app.accountLibrary!.displayedSeconds(for:id)==133,"unsubmitted duration should appear once before any POST")
  check(app.accountLibrary!.displayedSeconds(for:laterID)==57,"later unsubmitted duration should appear once")
  _ = try control(["revoked":true])
  app.accountLibrary!.refresh()
  wait{!app.store.games.contains{$0.id==id}}
  spin()
  let revoked=try control()
  check(revoked["timePosts"] as! Int>0,"pending event must reach permission-denied route")
  check(!app.store.games.contains{$0.id==id},"403 time upload must not prevent authoritative removal")
  check(app.store.games.contains{$0.id==laterID},"unrelated authorized game must remain visible")
  check(revoked["laterBase"] as! Double==57,"later queued game's event must upload after the earlier 403")
  let trace=revoked["timeTrace"] as! [[String:Any]]
  check(trace.count>=2 && trace[0]["canonical"] as? String==String(repeating:"a",count:32) && trace[0]["status"] as? Int==403 && trace[1]["canonical"] as? String==String(repeating:"c",count:32) && trace[1]["status"] as? Int==200,"later success must actually follow the rejected event in the same queue pass")
  print("PASS R1: rejected time event does not block authoritative projection or the later game's upload")
  app.requestAccountSwitch()
  _ = try control(["revoked":false,"dropNextTimeACK":true,"holdTime":true])
  login();spin(0.5)
  let dropped=try control()
  check(dropped["base"] as! Double==133,"server must accept exactly the 10-second event before dropping ACK")
  app.requestAccountSwitch();login();spin(0.5)
  check(app.accountLibrary!.document!.games.first{$0.id==String(repeating:"a",count:32)}?.playtimeSeconds==133,"new login must read accepted server total")
  let displayed=app.accountLibrary!.displayedSeconds(for:id)!
  check(displayed==133,"accepted event with lost ACK must not be added to the freshly fetched total again")
  let queueFile=app.store.root.appendingPathComponent("playtime-outbox.json")
  let queue=try JSONSerialization.jsonObject(with:Data(contentsOf:queueFile)) as! [String:Any]
  let pending=queue["pending"] as! [[String:Any]]
  check(pending.count==1 && pending[0]["attempted"] as? Bool==true,"submission uncertainty must survive relogin durably")
  print("PASS R2: accepted event/lost ACK/relogin displays authoritative 133, with attempted status persisted")
  app.requestAccountSwitch()
  var legacyQueue=queue
  legacyQueue["pending"]=pending.map{row -> [String:Any] in var row=row;row.removeValue(forKey:"attempted");return row}
  try JSONSerialization.data(withJSONObject:legacyQueue).write(to:queueFile,options:.atomic)
  let oldQueue=try AccountPlaytimeOutbox(root:queueFile.deletingLastPathComponent(),identity:AccountLibraryIdentity(serverID:conn.serverID,accountID:conn.accountID),deviceID:"review-fixture")
  check(oldQueue.pending.count==1 && oldQueue.unsubmitted.isEmpty,"unknown status must be excluded before any retry can mark it attempted")
  login();spin()
  check(app.accountLibrary!.displayedSeconds(for:id)==133,"older queue entries without attempted status must remain conservative")
  print("PASS legacy queue: unknown submission status is not optimistically double-counted")
  _ = try control(["holdTime":false])
  app.accountLibrary!.refresh();spin(0.5)
  let acknowledgedQueue=try JSONSerialization.jsonObject(with:Data(contentsOf:queueFile)) as! [String:Any]
  check((acknowledgedQueue["pending"] as! [[String:Any]]).isEmpty,"successful retry must durably acknowledge the event")
  check(app.accountLibrary!.displayedSeconds(for:id)==133,"successful idempotent retry must clear pending optimistic addition")
  _ = try control(["holdFinalLibrary":true])
  app.accountLibrary!.refresh()
  var held=false
  for _ in 0..<100{held=try control()["held"] as! Bool;if held{break};spin(0.02)}
  check(held,"refresh's final old library snapshot must be held in flight")
  var removed=false
  app.accountLibrary!.removeGame(id){removed=true}
  wait{removed}
  check(try control()["revoked"] as! Bool,"DELETE must succeed on the server")
  _ = try control(["release":true])
  wait{!app.store.games.contains{$0.id==id}}
  spin()
  check(!app.store.games.contains{$0.id==id},"old response must not restore the removed card")
  check(app.store.games.contains{$0.id==laterID},"DELETE must not remove another account game")
  check(try control()["holdReads"] as! Int>=6,"server mutation must queue a follow-up refresh after the held response")
  print("PASS R3: DELETE invalidates old refresh and coalesces an authoritative follow-up projection")
  app.accountLibrary?.close();app.sharedLibraryWindow?.close();app.window.close()
  print("PASS actual App regression suite: R1/R2/R3, later event after 403, pre-POST durable uncertainty, and conservative legacy queue")
 }
}
