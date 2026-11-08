import Foundation

@main struct AccountLibraryMigrationTests {
 struct Failure:Error{let message:String}
 static func check(_ value:Bool,_ message:String)throws{if !value{throw Failure(message:message)}}
 static func rejects(_ body:()throws->Void)throws{do{try body()}catch is Failure{throw Failure(message:"unexpected assertion")}catch{return};throw Failure(message:"expected rejection")}
 static func main()throws {
  let fm=FileManager.default,base=URL(fileURLWithPath:"/private/tmp/AccountLibraryMigration-"+UUID().uuidString)
  try fm.createDirectory(at:base,withIntermediateDirectories:true);defer{try? fm.removeItem(at:base)}
  let owner=try AccountLibraryIdentity(serverID:"server",accountID:"owner"),guest=try AccountLibraryIdentity(serverID:"server",accountID:"guest")
  let binding=PersonalLibraryBinding(connectionID:UUID(),serverID:owner.serverID,accountID:owner.accountID)
  let a=base.appendingPathComponent("Games"),b=base.appendingPathComponent("Games2"),nested=a.appendingPathComponent("Special"),outside=base.appendingPathComponent("Games-Unconfigured")
  for path in [a,b,nested,outside]{try fm.createDirectory(at:path,withIntermediateDirectories:true)}
  let discovery=try DiscoveryStore(root:base.appendingPathComponent("Config"))
  _ = try discovery.register(path:a);_ = try discovery.register(path:b);_ = try discovery.register(path:nested)
  let ra=String(repeating:"a",count:32),rb=String(repeating:"b",count:32),rn=String(repeating:"c",count:32)
  let mappings=[AccountLibraryMigrationRoot(serverID:"server",rootID:ra,localPath:a.path),AccountLibraryMigrationRoot(serverID:"server",rootID:rb,localPath:b.path),AccountLibraryMigrationRoot(serverID:"server",rootID:rn,localPath:nested.path)]
  let roots:Set<String>=[ra,rb,rn]
  let exe=Candidate(path:"Game.exe",backend:.wine,engine:"Windows"),app=Candidate(path:".",backend:.native,engine:"macOS")
  var win=Game(title:"Same title",folder:a.appendingPathComponent("Win").path,originalFolder:a.appendingPathComponent("Win").path,entry:exe)
  var mac=Game(title:"Same title",folder:base.appendingPathComponent("NativeApps/Cache.app").path,originalFolder:b.appendingPathComponent("Mac.app").path,entry:app)
  let special=Game(title:"Same title",folder:nested.appendingPathComponent("Win").path,originalFolder:nested.appendingPathComponent("Win").path,entry:exe)
  let excluded=Game(title:"Same title",folder:outside.path,originalFolder:outside.path,entry:exe)
  var foreign=win;foreign.id=UUID();foreign.shared=SharedGameReference(connectionID:UUID(),serverID:"server",accountID:"guest",gameID:ra)
  func make(_ games:[Game],identity:AccountLibraryIdentity?=nil,maps:[AccountLibraryMigrationRoot]?=nil)throws->AccountLibraryMigrationPlan {
   try AccountLibraryMigrationPlan.make(owner:identity ?? owner,savedBinding:binding,games:games,sources:discovery.sources,mappings:maps ?? mappings,publicRootIDs:roots)
  }
  let plan=try make([win,mac,special,excluded,foreign])
  try check(plan.selections.count==3,"outside and foreign records must not migrate; got \(plan.selections.count), skips \(plan.skipped)")
  let w=plan.selections.first{$0.localID==win.id}!,m=plan.selections.first{$0.localID==mac.id}!,n=plan.selections.first{$0.localID==special.id}!
  try check(w.rootID==ra && w.relative=="Win" && w.entry==exe,"Windows tuple must retain exact root/directory/entry")
  try check(m.rootID==rb && m.relative=="Mac.app" && m.entry.path==".","Native cache must resolve to original .app root and dot entry")
  try check(n.rootID==rn && n.relative=="Win","nested sources must select the most specific root")
  try check(Set(plan.selections.map(\.selectionID)).count==3,"same-name unrelated locations must remain distinct")
  try check(plan.skipped.contains{$0.localID==excluded.id && $0.reason == .outsideConfiguredRoots},"prefix sibling must not count as contained")
  try rejects{_ = try make([win],identity:guest)}
  try rejects{_ = try AccountLibraryMigrationPlan.make(owner:owner,savedBinding:nil,games:[win],sources:discovery.sources,mappings:mappings,publicRootIDs:roots)}
  let otherServer=try AccountLibraryIdentity(serverID:"other-server",accountID:"owner")
  try rejects{_ = try make([win],identity:otherServer)}
  try check(try make([win],maps:[AccountLibraryMigrationRoot(serverID:"other-server",rootID:ra,localPath:a.path)]).selections.isEmpty,"foreign server mappings cannot be used")
  let missingNested=try make([special],maps:Array(mappings.prefix(2)))
  try check(missingNested.selections.isEmpty,"unmapped nested source must not fall back to another source root")
  try rejects{_ = try make([win],maps:mappings+[AccountLibraryMigrationRoot(serverID:"server",rootID:rb,localPath:a.path)])}
  try rejects{_ = try AccountLibraryMigrationPlan.make(owner:owner,savedBinding:binding,games:[win],sources:discovery.sources,mappings:mappings,publicRootIDs:[])}
  let raw=Data("[{\"serverID\":\"server\",\"rootID\":\"\(ra)\",\"localPath\":\"\(a.path)\"}]".utf8)
  try check(try JSONDecoder().decode([AccountLibraryMigrationRoot].self,from:raw).first?.rootID==ra,"legacy mapping file schema must remain readable")
  let rootApp=Game(title:"Root app",folder:b.appendingPathComponent("Mac.app").path,originalFolder:b.appendingPathComponent("Mac.app").path,entry:app)
  let rootAppSource=LibrarySource(title:"App",path:rootApp.originalFolder)
  let appPlan=try AccountLibraryMigrationPlan.make(owner:owner,savedBinding:binding,games:[rootApp],sources:[rootAppSource],mappings:[AccountLibraryMigrationRoot(serverID:"server",rootID:rb,localPath:rootAppSource.path)],publicRootIDs:roots)
  try check(appPlan.selections.first?.relative=="." && appPlan.selections.first?.entry.path==".","mapped app bundle root must preserve both dot paths")
  var unsafe=win;unsafe.id=UUID();unsafe.entry.path="../Game.exe"
  try check(try make([unsafe]).selections.isEmpty,"traversing entry must be skipped")
  var unsafeFolder=win;unsafeFolder.id=UUID();unsafeFolder.originalFolder=a.path+"/../Games/Win";unsafeFolder.folder=unsafeFolder.originalFolder
  try check(try make([unsafeFolder]).selections.isEmpty,"directory traversal must be skipped")
  let escape=a.appendingPathComponent("Escape")
  try fm.createSymbolicLink(at:escape,withDestinationURL:outside)
  var escaped=win;escaped.id=UUID();escaped.originalFolder=escape.path;escaped.folder=escape.path
  try check(try make([escaped]).selections.isEmpty,"symlink escape must not become a contained selection")
  try rejects{_ = try make([win,win])}
  var renamed=win;renamed.title="Unrelated new title"
  try check(try make([renamed]).selections.first?.selectionID==w.selectionID,"titles must never influence registration identity")
  let canonicalW=String(repeating:"d",count:32),canonicalM=String(repeating:"e",count:32),variantW=String(repeating:"f",count:32),variantM=String(repeating:"1",count:32)
  var doc=AccountLibraryDocument(schemaVersion:2,serverID:"server",user:AccountLibraryUser(id:"owner",username:"Owner",admin:true),games:[AccountLibraryGame(id:canonicalW,title:"Not a match key",revision:1,favorite:false,collectionIDs:[],playtimeSeconds:0,variants:[AccountLibraryVariant(id:variantW,platform:"windows",entries:[exe],available:true)]),AccountLibraryGame(id:canonicalM,title:"Not a match key",revision:1,favorite:false,collectionIDs:[],playtimeSeconds:0,variants:[AccountLibraryVariant(id:variantM,platform:"macos",entries:[app],available:true)])],collections:[])
  // Server selected-register legitimately re-probes the engine label. Physical
  // entry identity stays path + backend; the orchestrator verifies source SHA.
  doc.games[1].variants[0].entries[0].engine="renpy"
  let profile=try AccountLibraryProfile.open(under:base.appendingPathComponent("Profiles"),identity:owner,legacyOwner:nil)
  let manifest=try AccountLibraryMigrationManifest(profileRoot:profile,owner:owner,savedBinding:binding)
  let resolved=[AccountLibraryMigrationResolution(localID:win.id,selectionID:w.selectionID,canonicalID:canonicalW,variantID:variantW),AccountLibraryMigrationResolution(localID:mac.id,selectionID:m.selectionID,canonicalID:canonicalM,variantID:variantM)]
  let stale=try AccountLibraryMigrationManifest(profileRoot:profile,owner:owner,savedBinding:binding)
  let result=try manifest.validate(plan:plan,resolutions:resolved,document:doc)
  try check(result[win.id]==canonicalW && result[mac.id]==canonicalM && result.count==2,"validated acknowledgments must preserve local UUID and distinct canonical IDs")
  let file=profile.appendingPathComponent("game-links.json")
  try check(!fm.fileExists(atPath:file.path),"structural validation must not activate aliases")
  // Explicit unapproved raw fixture for structural reload/conflict tests only.
  try AccountLibraryDisk.write(JSONEncoder().encode(Dictionary(uniqueKeysWithValues:result.map{($0.key.uuidString,$0.value)})),to:file)
  let before=try Data(contentsOf:file)
  try check(try JSONDecoder().decode([String:String].self,from:before)[win.id.uuidString]==canonicalW,"manifest must match controller game-links format")
  let reopened=try AccountLibraryMigrationManifest(profileRoot:profile,owner:owner,savedBinding:binding)
  try check(try reopened.validate(plan:plan,resolutions:resolved,document:doc).count==2,"retry after reopening must not duplicate links")
  try check(try Data(contentsOf:file)==before,"identical retry must preserve durable bytes")
  let forged=AccountLibraryMigrationResolution(localID:win.id,selectionID:"wrong",canonicalID:canonicalW,variantID:variantW)
  try rejects{_ = try reopened.validate(plan:plan,resolutions:[forged],document:doc)}
  let wrongVariant=AccountLibraryMigrationResolution(localID:win.id,selectionID:w.selectionID,canonicalID:canonicalW,variantID:variantM)
  try rejects{_ = try reopened.validate(plan:plan,resolutions:[wrongVariant],document:doc)}
  let unknown=AccountLibraryMigrationResolution(localID:win.id,selectionID:w.selectionID,canonicalID:String(repeating:"2",count:32),variantID:variantW)
  try rejects{_ = try reopened.validate(plan:plan,resolutions:[unknown],document:doc)}
  var wrongPathDoc=doc;wrongPathDoc.games[1].variants[0].entries[0].path="Other.app"
  try rejects{_ = try reopened.validate(plan:plan,resolutions:[resolved[1]],document:wrongPathDoc)}
  var wrongBackendDoc=doc;wrongBackendDoc.games[1].variants[0].entries[0].backend = .wine
  try rejects{_ = try reopened.validate(plan:plan,resolutions:[resolved[1]],document:wrongBackendDoc)}
  var guestDoc=doc;guestDoc.user.id="guest"
  try rejects{_ = try reopened.validate(plan:plan,resolutions:resolved,document:guestDoc)}
  try rejects{_ = try AccountLibraryMigrationManifest(profileRoot:profile,owner:guest,savedBinding:binding)}
  try check(try Data(contentsOf:file)==before,"rejected mappings must leave manifest untouched")
  try rejects{_ = try reopened.validate(plan:plan,resolutions:[resolved[0],resolved[0]],document:doc)}
  let notInPlan=AccountLibraryMigrationResolution(localID:UUID(),selectionID:w.selectionID,canonicalID:canonicalW,variantID:variantW)
  try rejects{_ = try reopened.validate(plan:plan,resolutions:[notInPlan],document:doc)}
  let invalidID=AccountLibraryMigrationResolution(localID:win.id,selectionID:w.selectionID,canonicalID:"../invalid",variantID:variantW)
  try rejects{_ = try reopened.validate(plan:plan,resolutions:[invalidID],document:doc)}
  let canonicalN=String(repeating:"3",count:32),variantN=String(repeating:"4",count:32)
  var nextDoc=doc
  nextDoc.games.append(AccountLibraryGame(id:canonicalN,title:"Not a match key",revision:1,favorite:false,collectionIDs:[],playtimeSeconds:0,variants:[AccountLibraryVariant(id:variantN,platform:"windows",entries:[exe],available:true)]))
  let third=AccountLibraryMigrationResolution(localID:special.id,selectionID:n.selectionID,canonicalID:canonicalN,variantID:variantN)
  let merged=try stale.validate(plan:plan,resolutions:[third],document:nextDoc)
  try check(merged.count==3 && merged[win.id]==canonicalW && merged[special.id]==canonicalN,"a previously opened manifest must merge new links without losing another writer's committed IDs")
  let historyIDs=AccountLibraryHistoryLinks.make(visible:[win.id:canonicalW],aliases:[win.id.uuidString:canonicalW,mac.id.uuidString:canonicalW,special.id.uuidString:canonicalN,UUID().uuidString:String(repeating:"f",count:32)],document:nextDoc)
  try check(historyIDs[win.id]==canonicalW && historyIDs[mac.id]==canonicalW && historyIDs[special.id]==canonicalN && historyIDs.count==3,"Merged legacy cards must upload every alias's playtime, without accepting absent canonicals")
  let committed=try Data(contentsOf:file)
  let remap=AccountLibraryMigrationResolution(localID:win.id,selectionID:w.selectionID,canonicalID:canonicalN,variantID:variantN)
  try rejects{_ = try reopened.validate(plan:plan,resolutions:[remap],document:nextDoc)}
  try check(try Data(contentsOf:file)==committed,"conflicting canonical reassignment must fail without a partial write")
  let symlinkProfile=try AccountLibraryProfile.open(under:base.appendingPathComponent("SymlinkProfile"),identity:owner,legacyOwner:nil)
  try fm.createSymbolicLink(at:symlinkProfile.appendingPathComponent("game-links.json"),withDestinationURL:file)
  try rejects{_ = try AccountLibraryMigrationManifest(profileRoot:symlinkProfile,owner:owner,savedBinding:binding)}
  try check(try Data(contentsOf:file)==committed,"symlink manifest must not mutate its target")
  let attrs=try fm.attributesOfItem(atPath:file.path)
  try check((attrs[.posixPermissions] as? NSNumber)?.intValue==0o600,"manifest must be private")
  let configBefore=try Data(contentsOf:base.appendingPathComponent("Config/discovery.json"))
  _ = try make([win,mac])
  try check(try Data(contentsOf:base.appendingPathComponent("Config/discovery.json"))==configBefore,"planning must not rewrite sources")
  win.favorite=true;mac.favorite=true // Metadata is deliberately not part of the registration tuple.
  print("PASS AccountLibraryMigration: exact owner binding, multiple/nested roots and prefix containment, original native app/dot entry, platform variants, no title merge, unsafe/foreign skips, native engine-label upgrade with exact path/backend, read-only structural links and idempotent validation")
 }
}
