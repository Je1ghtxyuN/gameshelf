import AppKit
import Darwin

extension App {
 var directNASPreviewEnabled: Bool {
  (Bundle.main.object(forInfoDictionaryKey: "GameShelfPreviewBuild") as? Bool) == true &&
  (Bundle.main.object(forInfoDictionaryKey: "GameShelfDirectNASPreview") as? Bool) == true
 }

 @MainActor private func directNASContext(_ game: Game) -> (AccountLibraryController, URL, DirectNASBinding.Key)? {
  guard directNASPreviewEnabled, let account = accountLibrary, account.active,
        let profile = account.profileRoot, let reference = game.shared,
        reference.connectionID == account.connection.id, reference.serverID == account.identity.serverID,
        reference.accountID == account.identity.accountID, let catalogGame = account.game(for: game.id),
        let variant = catalogGame.variants.first(where: {
         $0.id == reference.gameID && $0.available && $0.permitsDirectNAS && $0.entries.contains(game.entry) &&
         DirectNASBinding.supports(game.entry, platform: $0.platform)
        }) else { return nil }
  return (account, profile, .init(identity: account.identity, canonicalID: catalogGame.id,
                                   variantID: variant.id, entryPath: game.entry.path, entryBackend: game.entry.backend))
 }

 @MainActor func directNASBindingStatus(_ game: Game) -> String {
  guard let (_, profile, key) = directNASContext(game) else { return "此版本不支持 NAS 直启。" }
  do { return try DirectNASBinding.load(key, profile: profile)?.folder ?? "尚未选择本机 NAS 资料夹。" }
  catch { return "本机 NAS 绑定不可读取，请重新选择。" }
 }

 @MainActor func canBindDirectNAS(_ game: Game) -> Bool { directNASContext(game) != nil }

 @MainActor func chooseDirectNASFolder(_ game: Game, completed: @escaping () -> Void) {
  guard let (owner, profile, key) = directNASContext(game), !accountGameIsRunning else { return }
  let picker = NSOpenPanel()
  picker.title = "选择已挂载的 NAS 游戏资料夹"
  picker.prompt = "绑定此资料夹"
  picker.canChooseDirectories = true
  picker.canChooseFiles = false
  picker.treatsFilePackagesAsDirectories = true
  guard picker.runModal() == .OK, let folder = picker.url, !accountGameIsRunning else { return }
  setBusy(true, "正在核对 NAS 游戏入口…")
  Task {
   defer { setBusy(false, ""); completed() }
   do {
    let binding = try await Task.detached(priority: .userInitiated) {
     try DirectNASBinding.capture(key: key, folder: folder, entry: game.entry)
    }.value
    guard !quitting, let current = store.games.first(where: { $0.id == game.id }),
          let (currentOwner, currentProfile, currentKey) = directNASContext(current),
          currentOwner === owner, currentProfile == profile, currentKey == key else { return }
    try binding.save(profile: profile)
   } catch { if !quitting { errorAlert(error) } }
  }
 }

 @MainActor private func familyNASSource(_ game: Game, account: AccountLibraryController) -> AccountLibraryNASSource? {
  guard account.document?.directNas == true, let reference = game.shared else { return nil }
  return account.game(for: game.id)?.variants.first {
   $0.id == reference.gameID && $0.platform == "windows" && $0.available &&
   $0.entries.contains(game.entry) && DirectNASBinding.supports(game.entry, platform: $0.platform)
  }?.nasSource
 }

