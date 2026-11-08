import AppKit

/// Account-specific state layered on the existing server scan candidate schema.
private struct AccountDiscoveryCandidate:Decodable {
 let candidate:SharedAdminCandidate
 let added:Bool
 let ignored:Bool
 private enum CodingKeys:String,CodingKey{case added,ignored}
 init(from decoder:Decoder)throws {
  candidate=try SharedAdminCandidate(from:decoder)
  let values=try decoder.container(keyedBy:CodingKeys.self)
  added=try values.decode(Bool.self,forKey:.added)
  ignored=try values.decode(Bool.self,forKey:.ignored)
 }
}
private struct AccountDiscoveryCandidates:Decodable {let candidates:[AccountDiscoveryCandidate]}
private struct AccountDiscoveryMutation:Decodable {}

/// Server-owned discovery only. No local directory enumeration or NAS mounting.
final class AccountDiscoveryWindowController:NSWindowController,NSWindowDelegate,NSTableViewDataSource,NSTableViewDelegate {
 private weak var app:App?
 private let connection:SharedConnection
 private let changed:()->Void
 private let api:SharedAPI
 private let token:String
 private let sources=NSPopUpButton()
 private let modes=NSSegmentedControl(labels:["全部","已加入","已忽略"],trackingMode:.selectOne,target:nil,action:nil)
 private let table=NSTableView()
 private let status=NSTextField(labelWithString:"")
 private let spinner=NSProgressIndicator()
 private var roots:[SharedAdminRoot]=[]
 private var candidates:[AccountDiscoveryCandidate]=[]
 private var records:[AccountDiscoveryCandidate]=[]
 private var checked=Set<String>()
 private var selectedEntries:[String:Candidate]=[:]
 private var buttons:[NSButton]=[]
 private var scanAction:NSButton?
 private var addAction:NSButton?
 private var ignoreAction:NSButton?
 private var operation=UUID()
 private var busy=false
 private var closed=false
 private var invalidated=false
 private var identityTimer:Timer?

