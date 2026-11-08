import AppKit

extension App {
 @objc func accountDiscoveryAction(){showAccountDiscovery()}

 func showAccountLogin(){
  // Never construct a grid from the owner store while authentication is pending.
  window?.orderOut(nil)
  window?.contentView=NSView()
  settingsWindow?.close();discoveryWindow?.close()
  for controller in propertyWindows.values{controller.close()}
  propertyWindows.removeAll()
  do{
   if sharedLibraryWindow==nil{sharedLibraryWindow=try SharedLibraryWindowController(app:self)}
   sharedLibraryWindow?.openLogin()
   sharedLibraryWindow?.showWindow(nil)
   sharedLibraryWindow?.window?.makeKeyAndOrderFront(nil)
  }catch{errorAlert(error)}
 }

 func showOnboardingIfNeeded(){
  do{
   let state=try OnboardingState(root:root)
   guard state.needsPresentation(existingGameCount:store.games.count),(try SharedConnectionStore(root:root)).connections.isEmpty else{return}
   onboardingWindow=OnboardingWindowController{[weak self] choice in
    guard let self else{return}
    do{try state.complete(choice:choice)}catch{self.errorAlert(error);return}
    self.onboardingWindow?.close();self.onboardingWindow=nil
    switch choice {
    case .local:do{try self.activateLocalLibrary()}catch{self.errorAlert(error)}
    case .connect:self.showAccountLogin()
    case .create:self.showAccountLogin();self.sharedLibraryWindow?.beginAdministratorSetup()
    }
   }
   onboardingWindow?.showWindow(nil);onboardingWindow?.window?.makeKeyAndOrderFront(nil)
  }catch{showStatus(error.localizedDescription)}
 }
}
