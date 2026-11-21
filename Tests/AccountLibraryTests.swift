import Foundation

@main struct AccountLibraryTests {
 static func main() throws {
  struct Failure:Error {let message:String}
  func check(_ value:Bool,_ message:String)throws{if !value{throw Failure(message:message)}}
  func rejects(_ body:()throws->Void)throws{do{try body();throw Failure(message:"expected rejection")}catch is Failure{throw Failure(message:"expected rejection")}catch{}}
  let sid=String(repeating:"a",count:32),aid=String(repeating:"b",count:32),gid=String(repeating:"c",count:32)
  let owner=try AccountLibraryIdentity(serverID:sid,accountID:aid)
  let other=try AccountLibraryIdentity(serverID:sid,accountID:String(repeating:"d",count:32))
  let win=AccountLibraryVariant(id:String(repeating:"1",count:32),platform:"windows",version:"16",entries:[Candidate(path:"config.exe",backend:.wine,engine:"Unity"),Candidate(path:"Play.exe",backend:.wine,engine:"Unity")],saveCompatibilityKey:nil,available:true)
  let mac=AccountLibraryVariant(id:String(repeating:"2",count:32),platform:"macos",version:"16",entries:[Candidate(path:"Phoenixes.app",backend:.native,engine:"Ren’Py")],saveCompatibilityKey:nil,available:true)
  var game=AccountLibraryGame(id:gid,title:"Phoenixes",revision:0,favorite:false,collectionIDs:[],playtimeSeconds:1234,lastPlayed:nil,coverSHA256:nil,variants:[win,mac])
  var document=AccountLibraryDocument(schemaVersion:2,serverID:sid,user:AccountLibraryUser(id:aid,username:"Alice",admin:true),games:[game],collections:[])
  try document.validate(identity:owner)
  var combined=document
  var sharedEdition=mac;sharedEdition.id=win.id
  combined.games[0].variants=[win,sharedEdition]
  try combined.validate(identity:owner)
  var family=combined
  family.directNas=true
  family.games[0].variants[1].nasSource=AccountLibraryNASSource(rootID:sid,relative:"Games/Example.app")
  try family.validate(identity:owner)
  let decoded=try JSONDecoder().decode(AccountLibraryDocument.self,from:JSONEncoder().encode(family))
  try check(decoded.games[0].variants[1].nasSource==family.games[0].variants[1].nasSource,"NAS source lost in round trip")
  family.directNas=false
  try rejects{try family.validate(identity:owner)}
  family.directNas=true
  for path in ["", "/outside", "game/../outside", "game//outside", "game:/outside"] {
   family.games[0].variants[1].nasSource?.relative=path
   try rejects{try family.validate(identity:owner)}
  }
  try rejects{try document.validate(identity:other)}
  try check(try game.selection(platform:"macos").variant.id==mac.id,"Mac did not prefer native")
  try check(try game.selection(platform:"windows").entry.path=="Play.exe","Windows selected helper or macOS")
  var preferred=win;preferred.entries.append(Candidate(path:"Play_cn.exe",backend:.wine,engine:"Unity"))
  var preferredGame=game;preferredGame.variants=[preferred,mac]
  preferredGame.preferredEntries=["windows":AccountLibraryPreferredEntry(variantID:win.id,entryPath:"Play_cn.exe")]
  try check(try preferredGame.selection(platform:"windows").entry.path=="Play_cn.exe","Windows account launcher preference ignored")
  try check(try preferredGame.selection(platform:"macos").variant.id==mac.id,"Wine preference overrode native Mac default")
  preferredGame.variants=[preferred]
  try check(try preferredGame.selection(platform:"macos").entry.path=="Play_cn.exe","Mac Wine fallback ignored account language launcher choice")
  var badPreference=document;badPreference.games[0].preferredEntries=["windows":AccountLibraryPreferredEntry(variantID:win.id,entryPath:"../other.exe")]
  try rejects{try badPreference.validate(identity:owner)}
  game.variants=[win];try check(try game.selection(platform:"macos").variant.id==win.id,"Wine fallback unavailable")
  game.variants=[mac];try rejects{_ = try game.selection(platform:"windows")}
  game.variants=[AccountLibraryVariant(id:win.id,platform:"windows",version:nil,entries:[Candidate(path:"../outside.exe",backend:.wine,engine:"Windows")],saveCompatibilityKey:nil,available:true)]
  try rejects{_ = try game.selection(platform:"windows")}
  var unsafeWineFallback=win
  unsafeWineFallback.entries=[
   Candidate(path:"unsafe./Play.exe",backend:.renpy,engine:"Ren’Py"),
   Candidate(path:"web.exe",backend:.web,engine:"WebKit"),
   Candidate(path:"Safe.exe",backend:.renpy,engine:"Ren’Py")
  ]
  game.variants=[unsafeWineFallback]
  try check(try game.selection(platform:"macos").entry.path=="Safe.exe","Mac Wine fallback accepted a Windows-unsafe path or a web backend")
  document.games.append(document.games[0]);try rejects{try document.validate(identity:owner)};document.games.removeLast()
  let fm=FileManager.default,root=fm.temporaryDirectory.appendingPathComponent("AccountLibrary-"+UUID().uuidString)
  try fm.createDirectory(at:root,withIntermediateDirectories:true);defer{try? fm.removeItem(at:root)}
  let legacy=try LibraryStore(root:root)
  let local=Game(title:"Owner original",folder:"/network/game",originalFolder:"/network/game",entry:Candidate(path:"Play.exe",backend:.wine,engine:"Windows"),favorite:true)
  let foreign=Game(title:"Other user",folder:"/other",originalFolder:"remote",entry:local.entry,shared:SharedGameReference(connectionID:UUID(),serverID:sid,accountID:other.accountID,gameID:gid))
  legacy.games=[local,foreign];try legacy.save()
  let group=try legacy.createCollection(name:"RPG");try legacy.setCollections(for:local.id,ids:[group.id])
  let original=try Data(contentsOf:root.appendingPathComponent("library.json"))
  let friends=try AccountLibraryProfile.open(under:root,identity:other,legacyOwner:owner)
  try check(try LibraryStore(root:friends).games.isEmpty,"friend inherited owner library")
  let profile=try AccountLibraryProfile.open(under:root,identity:owner,legacyOwner:owner)
  try check(try LibraryStore(root:profile).games.map(\.id)==[local.id],"owner migration failed or foreign records copied")
  try check(try Data(contentsOf:root.appendingPathComponent("library.json"))==original,"source library changed")
  try check(fm.fileExists(atPath:profile.deletingLastPathComponent().appendingPathComponent("MigrationBackup/library.json").path),"migration backup missing")
  let accountStore=try LibraryStore(root:profile)
  try accountStore.replaceAccountLibrary(games:[local],collections:[GameCollection(id:group.id,name:"RPG",gameIDs:[local.id])])
  try check(try LibraryStore(root:profile).collections.first?.gameIDs==[local.id],"account collection replacement not durable")
  try rejects{try legacy.replaceAccountLibrary(games:[],collections:[])}
  let cache=try AccountLibraryCache(root:profile,identity:owner)
  try cache.save(document)
  try check(try cache.load()?.games.count==1,"account cache not reusable on reload")
  try rejects{try AccountLibraryCache(root:profile,identity:other).save(document)}
  try Data("corrupt".utf8).write(to:profile.appendingPathComponent("account-library.json"))
  try rejects{_ = try cache.load()}
  try check(try Data(contentsOf:profile.appendingPathComponent("account-library.json"))==Data("corrupt".utf8),"corrupt cache was overwritten")
  let projectionRoot=try AccountLibraryProfile.open(under:root,identity:try AccountLibraryIdentity(serverID:sid,accountID:"projection-fixture"),legacyOwner:nil)
  let projectionIdentity=try AccountLibraryIdentity(serverID:sid,accountID:"projection-fixture")
  let projectionStore=try LibraryStore(root:projectionRoot)
  var projectionDocument=document;projectionDocument.user.id=projectionIdentity.accountID
  let obstruction=projectionRoot.appendingPathComponent("collections.json")
  try fm.createDirectory(at:obstruction,withIntermediateDirectories:false)
  try rejects{try AccountLibraryProjection.commit(projectionDocument,games:[local],collections:[],store:projectionStore,identity:projectionIdentity)}
  try check(fm.fileExists(atPath:projectionRoot.appendingPathComponent("projection-pending.json").path),"interrupted projection journal lost")
  try rejects{try AccountLibraryProjection.recover(store:projectionStore,identity:other)}
  try fm.removeItem(at:obstruction)
  try AccountLibraryProjection.recover(store:projectionStore,identity:projectionIdentity)
  try check(projectionStore.games.map(\.id)==[local.id],"interrupted projection did not recover local cache")
  try check(try AccountLibraryCache(root:projectionRoot,identity:projectionIdentity).load()?.games.first?.title==document.games.first?.title,"projection baseline not recovered with local cache")
  try check(!fm.fileExists(atPath:projectionRoot.appendingPathComponent("projection-pending.json").path),"committed projection journal retained")
  print("PASS AccountLibrary: identity validation, platform/entry selection, account separation, owner-only migration, backup, durable cache and corruption preservation")
 }
}
