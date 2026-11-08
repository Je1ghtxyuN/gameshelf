import AppKit

// Standalone UI host: no production App startup, installation, migration or games.
final class App {
 var activeLibraryConnection:SharedConnection?
 var sharedAccessTokens=SharedSessionTokens()
 func sharedToken(for connection:SharedConnection)throws->String?{sharedAccessTokens[connection.id]}
 func label(_ text:String,size:CGFloat=14,weight:NSFont.Weight = .regular)->NSTextField{let l=NSTextField(labelWithString:text);l.font = .systemFont(ofSize:size,weight:weight);return l}
 func stack(_ views:[NSView],vertical:Bool=false,spacing:CGFloat=10)->NSStackView{let s=NSStackView(views:views);s.orientation=vertical ? .vertical:.horizontal;s.spacing=spacing;s.alignment=vertical ? .leading:.centerY;return s}
 func pin(_ view:NSView,to parent:NSView,inset:CGFloat=0){view.translatesAutoresizingMaskIntoConstraints=false;parent.addSubview(view);NSLayoutConstraint.activate([view.leadingAnchor.constraint(equalTo:parent.leadingAnchor,constant:inset),view.trailingAnchor.constraint(equalTo:parent.trailingAnchor,constant:-inset),view.topAnchor.constraint(equalTo:parent.topAnchor,constant:inset),view.bottomAnchor.constraint(equalTo:parent.bottomAnchor,constant:-inset)])}
}
final class ActionButton:NSButton {
 let block:()->Void
 init(_ title:String,_ block:@escaping()->Void){self.block=block;super.init(frame:.zero);self.title=title;target=self;action=#selector(invoke);bezelStyle = .rounded}
 required init?(coder:NSCoder){fatalError()}
 @objc private func invoke(){block()}
}