 @MainActor func launchDirectNASPreview(_ game: Game) {
  guard !accountGameIsRunning else { errorAlert(LibraryError("请先保存并退出正在运行的游戏。")); return }
  guard let (owner, profile, key) = directNASContext(game) else {
   errorAlert(LibraryError("此版本不支持 NAS 直启。此预览支持 macOS .app 和可由 Wine 运行的 Windows .exe。"))
   return
  }
  do {
   let familySource = familyNASSource(game, account: owner)
   let binding = familySource == nil ? try DirectNASBinding.load(key, profile: profile) : nil
   guard familySource != nil || binding != nil else {
    throw LibraryError("请在游戏属性的文件页选择已挂载的 NAS 游戏资料夹。")
   }
   let alert = NSAlert()
   alert.messageText = "从 NAS 直接启动「\(game.title)」？"
   alert.informativeText = "请确认其他设备已退出此游戏。游戏自行保存。Wine 的用户目录保存在本机账号运行环境。GameShelf 不同步存档。"
   alert.addButton(withTitle: "直接启动")
   alert.addButton(withTitle: "取消")
   guard alert.runModal() == .alertFirstButtonReturn, !accountGameIsRunning else { return }
   beginGameLoading(game)
   setBusy(true, "正在核对 NAS 游戏入口…")
   let wine = game.entry.backend != .native
   let runtimeRoot = runtimeDataRoot(for: game)
   let engineRoot = root.appendingPathComponent("Engines")
   let dependencyRoot = root.appendingPathComponent("Dependencies")
   let configured = URL(fileURLWithPath: UserDefaults.standard.string(forKey: "WineSource") ?? Wrapper.defaultSource.path)
   Task {
    do {
     let (entryURL, runtime, rebound) = try await Task.detached(priority: .userInitiated) { () -> (URL, URL?, Game) in
      let folder: URL
      let entryURL: URL
      if let familySource {
       folder = try DirectNASRoot.gameFolder(source: familySource, profile: profile)
       entryURL = try DirectNASRoot.launchEntry(in: folder, entry: game.entry)
      } else if let binding {
       folder = URL(fileURLWithPath: binding.folder, isDirectory: true)
       entryURL = try binding.verifiedEntry(entry: game.entry)
      } else {
       throw LibraryError("未选择 NAS 游戏目录。")
      }
      var rebound = game
      rebound.folder = folder.path
      guard wine else { return (entryURL, nil, rebound) }
      try Self.prepareDirectNASRuntimeRoot(runtimeRoot)
      let source = try ManagedWine.resolve(configured: configured, root: engineRoot, allowInstall: false) { _ in }
      let destination = runtimeRoot.appendingPathComponent("Runtimes/"+game.id.uuidString+".app")
      try Self.prepareDirectNASRuntimeRoot(destination.deletingLastPathComponent())
      if FileManager.default.fileExists(atPath: destination.path) {
       try Self.prepareDirectNASRuntimeRoot(destination)
      }
      let runtime = try Wrapper.prepare(game: rebound, source: source, destination: destination, shareEngine: true, freshPrefix: true)
      try SharedWineBootstrap.prepare(game: rebound, runtime: runtime, directNAS: true)
      try RTP.register(game: rebound, runtime: runtime, root: dependencyRoot, directNAS: true)
      if let familySource {
       let current = try DirectNASRoot.gameFolder(source: familySource, profile: profile)
       guard current == folder else { throw LibraryError("NAS 游戏目录已变更。") }
       _ = try DirectNASRoot.launchEntry(in: current, entry: game.entry)
      } else if let binding {
       _ = try binding.verifiedEntry(entry: game.entry)
      }
      return (entryURL, runtime, rebound)
     }.value
     guard !quitting, let current = store.games.first(where: { $0.id == game.id }),
           let (currentOwner, currentProfile, currentKey) = directNASContext(current),
           currentOwner === owner, currentProfile == profile, currentKey == key,
           familyNASSource(current, account: owner) == familySource,
           try (familySource != nil || DirectNASBinding.load(key, profile: profile) == binding) else {
      setBusy(false, "")
      return
     }
     if let runtime {
      startWine(game, runtime: runtime, launchGame: rebound, directNAS: true)
      return
     }
     let appURL = entryURL
     let runningIdentifier = Bundle(url: appURL)?.bundleIdentifier.map {
      NSRunningApplication.runningApplications(withBundleIdentifier: $0).contains { !$0.isTerminated }
     } ?? false
     guard nativeApplicationLookup(appURL) == nil, !runningIdentifier else {
      throw LibraryError("此 NAS 游戏已在运行，请先在游戏内保存并退出。")
     }
     let existingPIDs = Set(NSWorkspace.shared.runningApplications.map(\.processIdentifier))
     let configuration = NSWorkspace.OpenConfiguration()
     configuration.createsNewApplicationInstance = false
     nativeLaunching.insert(game.id)
     setBusy(false, "正在从 NAS 启动…")
     NSWorkspace.shared.openApplication(at: appURL, configuration: configuration) { [weak self] process, error in
      DispatchQueue.main.async {
       guard let self else { return }
       self.nativeLaunching.remove(game.id)
       self.setBusy(false, "")
       guard !self.quitting else { return }
       if let error { self.errorAlert(error); return }
       guard let process, !process.isTerminated, !existingPIDs.contains(process.processIdentifier),
             process.bundleURL?.standardizedFileURL.path == appURL.standardizedFileURL.path else {
        self.errorAlert(LibraryError("macOS 未确认启动了绑定的 NAS 游戏入口。"))
        return
       }
       self.nativeApplications[game.id] = process
       self.beginPlaytime(game.id)
       self.markPlayed(game)
       self.showStatus("已从 NAS 启动「\(game.title)」")
      }
     }
    } catch {
     setBusy(false, "")
     if !quitting { errorAlert(error) }
    }
   }
  } catch { errorAlert(error) }
 }

 nonisolated private static func prepareDirectNASRuntimeRoot(_ directory: URL) throws {
  var parent = directory
  while !FileManager.default.fileExists(atPath: parent.path) && parent.path != "/" {
   if (try? FileManager.default.destinationOfSymbolicLink(atPath: parent.path)) != nil {
    throw LibraryError("Wine 运行目录包含符号链接。")
   }
   parent.deleteLastPathComponent()
  }
  var filesystem = statfs()
  guard parent.resolvingSymlinksInPath().path == parent.path,
        statfs(parent.path, &filesystem) == 0, filesystem.f_flags & UInt32(MNT_LOCAL) != 0 else {
   throw LibraryError("Wine 运行目录必须位于本机磁盘。")
  }
  try AccountLibraryDisk.directory(directory)
 }

}
