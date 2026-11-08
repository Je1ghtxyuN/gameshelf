import AppKit

@main struct AccountDiscoveryUITests {
 static func check(_ value:Bool,_ message:String){if !value{fatalError(message)}}
 static func views(_ root:NSView)->[NSView]{[root]+root.subviews.flatMap{views($0)}}
 static func wait(_ predicate:()->Bool){
  let end=Date().addingTimeInterval(5)
  while !predicate(),Date()<end{RunLoop.main.run(until:Date().addingTimeInterval(0.01))}
  check(predicate(),"Timed out waiting for fixture UI")
 }
 static func state(_ control:[String:Any]?=nil)throws->[String:Any]{
  var result:Result<Data,Error>?
  var request=URLRequest(url:URL(string:ProcessInfo.processInfo.environment["DISCOVERY_FIXTURE_URL"]!+(control==nil ? "/fixture/state":"/fixture/control"))!)
  if let control{request.httpMethod="POST";request.httpBody=try JSONSerialization.data(withJSONObject:control)}
  URLSession.shared.dataTask(with:request){data,_,error in
   result=error.map{.failure($0)} ?? .success(data!)
  }.resume()
  wait{result != nil}
  return try JSONSerialization.jsonObject(with:result!.get()) as! [String:Any]
 }
 static func main()throws {
  UserDefaults.standard.setVolatileDomain(["AutoOnlineCover":false],forName:UserDefaults.argumentDomain)
  _ = NSApplication.shared
  let app=App()
  let conn=SharedConnection(name:"Fixture",address:ProcessInfo.processInfo.environment["DISCOVERY_FIXTURE_URL"]!,serverID:"fixture-server",accountID:String(repeating:"a",count:32),username:"FixtureAdmin",isAdministrator:true)
  app.activeLibraryConnection=conn;app.sharedAccessTokens[conn.id]="fixture-token"
  var changes=0
  let controller=try AccountDiscoveryWindowController(app:app,connection:conn,changed:{changes+=1})
  let root=controller.window!.contentView!
  let table=views(root).compactMap{$0 as? NSTableView}.first!
  func button(_ title:String)->NSButton{views(root).compactMap{$0 as? NSButton}.first{$0.title==title}!}
  func checkbox(_ row:Int)->NSButton{controller.tableView(table,viewFor:table.tableColumns[0],row:row) as! NSButton}
  func click(_ control:NSControl){_ = control.target?.perform(control.action,with:control)}
  wait{table.numberOfRows==2 && button("刷新").isEnabled}
  check(controller.window?.title=="扫描游戏","Existing scan title")
  check(table.rowHeight==43 && table.usesAlternatingRowBackgroundColors,"Existing table presentation")
  check(try state()["scans"] as? Int==0,"Opening the window must not scan")
  let entryColumn=table.tableColumns.first{$0.identifier.rawValue=="entry"}!
  let entry=controller.tableView(table,viewFor:entryColumn,row:0) as! NSPopUpButton
  check(entry.numberOfItems==2,"Candidate editions remain selectable")
  entry.selectItem(at:1);click(entry)
  let platformColumn=table.tableColumns.first{$0.identifier.rawValue=="platform"}!
  let cell=controller.tableView(table,viewFor:platformColumn,row:0) as! NSTableCellView
  check(cell.textField?.stringValue=="macOS","Entry selection changes platform preview")
  let select=checkbox(0);select.state = .on;click(select)
  button("添加所选").performClick(nil)
  wait{changes==1 && button("刷新").isEnabled}
  let added=try state()["lastAdd"] as! [String:Any]
  check(added["userID"] as? String==conn.accountID,"Add targets the authenticated account")
  check(added["candidateIDs"] as? [String]==[String(repeating:"c",count:32)],"Add sends selected server candidate IDs")
  check((added["entryPaths"] as? [String:String])?[String(repeating:"c",count:32)]=="Game.app","Add must persist the chosen launcher, not only its preview")
  let modes=views(root).compactMap{$0 as? NSSegmentedControl}.first!
  modes.selectedSegment=1;click(modes)
  check(table.numberOfRows==2,"Added filter refreshes after adding")
  modes.selectedSegment=2;click(modes)
  check(table.numberOfRows==1,"Ignored state comes from server")
  let ignored=checkbox(0);ignored.state = .on;click(ignored)
  button("恢复所选").performClick(nil)
  wait{table.numberOfRows==0 && button("刷新").isEnabled}
  check((try state()["lastIgnore"] as? [String:Any])?["ignored"] as? Bool==false,"Restoring persists account ignore state")
  modes.selectedSegment=0;click(modes)
  let restored=checkbox(2);restored.state = .on;click(restored)
  button("忽略所选").performClick(nil)
  wait{table.numberOfRows==2 && button("刷新").isEnabled}
  button("扫描").performClick(nil)
  wait{button("刷新").isEnabled}
  check(try state()["scans"] as? Int==1,"Scan requires explicit action")
  // Complete an authorized add after switching; no callback or follow-up GET may escape.
  _ = try state(["resetCandidate":true])
  button("刷新").performClick(nil)
  wait{button("刷新").isEnabled}
  let old=checkbox(0);old.state = .on;click(old)
  button("添加所选").performClick(nil)
  var other=conn;other.id=UUID();other.accountID=String(repeating:"b",count:32)
  app.activeLibraryConnection=other
  wait{!button("刷新").isEnabled && table.numberOfRows==0}
  RunLoop.main.run(until:Date().addingTimeInterval(0.5))
  check(changes==1,"A switched account must not receive a stale add callback")
  check(try state()["adds"] as? Int==2,"Stale response test must actually reach add endpoint")
  controller.close()
  app.activeLibraryConnection=conn
  _ = try state(["resetCandidate":true])
  let closed=try AccountDiscoveryWindowController(app:app,connection:conn,changed:{changes+=1})
  let closeRoot=closed.window!.contentView!
  let closeTable=views(closeRoot).compactMap{$0 as? NSTableView}.first!
  let closeRefresh=views(closeRoot).compactMap{$0 as? NSButton}.first{$0.title=="刷新"}!
  wait{closeTable.numberOfRows==2 && closeRefresh.isEnabled}
  let closeCheck=closed.tableView(closeTable,viewFor:closeTable.tableColumns[0],row:0) as! NSButton
  closeCheck.state = .on;click(closeCheck)
  views(closeRoot).compactMap{$0 as? NSButton}.first{$0.title=="添加所选"}!.performClick(nil)
  closed.close()
  RunLoop.main.run(until:Date().addingTimeInterval(0.5))
  check(changes==1,"Closed windows must not invoke changed for late add success")
  check(try state()["adds"] as? Int==3,"Close-response test must actually reach add endpoint")
  closed.showWindow(nil)
  wait{closeRefresh.isEnabled}
  check(closeTable.numberOfRows==2,"Reopening a retained window reads saved candidates")
  closed.close()
  _ = try state(["emptyRoots":true])
  let empty=try AccountDiscoveryWindowController(app:app,connection:conn,changed:{changes+=1})
  empty.showWindow(nil)
  let emptyRoot=empty.window!.contentView!
  let emptyRefresh=views(emptyRoot).compactMap{$0 as? NSButton}.first{$0.title=="刷新"}!
  wait{emptyRefresh.isEnabled}
  check(views(emptyRoot).compactMap{$0 as? NSTextField}.contains{$0.stringValue=="请添加服务器文件夹。"},"Missing roots must expose configuration")
  check(views(emptyRoot).compactMap{$0 as? NSButton}.first{$0.title=="扫描"}?.isEnabled==false,"No root means no scan")
  views(emptyRoot).compactMap{$0 as? NSButton}.first{$0.title=="添加服务器文件夹…"}!.performClick(nil)
  wait{empty.window!.attachedSheet != nil}
  let sheet=empty.window!.attachedSheet!
  let fields=views(sheet.contentView!).compactMap{$0 as? NSTextField}
  fields.first{$0.placeholderString=="服务器文件夹路径"}!.stringValue="/srv/server-only-fixture"
  fields.first{$0.placeholderString=="名称（可选）"}!.stringValue="Server fixture"
  empty.window!.endSheet(sheet,returnCode:.alertFirstButtonReturn)
  wait{emptyRefresh.isEnabled && views(emptyRoot).compactMap{$0 as? NSPopUpButton}.first?.titleOfSelectedItem=="Server fixture"}
  check((try state()["lastRoot"] as? [String:Any])?["path"] as? String=="/srv/server-only-fixture","Root creation sends the typed server path")
  check(try state()["scans"] as? Int==1,"Adding a server root must not scan automatically")
  empty.close()
  var guest=conn;guest.isAdministrator=false
  app.activeLibraryConnection=guest
  let readonly=try AccountDiscoveryWindowController(app:app,connection:guest,changed:{changes+=1})
  let guestButtons=views(readonly.window!.contentView!).compactMap{$0 as? NSButton}
  check(!guestButtons.contains{$0.title=="添加服务器文件夹…"},"Non-administrators must not see root creation")
  check(guestButtons.first{$0.title=="刷新"}?.isEnabled==false,"Admin-only candidate endpoint must not be requested by guests")
  readonly.close()
  print("PASS account discovery UI: saved candidates without scan, table/entry selection, account add and refresh, filters, persisted ignore/restore, explicit scan, stale add isolation on switch/close, missing roots and server-path creation without auto-scan, guest permissions")
 }
}