 init(app:App,connection:SharedConnection,changed:@escaping()->Void)throws {
  guard let token=try app.sharedToken(for:connection),!token.isEmpty else{throw LibraryError("请重新登录。")}
  self.app=app;self.connection=connection;self.changed=changed;self.token=token
  api=try SharedAPI(address:connection.address,certificateSHA256:connection.certificateSHA256)
  let window=NSWindow(contentRect:NSRect(x:0,y:0,width:980,height:610),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
  super.init(window:window)
  window.title="扫描游戏";window.minSize=NSSize(width:860,height:480);window.isReleasedWhenClosed=false;window.delegate=self;window.appearance=NSAppearance(named:.aqua)
  build();window.center()
  watchIdentity()
  if !current{invalidateAccount()}
  else if connection.isAdministrator{refresh()}
  else{status.stringValue="需要管理员账号。";updateControls()}
 }
 override func showWindow(_ sender:Any?){
  if closed,!invalidated {
   closed=false
   if current {
    watchIdentity()
    if connection.isAdministrator{busy=false;refresh()}
   }else{invalidateAccount()}
  }
  super.showWindow(sender)
 }
 private func watchIdentity(){
  identityTimer?.invalidate()
  identityTimer=Timer(timeInterval:0.25,repeats:true){[weak self] _ in
   guard let self else{return};if !self.current{self.invalidateAccount()}
  }
  if let identityTimer{RunLoop.main.add(identityTimer,forMode:.common)}
 }
 required init?(coder:NSCoder){fatalError()}
 deinit{identityTimer?.invalidate()}
 func windowWillClose(_ notification:Notification){
  closed=true;operation=UUID();identityTimer?.invalidate();identityTimer=nil
  if let window,let sheet=window.attachedSheet{window.endSheet(sheet,returnCode:.cancel)}
 }
 private var current:Bool {
  guard !closed,!invalidated,let app,let active=app.activeLibraryConnection,
        active.id==connection.id,active.serverID==connection.serverID,active.accountID==connection.accountID,
        active.isAdministrator==connection.isAdministrator,
        let live=try? app.sharedToken(for:connection) else{return false}
  return live==token
 }
 private var enabled:Bool{current && connection.isAdministrator && !busy}
 private var source:SharedAdminRoot?{let i=sources.indexOfSelectedItem;return roots.indices.contains(i) ? roots[i]:nil}
 private func invalidateAccount(){
  guard !closed,!invalidated else{return}
  invalidated=true;operation=UUID();busy=false;identityTimer?.invalidate();identityTimer=nil
  roots=[];candidates=[];records=[];checked=[];selectedEntries=[:];sources.removeAllItems()
  if let window,let sheet=window.attachedSheet{window.endSheet(sheet,returnCode:.cancel)}
  status.stringValue="账号已切换，请重新打开扫描窗口。";table.reloadData();updateControls()
 }
 private func valid(_ id:UUID)->Bool {
  guard !closed,operation==id else{return false}
  guard current else{invalidateAccount();return false}
  return true
 }
 private func button(_ title:String,_ action:@escaping()->Void)->NSButton{let b=ActionButton(title,action);buttons.append(b);return b}
 private func build(){
  guard let app,let content=window?.contentView else{return}
  let title=app.label("扫描游戏",size:24,weight:.semibold)
  sources.target=self;sources.action=#selector(sourceChanged);sources.setContentCompressionResistancePriority(.defaultLow,for:.horizontal)
  sources.setAccessibilityLabel("服务器游戏文件夹")
  var sourceViews:[NSView]=[sources]
  if connection.isAdministrator{sourceViews.append(button("添加服务器文件夹…"){[weak self] in self?.addRoot()})}
  let scan=button("扫描"){[weak self] in self?.scan()};scanAction=scan
  sourceViews += [scan,button("刷新"){[weak self] in self?.refresh()}]
  let sourceRow=app.stack(sourceViews,spacing:10)
  modes.selectedSegment=0;modes.target=self;modes.action=#selector(filterChanged)
  spinner.style = .spinning;spinner.controlSize = .small;spinner.isDisplayedWhenStopped=false
  status.maximumNumberOfLines=2;status.lineBreakMode = .byWordWrapping;status.textColor = .secondaryLabelColor;status.font = .systemFont(ofSize:12)
  let filterRow=app.stack([modes,NSView(),app.stack([spinner,status],spacing:8)],spacing:16)
  let scroll=NSScrollView();scroll.hasVerticalScroller=true;scroll.hasHorizontalScroller=false;scroll.borderType = .noBorder;scroll.documentView=table
  table.delegate=self;table.dataSource=self;table.rowHeight=43;table.usesAlternatingRowBackgroundColors=true;table.selectionHighlightStyle = .none;table.intercellSpacing=NSSize(width:10,height:0);table.columnAutoresizingStyle = .lastColumnOnlyAutoresizingStyle
  for (id,name,width) in [("check","",28.0),("title","游戏",220.0),("version","版本",115.0),("platform","平台",85.0),("language","语言",85.0),("entry","启动入口",300.0)] {
   let column=NSTableColumn(identifier:NSUserInterfaceItemIdentifier(id));column.title=name;column.width=width;column.minWidth=id=="check" ? 28:65
   column.resizingMask=id=="entry" ? [.autoresizingMask,.userResizingMask]:id=="check" ? []:[.userResizingMask];table.addTableColumn(column)
  }
  let ignore=button("忽略所选"){[weak self] in self?.ignoreSelected()};ignoreAction=ignore
  let add=button("添加所选"){[weak self] in self?.addSelected()};addAction=add
  let footer=app.stack([NSView(),ignore,add],spacing:10)
  let layout=app.stack([title,sourceRow,filterRow,scroll,footer],vertical:true,spacing:18);app.pin(layout,to:content,inset:26)
  for view in [sourceRow,filterRow,scroll,footer]{view.widthAnchor.constraint(equalTo:layout.widthAnchor).isActive=true}
  scroll.heightAnchor.constraint(greaterThanOrEqualToConstant:180).isActive=true;scroll.setContentHuggingPriority(.defaultLow,for:.vertical)
  updateControls()
 }
 private func updateControls(){
  buttons.forEach{$0.isEnabled=enabled};sources.isEnabled=enabled && !roots.isEmpty;modes.isEnabled=enabled
  scanAction?.isEnabled=enabled && source != nil
  let selected=records.filter{checked.contains($0.candidate.id)}
  addAction?.isEnabled=enabled && selected.contains{!$0.added && !$0.ignored && $0.candidate.available && !$0.candidate.entries.isEmpty}
  ignoreAction?.title=modes.selectedSegment==2 ? "恢复所选":"忽略所选"
  ignoreAction?.isEnabled=enabled && selected.contains{modes.selectedSegment==2 ? $0.ignored:!$0.added && !$0.ignored}
  if busy{spinner.startAnimation(nil)}else{spinner.stopAnimation(nil)}
 }
 private func begin(_ message:String)->UUID? {
  guard enabled else{if !current{invalidateAccount()};return nil}
  operation=UUID();busy=true;status.stringValue=message;updateControls();table.reloadData();return operation
 }
 private func finish(_ message:String=""){busy=false;status.stringValue=message;updateControls();table.reloadData()}
 private func request<T:Decodable>(_ path:String,method:String="GET",body:[String:Any]?=nil,id:UUID,then:@escaping(T)->Void){
  guard valid(id) else{return}
  api.request(path,method:method,token:token,body:body){[weak self] (result:Result<T,Error>) in DispatchQueue.main.async {
   guard let self,self.valid(id) else{return}
   switch result{case .success(let value):then(value);case .failure(let error):self.finish(error.localizedDescription)}
  }}
 }
 private func refresh(selectRootID:String?=nil){
  guard let id=begin("正在读取…") else{return}
  let selected=selectRootID ?? source?.id
  request("/api/admin/roots",id:id){[weak self] (response:SharedAdminRoots) in
   guard let self else{return}
   self.roots=response.roots;self.sources.removeAllItems();self.sources.addItems(withTitles:self.roots.map(\.title))
   if let selected,let index=self.roots.firstIndex(where:{$0.id==selected}){self.sources.selectItem(at:index)}
   self.sources.toolTip=self.source?.path
   self.loadCandidates(id:id)
  }
 }
 private func loadCandidates(id:UUID,message:String=""){
  request("/api/library/candidates",id:id){[weak self] (response:AccountDiscoveryCandidates) in
   guard let self else{return}
   self.candidates=response.candidates;self.checked=[];self.reloadRows()
   self.finish(self.roots.isEmpty ? "请添加服务器文件夹。":message)
  }
 }
 @objc private func sourceChanged(){guard enabled else{return};checked=[];sources.toolTip=source?.path;status.stringValue="";reloadRows()}
 @objc private func filterChanged(){guard enabled else{return};checked=[];reloadRows()}
 private func reloadRows(){
  records=candidates.filter{row in
   guard row.candidate.rootID==source?.id else{return false}
   switch modes.selectedSegment{case 1:return row.added;case 2:return row.ignored;default:return !row.ignored}
  }.sorted{$0.candidate.relative.localizedStandardCompare($1.candidate.relative) == .orderedAscending}
  checked.formIntersection(Set(records.map{$0.candidate.id}));table.reloadData();updateControls()
 }
 private func scan(){
  guard let source else{return}
  do {
   let path=try SharedAdminPath.scan(source.id)
   guard let id=begin("正在扫描…") else{return}
   request(path,method:"POST",id:id){[weak self] (response:SharedAdminScan) in
    let message=response.complete ? (response.issues.first ?? "扫描完成"):"扫描未完成"
    self?.loadCandidates(id:id,message:message)
   }
  }catch{status.stringValue=error.localizedDescription}
 }
 private func addRoot(){
  guard enabled,let window,window.attachedSheet==nil else{return}
  let id=operation
  let alert=NSAlert();alert.messageText="添加服务器文件夹";alert.addButton(withTitle:"添加");alert.addButton(withTitle:"取消")
  let path=NSTextField(),title=NSTextField();path.placeholderString="服务器文件夹路径";title.placeholderString="名称（可选）"
  let fields=NSStackView(views:[path,title]);fields.orientation = .vertical;fields.spacing=10;fields.frame=NSRect(x:0,y:0,width:420,height:64)
  for field in [path,title]{field.widthAnchor.constraint(equalTo:fields.widthAnchor).isActive=true}
  alert.accessoryView=fields
  alert.beginSheetModal(for:window){[weak self] response in
   guard let self,self.valid(id),self.enabled,response == .alertFirstButtonReturn else{return}
   let value=path.stringValue.trimmingCharacters(in:.whitespacesAndNewlines)
   guard !value.isEmpty,value.utf8.count<=4096,!value.unicodeScalars.contains(where:CharacterSet.controlCharacters.contains) else{self.status.stringValue="请输入服务器文件夹路径。";return}
   let name=title.stringValue.trimmingCharacters(in:.whitespacesAndNewlines)
   guard let operation=self.begin("正在添加文件夹…") else{return}
   self.request("/api/admin/roots",method:"POST",body:["path":value,"title":name.isEmpty ? (value as NSString).lastPathComponent:name],id:operation){[weak self] (root:SharedAdminRoot) in
    guard let self else{return}
    self.busy=false;self.refresh(selectRootID:root.id) // Adding a root never scans it.
   }
  }
 }
 private func mutationIDs(_ predicate:(AccountDiscoveryCandidate)->Bool)->[String]{records.filter{checked.contains($0.candidate.id) && predicate($0)}.map{$0.candidate.id}.sorted()}
 private func addSelected(){
  let ids=mutationIDs{!$0.added && !$0.ignored && $0.candidate.available && !$0.candidate.entries.isEmpty}
  guard !ids.isEmpty else{return}
  guard ids.count<=100 else{status.stringValue="每次最多添加 100 项。";return}
  guard let id=begin("正在添加…") else{return}
  let paths=Dictionary(uniqueKeysWithValues:records.filter{ids.contains($0.candidate.id)}.compactMap{row in entry(row).map{(row.candidate.id,$0.path)}})
  request("/api/admin/library/add",method:"POST",body:["candidateIDs":ids,"userID":connection.accountID,"entryPaths":paths],id:id){[weak self] (_:AccountDiscoveryMutation) in
   guard let self else{return}
   self.changed()
   guard self.valid(id) else{return}
   self.loadCandidates(id:id,message:"已添加所选项目")
  }
 }
 private func ignoreSelected(){
  let ignored=modes.selectedSegment != 2
  let ids=mutationIDs{ignored ? !$0.added && !$0.ignored:$0.ignored}
  guard !ids.isEmpty else{return}
  guard ids.count<=100 else{status.stringValue="每次最多处理 100 项。";return}
  guard let id=begin(ignored ? "正在忽略…":"正在恢复…") else{return}
  request("/api/library/candidates/ignore",method:"POST",body:["candidateIDs":ids,"ignored":ignored],id:id){[weak self] (_:AccountDiscoveryMutation) in
   self?.loadCandidates(id:id,message:ignored ? "已忽略所选项目":"已恢复所选项目")
  }
 }
 func numberOfRows(in tableView:NSTableView)->Int{records.count}
 private func entry(_ row:AccountDiscoveryCandidate)->Candidate? {
  if let selected=selectedEntries[row.candidate.id],row.candidate.entries.contains(selected){return selected}
  return row.candidate.entries.first
 }
 func tableView(_ tableView:NSTableView,viewFor tableColumn:NSTableColumn?,row:Int)->NSView? {
  guard records.indices.contains(row),let column=tableColumn?.identifier.rawValue else{return nil}
  let record=records[row],candidate=record.candidate,chosen=entry(record)
  if column=="check" {
   let check=NSButton(checkboxWithTitle:"",target:self,action:#selector(checkedChanged(_:)));check.identifier=NSUserInterfaceItemIdentifier(candidate.id);check.setAccessibilityLabel("选择 "+candidate.title)
   check.state=checked.contains(candidate.id) ? .on:.off;check.isEnabled=enabled && (!record.added || candidate.available);return check
  }
  if column=="entry",candidate.entries.count>1 {
   let popup=NSPopUpButton();popup.identifier=NSUserInterfaceItemIdentifier(candidate.id);popup.addItems(withTitles:candidate.entries.map{$0.path=="." ? candidate.title+".app":$0.path})
   popup.selectItem(at:chosen.flatMap{candidate.entries.firstIndex(of:$0)} ?? 0);popup.target=self;popup.action=#selector(entrySelected(_:));popup.isEnabled=enabled;return popup
  }
  let value:String
  switch column {
  case "title":value=candidate.title+(candidate.available ? "":" · 不可用")
  case "version":value=DiscoveryPresentation.version(relativePath:chosen.map{candidate.relative+"/"+$0.path} ?? candidate.relative).replacingOccurrences(of:"（目录标记）",with:"").replacingOccurrences(of:"未知",with:"—")
  case "platform":value=chosen.map{DiscoveryPresentation.platform(entry:$0)} ?? "—"
  case "language":value=chosen.map{DiscoveryPresentation.language(entry:$0,relativePath:candidate.relative).replacingOccurrences(of:"标记",with:"").replacingOccurrences(of:"未知",with:"—")} ?? "—"
  case "entry":value=chosen.map{$0.path=="." ? candidate.title+".app":$0.path} ?? "—"
  default:value=""
  }
  let text=NSTextField(labelWithString:value);text.font = .systemFont(ofSize:13,weight:column=="title" ? .medium:.regular);text.lineBreakMode = .byTruncatingMiddle;text.toolTip=candidate.relative;text.textColor=candidate.available ? .labelColor:.secondaryLabelColor;text.alignment = .left;text.maximumNumberOfLines=1;text.usesSingleLineMode=true
  let cell=NSTableCellView();cell.textField=text;cell.addSubview(text);text.translatesAutoresizingMaskIntoConstraints=false
  NSLayoutConstraint.activate([text.leadingAnchor.constraint(equalTo:cell.leadingAnchor),text.trailingAnchor.constraint(equalTo:cell.trailingAnchor),text.centerYAnchor.constraint(equalTo:cell.centerYAnchor),text.heightAnchor.constraint(equalToConstant:20)]);cell.toolTip=candidate.relative;return cell
 }
 @objc private func checkedChanged(_ button:NSButton){
  guard enabled,let id=button.identifier?.rawValue,records.contains(where:{$0.candidate.id==id}) else{return}
  if button.state == .on{checked.insert(id)}else{checked.remove(id)};updateControls()
 }
 @objc private func entrySelected(_ popup:NSPopUpButton){
  guard enabled,let id=popup.identifier?.rawValue,let row=records.first(where:{$0.candidate.id==id}),row.candidate.entries.indices.contains(popup.indexOfSelectedItem) else{return}
  selectedEntries[id]=row.candidate.entries[popup.indexOfSelectedItem];table.reloadData()
 }
}
