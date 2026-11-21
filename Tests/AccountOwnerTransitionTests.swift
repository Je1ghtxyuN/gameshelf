import Foundation
import CryptoKit

@main struct AccountOwnerTransitionTests {
 static func main()async throws {
  struct Failure:Error{}
  func check(_ value:Bool,_ text:String)throws{if !value{throw LibraryError(text)}}
  func rejects(_ work:()throws->Void)throws{do{try work()}catch{return};throw LibraryError("Expected transition refusal")}
  func sha(_ bytes:Data)->String{SHA256.hash(data:bytes).map{String(format:"%02x",$0)}.joined()}
  let fm=FileManager.default,root=URL(fileURLWithPath:"/private/tmp/OwnerTransition-"+UUID().uuidString)
  try fm.createDirectory(at:root,withIntermediateDirectories:true);defer{try? fm.removeItem(at:root)}
  let owner=try AccountLibraryIdentity(serverID:"server",accountID:"owner")
  let binding=PersonalLibraryBinding(connectionID:UUID(),serverID:"server",accountID:"owner")
  let profile=try AccountLibraryProfile.open(under:root,identity:owner,legacyOwner:nil)
  let game=Game(title:"Original unchanged",folder:"/fixture/original",originalFolder:"/fixture/original",entry:Candidate(path:"Game.exe",backend:.renpy,engine:"RenPy"))
  let expected=AccountOwnerTransitionBinding(serverID:"server",accountID:"owner",localID:game.id.uuidString,selectionID:String(repeating:"a",count:64),canonicalID:String(repeating:"b",count:32),variantID:String(repeating:"c",count:32),candidateID:String(repeating:"d",count:32),rootID:String(repeating:"e",count:32),relative:"Exact/Game",sourceSignature:String(repeating:"f",count:64),reviewedProfileID:"reviewed-renpy",profileVariantID:"windows",profileRevision:1,rulesSHA256:String(repeating:"1",count:64),evidenceSHA256:String(repeating:"2",count:64),sourceInventorySHA256:String(repeating:"3",count:64),mappingSHA256:String(repeating:"4",count:64),namespaceKey:"server/owner/"+String(repeating:"b",count:32)+"/"+String(repeating:"5",count:64))
  let receipt=AccountOwnerTransitionPayload(binding:expected,schemaVersion:1,kind:"owner-save-migration",status:"applied",operationID:String(repeating:"6",count:32),targetInventorySHA256:String(repeating:"7",count:64),importOperationID:String(repeating:"8",count:64))
  let receiptURL=profile.appendingPathComponent("owner-adoption.json")
  let receiptSHA=String(repeating:"9",count:64)
  func write(_ payload:AccountOwnerTransitionPayload)throws->Data {
   let encoded=try JSONEncoder().encode(payload),object=try JSONSerialization.jsonObject(with:encoded)
   let bytes=try JSONSerialization.data(withJSONObject:["payload":object,"receiptSHA256":receiptSHA,"signature":["algorithm":"hmac-sha256","keyID":"coordinator-only","mac":String(repeating:"a",count:64)]],options:[.sortedKeys])
   try AccountLibraryDisk.write(bytes,to:receiptURL);return bytes
  }
  let bytes=try write(receipt)
  // This private committed ledger simulates the TRUSTED internal bridge, not a
  // caller-approved Boolean or authority derived from the submitted envelope.
  let ledger=profile.appendingPathComponent("fixture-server-committed.json")
  func commitLedger(_ payload:AccountOwnerTransitionPayload,_ envelope:Data)throws {
   let verified=AccountOwnerTransitionConfirmation(payload:payload,receiptSHA256:receiptSHA,envelopeSHA256:sha(envelope))
   try AccountLibraryDisk.write(JSONEncoder().encode(verified),to:ledger)
  }
  var targetChanged=false
  let gate=try AccountOwnerTransitionGate(profileRoot:profile,owner:owner,savedBinding:binding,verifyCommittedReceipt:{operation in
   guard !targetChanged,operation==receipt.operationID,fm.fileExists(atPath:ledger.path) else{throw Failure()}
   return try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))
  })
  try gate.stage(original:game,expected:expected)
  try check(!fm.fileExists(atPath:profile.appendingPathComponent("game-links.json").path),"metadata stage must never activate aliases")
  try rejects{_ = try gate.authorize(original:game,receiptURL:receiptURL)}
  try commitLedger(receipt,bytes)
  let permit=try gate.authorize(original:game,receiptURL:receiptURL)
  try check(permit.localID==game.id && permit.canonicalID==expected.canonicalID,"committed exact receipt should authorize original UUID only")
  let noVerifier=try AccountOwnerTransitionGate(profileRoot:profile,owner:owner,savedBinding:binding)
  try rejects{_ = try noVerifier.authorize(original:game,receiptURL:receiptURL)}
  let unknown=Game(title:"Unknown rules",folder:"/fixture/unknown",originalFolder:"/fixture/unknown",entry:game.entry)
  try gate.stage(original:unknown,expected:nil)
  try rejects{_ = try gate.authorize(original:unknown,receiptURL:receiptURL)}
  var moved=game;moved.originalFolder="/fixture/changed"
  try rejects{_ = try gate.authorize(original:moved,receiptURL:receiptURL)}
  var wrong=expected;wrong.variantID=String(repeating:"d",count:32)
  var wrongPayload=receipt;wrongPayload.binding=wrong
  let wrongBytes=try write(wrongPayload);try commitLedger(wrongPayload,wrongBytes)
  try rejects{_ = try gate.authorize(original:game,receiptURL:receiptURL)}
  var rolledBack=receipt;rolledBack.status="rolled-back"
  let rollbackBytes=try write(rolledBack);try commitLedger(rolledBack,rollbackBytes)
  try rejects{_ = try gate.authorize(original:game,receiptURL:receiptURL)}
  _ = try write(receipt) // ledger still describes another durable receipt.
  try rejects{_ = try gate.authorize(original:game,receiptURL:receiptURL)}
  try commitLedger(receipt,bytes)
  let reopened=try AccountOwnerTransitionGate(profileRoot:profile,owner:owner,savedBinding:binding,verifyCommittedReceipt:{_ in try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))})
  try check(try reopened.authorize(original:game,receiptURL:receiptURL).receiptSHA256==receiptSHA,"durable stage/receipt retry must be stable")
  let guest=try AccountLibraryIdentity(serverID:"server",accountID:"guest")
  try rejects{_ = try AccountOwnerTransitionGate(profileRoot:profile,owner:guest,savedBinding:binding)}
  let actual=profile.appendingPathComponent("actual-receipt.json")
  try fm.moveItem(at:receiptURL,to:actual);try fm.createSymbolicLink(at:receiptURL,withDestinationURL:actual)
  try rejects{_ = try gate.authorize(original:game,receiptURL:receiptURL)}
  try check(game.folder=="/fixture/original" && game.shared==nil,"original path/source must stay unchanged")
  try check(!fm.fileExists(atPath:profile.appendingPathComponent("game-links.json").path),"approval check alone must not write aliases")
  // The wrapper itself must preflight committed adoption before alias writes.
  try fm.removeItem(at:receiptURL);try fm.moveItem(at:actual,to:receiptURL)
  let rootID=expected.rootID
  let plan=try AccountLibraryMigrationPlan.make(owner:owner,savedBinding:binding,games:[game],sources:[LibrarySource(title:"Fixture",path:"/fixture")],mappings:[AccountLibraryMigrationRoot(serverID:"server",rootID:rootID,localPath:"/fixture")],publicRootIDs:[rootID])
  let selected=plan.selections[0]
  var applyExpected=expected;applyExpected.relative=selected.relative;applyExpected.selectionID=selected.selectionID
  try gate.stage(original:game,expected:applyExpected)
  var applyReceipt=receipt;applyReceipt.binding=applyExpected
  let applyBytes=try write(applyReceipt)
  let resolution=AccountLibraryMigrationResolution(localID:game.id,selectionID:selected.selectionID,canonicalID:expected.canonicalID,variantID:expected.variantID)
  let document=AccountLibraryDocument(schemaVersion:2,serverID:owner.serverID,user:AccountLibraryUser(id:owner.accountID,username:"Owner",admin:true),games:[AccountLibraryGame(id:expected.canonicalID,title:game.title,revision:0,favorite:false,collectionIDs:[],playtimeSeconds:0,variants:[AccountLibraryVariant(id:expected.variantID,platform:"windows",entries:[game.entry],available:true)])],collections:[])
  try rejects{_ = try gate.applyApproved(plan:plan,resolutions:[resolution],document:document,originals:[game],receiptURLs:[game.id:receiptURL])}
  try check(!fm.fileExists(atPath:profile.appendingPathComponent("game-links.json").path),"uncommitted receipt must not write aliases")
  try commitLedger(applyReceipt,applyBytes)
  let links=try gate.applyApproved(plan:plan,resolutions:[resolution],document:document,originals:[game],receiptURLs:[game.id:receiptURL])
  try check(links[game.id]==expected.canonicalID && links.count==1,"verified wrapper must preserve local UUID")
  try check(try gate.applyApproved(plan:plan,resolutions:[resolution],document:document,originals:[game],receiptURLs:[game.id:receiptURL])==links,"committed alias retries must not duplicate")
  // Simulate legitimate later gameplay; initial live verification now refuses.
  let target=profile.appendingPathComponent("fixture-save-after-gameplay")
  try Data("new legitimate progress".utf8).write(to:target)
  targetChanged=true
  try rejects{_ = try gate.authorize(original:game,receiptURL:receiptURL)}
  // A live-inventory verifier must not be used after legitimate gameplay.
  let aliasesBefore=try Data(contentsOf:profile.appendingPathComponent("game-links.json"))
  try rejects{_ = try AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:owner,savedBinding:binding)}
  let historical=try AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:owner,savedBinding:binding,verifyHistoricalReceipt:{operation in
   guard operation==applyReceipt.operationID else{throw Failure()}
   return try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))
  })
  try check(try Data(contentsOf:target)==Data("new legitimate progress".utf8),"history auth must leave post-gameplay saves unchanged")
  try check(historical==links,"already accepted alias must survive later gameplay using immutable historical auth")
  try check(try Data(contentsOf:profile.appendingPathComponent("game-links.json"))==aliasesBefore,"history load must never replay a save or alias snapshot")
  let foreignProfile=try AccountLibraryProfile.open(under:root,identity:guest,legacyOwner:nil)
  try AccountLibraryDisk.write(aliasesBefore,to:foreignProfile.appendingPathComponent("game-links.json"))
  try rejects{_ = try AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:foreignProfile,owner:guest,savedBinding:binding,verifyHistoricalReceipt:{_ in try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))})}
  var forgedLinks=links;forgedLinks[UUID()]=expected.canonicalID
  try AccountLibraryDisk.write(JSONEncoder().encode(Dictionary(uniqueKeysWithValues:forgedLinks.map{($0.key.uuidString,$0.value)})),to:profile.appendingPathComponent("game-links.json"))
  try rejects{_ = try AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:owner,savedBinding:binding,verifyHistoricalReceipt:{_ in try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))})}
  try AccountLibraryDisk.write(aliasesBefore,to:profile.appendingPathComponent("game-links.json"))
  let unverifiedProfile=try AccountLibraryProfile.open(under:root.appendingPathComponent("Unverified"),identity:owner,legacyOwner:nil)
  try AccountLibraryDisk.write(aliasesBefore,to:unverifiedProfile.appendingPathComponent("game-links.json"))
  try rejects{_ = try AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:unverifiedProfile,owner:owner,savedBinding:binding,verifyHistoricalReceipt:{_ in try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))})}
  let baselineURL=profile.appendingPathComponent("owner-transition-accepted.json")
  let baselineBefore=try Data(contentsOf:baselineURL)
  var baselineObject=try JSONSerialization.jsonObject(with:baselineBefore) as! [String:Any]
  baselineObject["mac"]=Data(repeating:0,count:32).base64EncodedString()
  try AccountLibraryDisk.write(JSONSerialization.data(withJSONObject:baselineObject),to:baselineURL)
  try rejects{_ = try AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:owner,savedBinding:binding,verifyHistoricalReceipt:{_ in try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))})}
  try AccountLibraryDisk.write(baselineBefore,to:baselineURL)
  try rejects{_ = try AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:owner,savedBinding:binding,verifyHistoricalReceipt:{_ in throw Failure()})}
  let newManifest=try AccountLibraryMigrationManifest(profileRoot:profile,owner:owner,savedBinding:binding)
  try rejects{_ = try newManifest.apply(plan:plan,resolutions:[resolution],document:document,permits:[])}
  // Two-step adoption: old target changed by gameplay, only NEW adoption live.
  let second=Game(title:"Second",folder:"/fixture/second",originalFolder:"/fixture/second",entry:game.entry)
  let secondPlan=try AccountLibraryMigrationPlan.make(owner:owner,savedBinding:binding,games:[second],sources:[LibrarySource(title:"Fixture",path:"/fixture")],mappings:[AccountLibraryMigrationRoot(serverID:"server",rootID:rootID,localPath:"/fixture")],publicRootIDs:[rootID])
  let secondSelection=secondPlan.selections[0]
  var secondExpected=applyExpected
  secondExpected.localID=second.id.uuidString;secondExpected.selectionID=secondSelection.selectionID;secondExpected.relative=secondSelection.relative
  secondExpected.canonicalID=String(repeating:"0",count:32);secondExpected.variantID=String(repeating:"1",count:32)
  secondExpected.namespaceKey="server/owner/"+secondExpected.canonicalID+"/"+String(repeating:"5",count:64)
  var secondReceipt=applyReceipt;secondReceipt.binding=secondExpected;secondReceipt.operationID=String(repeating:"2",count:32)
  let firstReceiptURL=profile.appendingPathComponent("first-accepted-receipt.json")
  try AccountLibraryDisk.write(applyBytes,to:firstReceiptURL)
  let secondBytes=try write(secondReceipt)
  let secondConfirmation=AccountOwnerTransitionConfirmation(payload:secondReceipt,receiptSHA256:receiptSHA,envelopeSHA256:sha(secondBytes))
  var liveOperations:[String]=[],historyOperations:[String]=[]
  let secondGate=try AccountOwnerTransitionGate(profileRoot:profile,owner:owner,savedBinding:binding,verifyCommittedReceipt:{operation in
   liveOperations.append(operation)
   guard operation==secondReceipt.operationID else{throw Failure()}
   return secondConfirmation
  },verifyHistoricalReceipt:{operation in
   historyOperations.append(operation)
   guard operation==applyReceipt.operationID else{throw Failure()}
   return try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))
  })
  try secondGate.stage(original:second,expected:secondExpected)
  var secondDocument=document
  secondDocument.games.append(AccountLibraryGame(id:secondExpected.canonicalID,title:second.title,revision:0,favorite:false,collectionIDs:[],playtimeSeconds:0,variants:[AccountLibraryVariant(id:secondExpected.variantID,platform:"windows",entries:[second.entry],available:true)]))
  let secondResolution=AccountLibraryMigrationResolution(localID:second.id,selectionID:secondSelection.selectionID,canonicalID:secondExpected.canonicalID,variantID:secondExpected.variantID)
  let both=try secondGate.applyApproved(plan:secondPlan,resolutions:[secondResolution],document:secondDocument,originals:[game,second],receiptURLs:[game.id:firstReceiptURL,second.id:receiptURL])
  try check(both.count==2 && both[game.id]==expected.canonicalID && both[second.id]==secondExpected.canonicalID,"later adoption must retain history-authenticated played alias")
  try check(!liveOperations.isEmpty && liveOperations.allSatisfy{$0==secondReceipt.operationID} && historyOperations.count>=2,"only new aliases may use live verification; old aliases must revalidate history at commit")
  try check(try Data(contentsOf:target)==Data("new legitimate progress".utf8),"second adoption must not replay old target snapshots")
  try check(try AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:owner,savedBinding:binding,verifyHistoricalReceipt:{operation in
   if operation==secondReceipt.operationID{return secondConfirmation}
   guard operation==applyReceipt.operationID else{throw Failure()}
   return try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))
  })==both,"both accepted aliases must reload with immutable history auth")
  let acceptedFirst=try JSONDecoder().decode(AccountOwnerTransitionConfirmation.self,from:Data(contentsOf:ledger))
  let fetched=try await AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:owner,savedBinding:binding,fetchHistoricalReceipt:{operation in
   await Task.yield()
   if operation==secondReceipt.operationID{return secondConfirmation}
   guard operation==applyReceipt.operationID else{throw Failure()}
   return acceptedFirst
  })
  try check(fetched==both,"async historical verification must preserve both existing aliases")
  do {
   _ = try await AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:owner,savedBinding:binding,fetchHistoricalReceipt:{operation in
    if operation==secondReceipt.operationID{return secondConfirmation}
    return AccountOwnerTransitionConfirmation(payload:acceptedFirst.payload,receiptSHA256:acceptedFirst.receiptSHA256,envelopeSHA256:String(repeating:"0",count:64))
   })
   throw LibraryError("Mismatched envelope digest passed")
  } catch let error as LibraryError where error.localizedDescription == "Accepted adoption history changed." {}
  let linksURL=profile.appendingPathComponent("game-links.json")
  let bothBytes=try Data(contentsOf:linksURL)
  do {
   _ = try await AccountOwnerTransitionGate.loadApprovedAliases(profileRoot:profile,owner:owner,savedBinding:binding,fetchHistoricalReceipt:{operation in
    try AccountLibraryDisk.write(Data("{}".utf8),to:linksURL)
    return operation==secondReceipt.operationID ? secondConfirmation:acceptedFirst
   })
   throw LibraryError("Alias snapshot changed across await but passed")
  } catch let error as LibraryError where error.localizedDescription == "Accepted adoption history changed." {}
  try AccountLibraryDisk.write(bothBytes,to:linksURL)
  print("PASS AccountOwnerTransition: metadata-only staging, unknown-rules preservation, trusted COMMITTED receipt only, exact identity/profile/fingerprint binding, rollback/forgery/symlink refusal and durable retries")
 }
}
