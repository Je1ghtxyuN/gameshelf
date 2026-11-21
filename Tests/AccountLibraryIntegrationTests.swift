import AppKit

@main struct AccountLibraryIntegrationTests {
 @MainActor static func main() throws {
  _ = NSApplication.shared
  UserDefaults.standard.setVolatileDomain(["AutoOnlineCover":false],forName:UserDefaults.argumentDomain)
  let app=App()
  guard app.root.path.hasPrefix("/private/tmp/"),let address=ProcessInfo.processInfo.environment["ACCOUNT_FIXTURE_URL"] else{fatalError("Temporary loopback fixture required")}
  func check(_ condition:Bool,_ message:String){if !condition{fatalError(message)}}
  func scanButtons(_ view:NSView)->[NSButton]{(view as? NSButton).map{[$0].filter{$0.title=="扫描游戏"}} ?? view.subviews.flatMap{scanButtons($0)}}
  func wait(_ ready:()->Bool){let end=Date().addingTimeInterval(12);while !ready() && Date()<end{RunLoop.main.run(until:Date().addingTimeInterval(0.02))};check(ready(),"Timed out waiting for account transition")}
  let legacy=try LibraryStore(root:app.root)
  let secret=Game(title:"Legacy private record",folder:"/fixture/private",originalFolder:"/fixture/private",entry:Candidate(path:"Game.exe",backend:.wine,engine:"fixture"))
  legacy.games=[secret];try legacy.save()
  app.store=try LibraryStore(root:app.root.appendingPathComponent("SignedOut"))
  app.window=NSWindow(contentRect:NSRect(x:0,y:0,width:1180,height:780),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
  let owner=SharedConnection(name:"Fixture",address:address,serverID:"fixture-server",accountID:"owner",username:"Owner",isAdministrator:true)
  let guest=SharedConnection(name:"Fixture",address:address,serverID:"fixture-server",accountID:"guest",username:"Guest",isAdministrator:false)
  let connections=try SharedConnectionStore(root:app.root);try connections.save(owner);try connections.save(guest)
  app.showAccountLogin();app.didAuthenticateAccount(connection:owner,token:"owner-token",rememberLogin:true)
  wait{app.accountLibrary?.active==true}
  check(!scanButtons(app.window.contentView!).isEmpty,"Administrator retains the scan entry")
  check(app.store.games.map(\.title)==["Server game"],"Server account library only; unbound legacy record must not leak")
  check(app.window.isVisible,"Library appears only after validated account load")
  var edited=app.store.games[0];edited.favorite=true;try app.store.update(edited)
  app.accountLibrary?.refresh()
  wait{app.accountLibrary?.document?.games.first?.favorite==true}
  let ownerProfile=app.store.root
  check(try AccountLoginStore(root:app.root).load()?.identity.accountID=="owner","Remembered identity is owner")
  // Bind an exact old native edition after the signed-in library is active.
  // The lookup hook observes App.launch's real branch without starting a game.
  let original=Game(title:"Legacy native",folder:"/fixture/native",originalFolder:"/fixture/native",
                    entry:Candidate(path:"Game.app",backend:.native,engine:"macOS"))
  legacy.games.append(original);try legacy.save()
  let preservedBytes=try Data(contentsOf:app.root.appendingPathComponent("library.json"))
  _ = try PersonalLibraryBindingStore(root:app.root).bind(to:owner)
  let identity=try AccountLibraryIdentity(serverID:owner.serverID,accountID:owner.accountID)
  let binding=AccountOwnerNativeLaunchBinding(owner:identity,localID:original.id,
      canonicalID:String(repeating:"a",count:32),variantID:String(repeating:"b",count:32),
      entryPath:original.entry.path,folder:original.folder,originalFolder:original.originalFolder)
  try AccountLibraryDisk.write(JSONEncoder().encode([binding]),to:app.root.appendingPathComponent("owner-native-launch-bindings.json"))
  let card=app.store.games[0]
  var lookup:URL?, launchError:String?
  app.errorPresenter={launchError=$0.localizedDescription}
  app.nativeApplicationLookup={lookup=$0;return NSRunningApplication.current}
  app.launch(card)
  check(lookup?.lastPathComponent==original.id.uuidString+".app","Account launch must reuse the original native runtime identity")
  check(app.nativeApplications[card.id] != nil && app.nativeApplications[original.id]==nil,
        "Process tracking remains tied to the account card")
  check(launchError != nil,"An already-running process with unverified save settings remains blocked")
  app.nativeApplications.removeAll();app.unverifiedNativeApplications.removeAll()
  app.requestAccountSwitch()
  check(!app.window.isVisible && app.store.games.isEmpty,"Switch must remove prior account view and records")
  app.didAuthenticateAccount(connection:guest,token:"guest-token",rememberLogin:true)
  wait{app.accountLibrary?.active==true}
  check(app.store.games.isEmpty && app.store.root != ownerProfile,"Fresh guest never inherits owner game list")
  check(scanButtons(app.window.contentView!).isEmpty,"Guest must not receive a nonfunctional scan button or empty-state scan action")
  check(try Data(contentsOf:app.root.appendingPathComponent("library.json"))==preservedBytes,"Legacy library remains byte-for-byte untouched")
  check(try AccountLoginStore(root:app.root).load()?.identity.accountID=="guest","Remember session follows active guest")
  app.accountLibrary?.close();app.sharedLibraryWindow?.close();app.window.close()
  let adoption=App();var adoptionErrors:[String]=[]
  adoption.errorPresenter={adoptionErrors.append($0.localizedDescription)}
  adoption.store=try LibraryStore(root:app.root.appendingPathComponent("AdoptionFixture"))
  adoption.nativeApplicationLookup={_ in NSRunningApplication.current}
  var oldGame=Game(title:"Existing harmless process",folder:app.root.appendingPathComponent("Inert.app").path,originalFolder:"",entry:Candidate(path:".",backend:.native,engine:"fixture"))
  oldGame.shared=SharedGameReference(connectionID:UUID(),serverID:"fixture-server",accountID:"owner",gameID:String(repeating:"a",count:32))
  adoption.startNativeApplication(oldGame)
  check(adoptionErrors.count==1 && adoption.unverifiedNativeApplications.contains(oldGame.id),"Unknown account process must not be adopted with new save parameters")
  check(adoption.nativeApplications[oldGame.id] != nil,"Unverified live writer must remain tracked until it exits")
  adoption.startNativeApplication(oldGame)
  check(adoptionErrors.count==2,"A second click must not activate the unverified process")
  adoption.nativeApplications.removeAll();adoption.unverifiedNativeApplications.removeAll()
  var inventoryCalls=0
  adoption.nativeApplicationLookup={_ in inventoryCalls+=1;return inventoryCalls>2 ? NSRunningApplication.current:nil}
  adoption.startNativeApplication(oldGame)
  wait{adoptionErrors.count==3}
  check(adoption.unverifiedNativeApplications.contains(oldGame.id),"A process appearing during failed preparation must retain writer tracking")
  adoption.nativeApplications.removeAll();adoption.unverifiedNativeApplications.removeAll()
  print("PASS actual App integration: validated login, owner native launch routing, empty guest, remember identity and untouched legacy library")
 }
}
