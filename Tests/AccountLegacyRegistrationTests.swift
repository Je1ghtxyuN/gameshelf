import Foundation

@main struct AccountLegacyRegistrationTests {
 @MainActor static func main()async throws {
  struct State:Decodable {let calls:[String]}
  func check(_ value:Bool,_ message:String)throws{if !value{throw LibraryError(message)}}
  guard let address=ProcessInfo.processInfo.environment["LEGACY_FIXTURE_URL"],let path=ProcessInfo.processInfo.environment["LEGACY_FIXTURE_ROOT"] else{fatalError("Loopback fixture required")}
  let root=URL(fileURLWithPath:path),folder=root.appendingPathComponent("Games/Title")
  let owner=try AccountLibraryIdentity(serverID:String(repeating:"a",count:32),accountID:String(repeating:"b",count:32))
  let binding=PersonalLibraryBinding(connectionID:UUID(),serverID:owner.serverID,accountID:owner.accountID)
  let source=LibrarySource(title:"Games",path:root.appendingPathComponent("Games").path)
  let mapping=AccountLibraryMigrationRoot(serverID:owner.serverID,rootID:String(repeating:"c",count:32),localPath:source.path)
  let game=Game(title:"Private original",folder:folder.path,originalFolder:folder.path,entry:Candidate(path:"Game.exe",backend:.wine,engine:"Windows"))
  let store=try LibraryStore(root:root.appendingPathComponent("original"));store.games=[game];try store.save()
  let original=try Data(contentsOf:store.root.appendingPathComponent("library.json"))
  let plan=try AccountLibraryMigrationPlan.make(owner:owner,savedBinding:binding,games:[game],sources:[source],mappings:[mapping],publicRootIDs:[mapping.rootID])
  let api=try SharedAPI(address:address,certificateSHA256:nil)
  let registrar=AccountLegacyRegistration(identity:owner,api:api,token:"fixture-owner")
  let result=try await registrar.stage(plan:plan,mappings:[mapping])
  try check(result.resolutions.count==1 && result.resolutions[0].canonicalID==String(repeating:"d",count:32),"Existing source identity was not retained")
  let state:State=try await api.value("/api/fixture/state")
  guard let link=state.calls.firstIndex(where:{$0.hasSuffix("/variants")}),let add=state.calls.firstIndex(of:"/api/admin/library/add") else{throw LibraryError("Missing staged orchestration calls")}
  try check(link<add,"Preferences/grants were applied before the empty edition was linked")
  try check(try Data(contentsOf:store.root.appendingPathComponent("library.json"))==original,"Staging changed original client records before save migration")
  try check(!FileManager.default.fileExists(atPath:store.root.appendingPathComponent("game-links.json").path),"Staging prematurely activated aliases")
  for mode in ["fingerprint-fail","ambiguous"] {
   let _:State=try await api.value("/api/fixture/reset",method:"POST",body:["mode":mode])
   var failed=false;do{_ = try await registrar.stage(plan:plan,mappings:[mapping])}catch{failed=true}
   try check(failed,"Invalid migration confirmation was accepted")
   let after:State=try await api.value("/api/fixture/state")
   try check(!after.calls.contains(where:{$0.hasSuffix("/publish") || $0=="/api/admin/library/add"}),"Failed preflight still published/granted a game")
  }
  print("PASS legacy registration: exact fingerprint/source identity, old canonical retained, link before preferences, ambiguous/rejected preflight preserves original records and aliases")
 }
}
