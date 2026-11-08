import AppKit

/// Build with App's Bootstrap removed, then pass --data-dir pointing to a fresh
/// temporary fixture directory. This never uses the installed application.
@main struct MacAccountUITests {
 static func check(_ value:Bool,_ message:String){if !value{fatalError(message)}}
 static func descendants(_ view:NSView)->[NSView]{[view]+view.subviews.flatMap{descendants($0)}}
 static func main()throws {
  UserDefaults.standard.setVolatileDomain(["AutoOnlineCover":false],forName:UserDefaults.argumentDomain)
  _ = NSApplication.shared
  check(CommandLine.arguments.contains("--data-dir"),"UI tests require an explicit fixture root")
  let app=App()
  check(app.root.path.hasPrefix("/private/tmp/"),"UI tests are confined to temporary fixtures")
  app.store=try LibraryStore(root:app.root)
  app.window=NSWindow(contentRect:NSRect(x:0,y:0,width:1180,height:780),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
  let owner=SharedConnection(name:"Fixture Server",address:"https://fixture.invalid",serverID:"fixture-server",accountID:"fixture-owner",username:"Owner must not prefill",isAdministrator:true)
  let connections=try SharedConnectionStore(root:app.root)
  try connections.save(owner)
  app.sharedAccessTokens[owner.id]="fixture-memory-token"
  // An already displayed owner view must disappear when login is requested.
  app.window.contentView=NSTextField(labelWithString:"PRIVATE OWNER LIBRARY")
  app.window.orderFront(nil)
  app.showAccountLogin()
  check(!app.window.isVisible,"Main window must be hidden during login")
  check(app.window.contentView?.subviews.isEmpty==true,"Owner content must be removed before login")
  guard let login=app.sharedLibraryWindow,let root=login.window?.contentView else{fatalError("Login missing")}
  root.layoutSubtreeIfNeeded()
  var views=descendants(root)
  check(views.contains{$0 is SharedLoginArtwork},"Existing blue artwork must be retained")
  let username=views.compactMap{$0 as? NSTextField}.first{$0.placeholderString=="账号"}
  check(username?.stringValue=="","Even an in-memory owner session must not prefill another login")
  let remember=views.compactMap{$0 as? NSButton}.first{$0.title=="保持登录"}
  check(remember?.state == .on,"Remember login defaults to enabled")
  let secure=views.compactMap{$0 as? NSSecureTextField}.first
  secure?.stringValue="fixture password"
  login.accountLibraryLoadFailed(LibraryError("Fixture library load failed"))
  views=descendants(root)
  check(secure?.stringValue=="","Load failure must clear the password")
  check(views.compactMap{$0 as? NSButton}.first{$0.title=="登录"}?.isEnabled==true,"Load failure must allow retry")
  check(views.compactMap{$0 as? NSTextField}.contains{$0.stringValue=="Fixture library load failed"},"Load failure should be visible")
  check(!app.window.isVisible,"Failure must not reveal the owner library")
  login.openAccount(connectionID:owner.id)
  views=descendants(root)
  let titles=views.compactMap{($0 as? NSButton)?.title}
  check(titles.contains("修改密码") && titles.contains("账号管理"),"Administrator account actions remain available")
  check(!titles.contains(where:{$0.contains("绑定") || $0.contains("共享库")}),"Account settings must not reintroduce binding or legacy catalog")
  check(!views.contains{$0 is SharedLoginArtwork},"Authenticated account settings open directly")
  login.close()
  let guest=SharedConnection(name:"Fixture Server",address:"https://fixture.invalid",serverID:"fixture-server",accountID:"fixture-guest",username:"Guest",isAdministrator:false)
  try connections.save(guest);app.sharedAccessTokens[guest.id]="fixture-guest-token"
  let guestWindow=try SharedLibraryWindowController(app:app)
  guestWindow.openAccount(connectionID:guest.id)
  let guestTitles=descendants(guestWindow.window!.contentView!).compactMap{($0 as? NSButton)?.title}
  check(!guestTitles.contains("账号管理"),"Guest must not see user management")
  check(guestTitles.contains("切换账号") && guestTitles.contains("退出登录"),"Account switch and logout must remain reachable")
  guestWindow.close()
  print("PASS Mac account UI: hidden owner window, blue login, neutral username, remember default, failed-load retry, administrator/guest account actions")
 }
}
