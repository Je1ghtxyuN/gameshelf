using GameShelf.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;
using System.Text.Json;
using System.Runtime.InteropServices;
using Windows.Storage.Streams;
namespace GameShelf.Windows;
public sealed partial class MainWindow:Window
{
    StandaloneLibrary? local;ApiClient? api;AccountStore? store;Connection? connection;LibrarySnapshot? library;
    readonly AccountGate gate=new();CancellationTokenSource? gameCancellation;Task? running;string selected="all";
    readonly string deviceID=Environment.MachineName;int identityEpoch;
    readonly Microsoft.UI.Dispatching.DispatcherQueueTimer libraryRefreshTimer;
    readonly SemaphoreSlim libraryRefreshLock = new(1, 1);
    public MainWindow()
    {
        InitializeComponent();AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory,"Assets","AppIcon.ico"));App.StartupLog("Window XAML loaded");var dpi=Math.Max(96,GetDpiForWindow(WinRT.Interop.WindowNative.GetWindowHandle(this)));AppWindow.Resize(new global::Windows.Graphics.SizeInt32((int)(1180*dpi/96),(int)(800*dpi/96)));
        libraryRefreshTimer = DispatcherQueue.CreateTimer();
        libraryRefreshTimer.Interval = TimeSpan.FromSeconds(60);
        libraryRefreshTimer.Tick += RefreshConnectedLibrary;
        libraryRefreshTimer.Start();
        Closed+=WindowClosed;Root.Loaded+=RestoreLoaded;
    }
    [DllImport("user32.dll")]static extern uint GetDpiForWindow(IntPtr window);
    async void RestoreLoaded(object sender,RoutedEventArgs e)
    {
        Root.Loaded-=RestoreLoaded;if(Connection.LastMode()=="local"){try{await EnterLocal();}catch(Exception error){LoginError.Text=error.Message;}return;}connection=Connection.Load();Username.Text="";if(connection==null)return;
        Address.Text=connection.Address;
        var session=connection.Session();if(session==null){ConnectionFields.Visibility=Visibility.Collapsed;CredentialsFields.Visibility=Visibility.Visible;return;}
        var owner=connection.Client();api=owner;
        try{owner.Restore(session);await EnterLibrary();}
        catch{if(api==owner){owner.Dispose();api=null;Username.Text="";Password.Password="";ConnectionFields.Visibility=Visibility.Collapsed;CredentialsFields.Visibility=Visibility.Visible;}}
    }
    async void ConnectClick(object sender,RoutedEventArgs e)
    {
        var epoch=++identityEpoch;ConnectButton.IsEnabled=false;LoginError.Text="";
        try{
            DiscoveredServer info;
            var address=Address.Text.Trim();var known=connection!=null&&connection.Address.TrimEnd('/')==address.TrimEnd('/')?connection.Pin:null;
            try{info=await Discovery.Inspect(address,known);}
            catch(CertificateTrustRequired certificate){
                var compare=new StackPanel{Spacing=16,Width=440};compare.Children.Add(new TextBlock{Text=address,TextWrapping=TextWrapping.Wrap});compare.Children.Add(new TextBox{Header="证书 SHA256",Text=certificate.Fingerprint,IsReadOnly=true,TextWrapping=TextWrapping.Wrap});
                if(await Dialog("比较服务器证书",compare,"信任并连接").ShowAsync()!=ContentDialogResult.Primary)return;
                info=await Discovery.Inspect(address,certificate.Fingerprint);
            }
            if(epoch!=identityEpoch)return;
            Connection.SaveMode("server");connection=new(info.Address,info.ServerID,info.CertificateSHA256??"",info.PlainHttp,null);
            api?.Dispose();api=connection.Client();Username.Text="";Password.Password="";
            ConnectionFields.Visibility=Visibility.Collapsed;CredentialsFields.Visibility=Visibility.Visible;
        }catch(Exception error){LoginError.Text=error.Message;}finally{ConnectButton.IsEnabled=true;}
    }
    void ChangeServerClick(object sender,RoutedEventArgs e){Username.Text="";Password.Password="";connection=Connection.Load();Address.Text=connection?.Address??"";ConnectionFields.Visibility=Visibility.Visible;CredentialsFields.Visibility=Visibility.Collapsed;}
    async void LoginClick(object sender,RoutedEventArgs e)
    {
        LoginButton.IsEnabled=false;LoginError.Text="";
        try{gate.CanSwitch();var target=connection??throw new InvalidOperationException("请先连接服务器");var owner=api??=target.Client();var login=await owner.Login(Username.Text,Password.Password);Password.Password="";if(api!=owner)return;await EnterLibrary();if(api!=owner)return;connection=target with{UserID=login.User.Id};connection.Save(login,Remember.IsChecked==true);}
        catch(Exception error){api?.Dispose();api=null;LoginError.Text=error.Message;}
        finally{LoginButton.IsEnabled=true;}
    }
    async Task EnterLibrary()
    {
        // Never reveal cache before a valid authenticated identity response.
        var owner=api??throw new InvalidOperationException("请先登录");var value=await owner.Library();
        if(api!=owner)throw new OperationCanceledException();
        var accountStore=new AccountStore(Connection.DataRoot,value.ServerID,value.User.Id);await accountStore.SaveLibrary(value);
        if(api!=owner)throw new OperationCanceledException();
        local=null;selected="all";Search.Text="";library=value;store=accountStore;
        LoginPanel.Visibility=Visibility.Collapsed;LibraryPanel.Visibility=Visibility.Visible;ScanButton.Visibility=library.User.Admin?Visibility.Visible:Visibility.Collapsed;
        BuildTree();Filter();if(await Flush())await Refresh();
    }
    async Task<bool> Flush()
    {
        var owner=api;var accountStore=store;
        if(local!=null||accountStore==null||owner==null)return false;
        var uploaded=false;
        try{
            await accountStore.Flush(async pending=>{
                if(api!=owner||store!=accountStore||owner.UserID!=pending.UserID||owner.ServerID!=pending.ServerID)
                    throw new InvalidDataException("账号不匹配");
                await owner.Post($"/api/library/games/{Safety.Id(pending.GameID)}/playtime",pending.Event);
                uploaded=true;
            });
        }
        catch{if(api==owner&&store==accountStore)Status.Text="游玩时长等待同步";}
        return uploaded&&api==owner&&store==accountStore;
    }
    void BuildTree()
    {
        Categories.RootNodes.Clear();
        foreach(var c in library!.Collections)Categories.RootNodes.Add(new(){Content=new Category(c.Id,c.Name),IsExpanded=false});
        Categories.RootNodes.Add(new(){Content=new Category("uncategorized","未分类"),IsExpanded=false});
        foreach(var node in Categories.RootNodes){var c=(Category)node.Content;foreach(var game in library.Games.Where(g=>c.ID=="uncategorized"?g.CollectionIDs.Length==0:g.CollectionIDs.Contains(c.ID)))node.Children.Add(new(){Content=game});}
    }
    record EntryChoice(string Name,string? VariantID,string? Path){public override string ToString()=>Name;}
    record Category(string ID,string Title){public override string ToString()=>Title;}
    void Filter()
    {
        if(library==null)return;NavAll.IsSelected=selected=="all";NavRecent.IsSelected=selected=="recent";NavFavorite.IsSelected=selected=="favorite";Heading.Text=selected switch{"all"=>"所有游戏","recent"=>"最近游玩","favorite"=>"收藏","uncategorized"=>"未分类",_=>library.Collections.FirstOrDefault(c=>c.Id==selected)?.Name??"所有游戏"};
        ScanButton.Visibility=(local!=null||library.User.Admin)&&selected=="all"?Visibility.Visible:Visibility.Collapsed;
        var games=library.Games.Where(g=>(selected switch{"all"=>true,"recent"=>g.LastPlayed.HasValue&&g.LastPlayed.Value.ValueKind==JsonValueKind.Number,"favorite"=>g.Favorite,"uncategorized"=>g.CollectionIDs.Length==0,_=>g.CollectionIDs.Contains(selected)})&&g.Title.Contains(Search.Text??"",StringComparison.CurrentCultureIgnoreCase));
        Covers.ItemsSource=(selected=="recent"?games.OrderByDescending(g=>g.LastPlayed?.GetDouble()??0):games).Select(g=>new LibraryCard(g,CardWidth())).ToArray();UpdateGrid();
    }
    void NavigationChanged(NavigationView sender,NavigationViewSelectionChangedEventArgs args){if(args.SelectedItem is NavigationViewItem{Tag:string value}&&selected!=value){selected=value;Filter();}}
    double CardWidth(){var space=Math.Max(180,Covers.ActualWidth-64);var columns=Math.Max(1,(int)Math.Floor(space/232));return space/columns-12;}
    void GridSizeChanged(object sender,SizeChangedEventArgs args)=>UpdateGrid();
    void UpdateGrid(){var width=CardWidth();if(Covers.ItemsPanelRoot is ItemsWrapGrid wrap)wrap.ItemWidth=width+12;if(Covers.ItemsSource is IEnumerable<LibraryCard> cards)foreach(var card in cards)card.Width=width;}

    async void PropertiesClick(object sender,RoutedEventArgs args){if(sender is Button{Tag:string id}&&library!=null)await Properties(library.Games.Single(g=>g.Id==id));}
    async void CreateCollectionClick(object sender,RoutedEventArgs args){var name=new TextBox{Header="名称"};if(await Dialog("新建分类",name,"新建").ShowAsync()==ContentDialogResult.Primary)try{await OwnPost("/api/library/collections",new{id=Guid.NewGuid(),name=name.Text});await Refresh();}catch(Exception e){Status.Text=e.Message;}}
    void CategoryInvoked(TreeView sender,TreeViewItemInvokedEventArgs args){if(args.InvokedItem is TreeViewNode n){if(n.Content is Category c){selected=c.ID;Filter();}else if(n.Content is LibraryGame g)_=Properties(g);}}
    void SearchChanged(AutoSuggestBox sender,AutoSuggestBoxTextChangedEventArgs args)=>Filter();
    async void RefreshConnectedLibrary(Microsoft.UI.Dispatching.DispatcherQueueTimer sender, object args)
    {
        if (local != null || api == null || store == null || gate.Active || running != null ||
            libraryRefreshLock.CurrentCount == 0) return;
        try { await Refresh(); }
        catch (Exception error) { Status.Text = error.Message; }
    }

    async Task Refresh()
    {
        var owner = api;
        var accountStore = store;
        var localLibrary = local;
        await libraryRefreshLock.WaitAsync();
        try
        {
            if (api != owner || store != accountStore || local != localLibrary) return;
            if (localLibrary != null)
            {
                library = localLibrary.Library();
                BuildTree();
                Filter();
                return;
            }
            if (owner == null || accountStore == null) return;
            await Flush();
            if (api != owner || store != accountStore) return;
            var value = await owner.Library();
            if (api != owner || store != accountStore) return;
            var snapshot = JsonSerializer.Serialize(value, AccountStore.Json);
            if (snapshot == JsonSerializer.Serialize(library, AccountStore.Json)) return;
            await accountStore.SaveLibrary(value);
            if (api != owner || store != accountStore) return;
            library = value;
            BuildTree();
            Filter();
        }
        finally { libraryRefreshLock.Release(); }
    }
    async void RefreshClick(object sender,RoutedEventArgs args){try{await Refresh();Status.Text="";}catch(Exception e){Status.Text=e.Message;}}
    async void GameInvoked(object sender,ItemClickEventArgs e){if(e.ClickedItem is LibraryGame g)await Properties(g);}
    async Task Properties(LibraryGame g)
    {
        var localOwner=local;var apiOwner=api;
        var title=new TextBox{Header="名称",Text=g.Title};var favorite=new CheckBox{Content="收藏",IsChecked=g.Favorite};
        var choices=new List<EntryChoice>{new("自动选择 Windows",null,null)};
        choices.AddRange(g.Variants.Where(v=>v.Platform=="windows"&&v.Available).SelectMany(v=>v.Entries.Where(e=>Safety.NativeEntry(e.Path,e.Backend)).Select(e=>new EntryChoice($"{v.Version??"Windows"} · {e.Path}",v.Id,e.Path))));
        var versions=new ComboBox{Header="Windows 版本",ItemsSource=choices,SelectedIndex=0,HorizontalAlignment=HorizontalAlignment.Stretch};
        if(g.PreferredEntries?.Windows is LaunchPreference pref)versions.SelectedItem=choices.FirstOrDefault(c=>c.VariantID==pref.VariantID&&c.Path==pref.EntryPath)??choices[0];
        var panel=new StackPanel{Spacing=12};panel.Children.Add(title);panel.Children.Add(favorite);panel.Children.Add(versions);
        var checks=new List<(string,CheckBox)>();foreach(var c in library!.Collections){var check=new CheckBox{Content=c.Name,IsChecked=g.CollectionIDs.Contains(c.Id)};checks.Add((c.Id,check));panel.Children.Add(check);}
#if PERSONAL_NAS_PREVIEW
        var preview = library.User.Admin && apiOwner != null && localOwner == null && store is { } account && choices.Count > 1
            ? PersonalPreviewControls(g, versions, apiOwner, account)
            : null;
        if (preview != null) panel.Children.Add(preview);
#endif
        var dialog=Dialog("属性",panel,"保存");dialog.SecondaryButtonText="移出游戏库";var action=await dialog.ShowAsync();
        if(local!=localOwner||api!=apiOwner)return;
        if(action==ContentDialogResult.Secondary){try{gate.CanSwitch();await OwnDelete($"/api/library/games/{Safety.Id(g.Id)}");await Refresh();}catch(Exception error){Status.Text=error.Message;}return;}
        if(action!=ContentDialogResult.Primary)return;
        try{await OwnPost($"/api/library/games/{Safety.Id(g.Id)}/metadata",new{revision=g.Revision,title=title.Text,favorite=favorite.IsChecked==true,collectionIDs=checks.Where(c=>c.Item2.IsChecked==true).Select(c=>c.Item1).ToArray()});
            if(local!=localOwner||api!=apiOwner)return;
            var entry=versions.SelectedItem as EntryChoice;await OwnPost($"/api/library/games/{Safety.Id(g.Id)}/launch-preferences",new{platform="windows",variantID=entry?.VariantID,entryPath=entry?.Path});await Refresh();}catch(Exception error){Status.Text=error.Message;}
    }
    async void PlayClick(object sender,RoutedEventArgs args){if(sender is Button{Tag:string id})await Launch(id);}
    async void CoverTapped(object sender,Microsoft.UI.Xaml.Input.TappedRoutedEventArgs args){args.Handled=true;if(sender is Image{Tag:string id})await Launch(id);}
    async Task Launch(string id)
    {
        if (gate.Active || running != null) { Status.Text = "请先结束游戏"; return; }
        if(library==null||(local==null&&(api==null||store==null)))return;
        try
        {
            var game = library.Games.Single(g => g.Id == id);
            gameCancellation = new();
            var token = gameCancellation.Token;
            StopButton.Visibility = Visibility.Visible;
            Status.Text = game.Title;
            if (local != null)
                running = new StandaloneRunner(local, gate, new WindowsView().Start).Run(game.Id, token);
            else
            {
                var account = store!;
                if (api!.ServerID != account.ServerID || api.UserID != account.UserID)
                    throw new InvalidOperationException("当前连接账号已变化。");
                var (variant, entry) = Editions.Select(game);
                var directNas = new PersonalNasPreview(account);
                var useDirectNas = library.MountedWorkspaceShare == null && variant.PermitsDirectNas() && (library.User.Admin || library.DirectNas) && directNas.HasRootBinding(variant);
                if (library.MountedWorkspaceShare == null && variant.PermitsDirectNas() && library.DirectNas && variant.NasSource != null && !useDirectNas)
                    throw new InvalidOperationException("请在「设置 → 资料夹」选择此电脑的 NAS 游戏库根目录。");
#if PERSONAL_NAS_PREVIEW
                useDirectNas |= library.MountedWorkspaceShare == null && variant.PermitsDirectNas() && library.User.Admin && directNas.Folder(variant.Id) != null;
#endif
                if (useDirectNas)
                {
                    Status.Text = "从 NAS 启动 · " + game.Title;
                    running = directNas.Run(game, variant, entry, gate, deviceID, new WindowsView().Start, token);
                }
                else
                    running = new GameRunner(api, account, ConnectedLauncher.Create(library.MountedWorkspaceShare != null), gate, deviceID,
                        library.OwnerWindowsOverlay && library.User.Admin, library.MountedWorkspaceShare).Run(game, token);
            }
            await running;
            await Refresh();
            Status.Text = "";
        }
        catch (OperationCanceledException)
        {
            Status.Text = "已停止";
            if (!gate.Active) await Flush();
        }
        catch (Exception error) { Status.Text = error.Message; }
        finally { StopButton.Visibility=Visibility.Collapsed;gameCancellation?.Dispose();gameCancellation=null;running=null; }
    }
    void StopClick(object sender,RoutedEventArgs args)=>gameCancellation?.Cancel();
    async void CoverLoaded(object sender,RoutedEventArgs args){if(sender is Image image)await LoadCover(image);}
    async void CoverContextChanged(FrameworkElement sender,DataContextChangedEventArgs args){if(sender is Image image)await LoadCover(image);}
    async Task LoadCover(Image image)
    {
        image.Source=null;if(image.DataContext is not LibraryCard card)return;var id=card.Id;var owner=api;var localOwner=local;
        try{
            byte[] bytes;
            if(localOwner!=null){var(folder,_)=localOwner.Resolve(id);var path=new[]{"cover.png","cover.jpg","folder.jpg"}.Select(name=>Path.Combine(folder,name)).FirstOrDefault(File.Exists);if(path==null||(File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0||new FileInfo(path).Length>8*1024*1024)return;bytes=await File.ReadAllBytesAsync(path);}
            else{if(owner==null)return;var cover=await owner.Send<JsonElement>(HttpMethod.Get,$"/api/library/games/{Safety.Id(id)}/cover");bytes=Convert.FromBase64String(cover.GetProperty("data").GetString()!);}
            if(api!=owner||local!=localOwner||image.DataContext!=card||bytes.Length>8*1024*1024)return;
            using var stream=new InMemoryRandomAccessStream();using(var writer=new DataWriter(stream)){writer.WriteBytes(bytes);await writer.StoreAsync();writer.DetachStream();}stream.Seek(0);var bitmap=new BitmapImage();await bitmap.SetSourceAsync(stream);if(api==owner&&local==localOwner&&image.DataContext==card)image.Source=bitmap;
        }catch{}
    }
    ContentDialog Dialog(string title,UIElement content,string primary)=>new(){Title=title,Content=content,RequestedTheme=ElementTheme.Light,PrimaryButtonText=primary,CloseButtonText="关闭",XamlRoot=Root.XamlRoot,DefaultButton=ContentDialogButton.Primary};
    async void WindowClosed(object sender,WindowEventArgs args){if(running!=null){args.Handled=true;gameCancellation?.Cancel();try{await running;}catch{}if(!gate.Active)Close();return;}libraryRefreshTimer.Stop();}
    async void ScanClick(object sender,RoutedEventArgs args)
    {
        if(local!=null){await ScanLocal();return;}
        if(library?.User.Admin!=true)return;
        try{
            var roots=await api!.Send<JsonElement>(HttpMethod.Get,"/api/library/roots");var picker=new ComboBox{Header="资料夹",DisplayMemberPath="Title",ItemsSource=roots.GetProperty("roots").Deserialize<Root[]>(AccountStore.Json),HorizontalAlignment=HorizontalAlignment.Stretch};picker.SelectedIndex=0;
            var scan=new Button{Content="扫描"};var list=new ListView{SelectionMode=ListViewSelectionMode.Multiple,DisplayMemberPath="DisplayTitle",Height=270};var state=new TextBlock{TextWrapping=TextWrapping.Wrap};
            Candidate[] candidates=[];var chosenEntries=new Dictionary<string,string>();Candidate? activeCandidate=null;
            var entryPicker=new ComboBox{Header="Windows 启动入口",HorizontalAlignment=HorizontalAlignment.Stretch};
            list.SelectionChanged+=(_,a)=>{if(a.AddedItems.LastOrDefault() is Candidate candidate){activeCandidate=candidate;var paths=(candidate.Entries??[]).Where(e=>Safety.NativeEntry(e.Path,e.Backend)).Select(e=>e.Path).ToArray();entryPicker.ItemsSource=paths;entryPicker.IsEnabled=paths.Length>0;entryPicker.SelectedItem=chosenEntries.TryGetValue(candidate.Id,out var path)&&paths.Contains(path)?path:paths.FirstOrDefault();}};
            entryPicker.SelectionChanged+=(_,_)=>{if(activeCandidate!=null&&entryPicker.SelectedItem is string path)chosenEntries[activeCandidate.Id]=path;};
            var candidateFilter=new ComboBox{ItemsSource=new[]{"未添加","已添加","已忽略","全部"},SelectedIndex=0};var ignore=new Button{Content="忽略"};var restore=new Button{Content="恢复"};
            void ShowCandidates()=>list.ItemsSource=candidates.Where(c=>candidateFilter.SelectedIndex switch{0=>c.Available&&!c.Added&&!c.Ignored,1=>c.Added,2=>c.Ignored,_=>true}).ToArray();
            candidateFilter.SelectionChanged+=(_,_)=>ShowCandidates();
            string[] SelectedIDs(){var ids=list.SelectedItems.Cast<Candidate>().Select(c=>Safety.Id(c.Id)).Distinct().ToArray();if(ids.Length>100)throw new InvalidOperationException("每次最多选择 100 项");return ids;}
            async Task Candidates(){var data=await api.Send<JsonElement>(HttpMethod.Get,"/api/library/candidates");candidates=data.GetProperty("candidates").Deserialize<Candidate[]>(AccountStore.Json)!;foreach(var candidate in candidates){var entries=(candidate.Entries??[]).Where(e=>Safety.NativeEntry(e.Path,e.Backend)).Select(e=>e.Path).ToArray();if(entries.Length>0&&(!chosenEntries.TryGetValue(candidate.Id,out var previous)||!entries.Contains(previous)))chosenEntries[candidate.Id]=entries[0];}ShowCandidates();}
            async Task SetIgnored(bool value){try{var ids=SelectedIDs();if(ids.Length>0){await OwnPost("/api/library/candidates/ignore",new{candidateIDs=ids,ignored=value});await Candidates();}}catch(Exception e){state.Text=e.Message;}}
            ignore.Click+=async(_,_)=>await SetIgnored(true);restore.Click+=async(_,_)=>await SetIgnored(false);
            // Existing persisted candidates load immediately: adding never triggers another scan.
            await Candidates();
            scan.Click+=async(_,_)=>{scan.IsEnabled=false;try{if(picker.SelectedItem is Root root){await OwnPost($"/api/admin/roots/{Safety.Id(root.Id)}/scan",new{});await Candidates();}}catch(Exception e){state.Text=e.Message;}finally{scan.IsEnabled=true;}};
            var panel=new StackPanel{Spacing=12,Width=450};panel.Children.Add(picker);panel.Children.Add(scan);panel.Children.Add(candidateFilter);panel.Children.Add(list);panel.Children.Add(entryPicker);var actions=new StackPanel{Orientation=Orientation.Horizontal,Spacing=8};actions.Children.Add(ignore);actions.Children.Add(restore);panel.Children.Add(actions);panel.Children.Add(state);
            if(await Dialog("添加游戏",panel,"添加").ShowAsync()==ContentDialogResult.Primary){var ids=SelectedIDs();if(ids.Length>0){await OwnPost("/api/admin/library/add",new{candidateIDs=ids,userID=library.User.Id,entryPaths=ids.Where(chosenEntries.ContainsKey).ToDictionary(id=>id,id=>chosenEntries[id])});await Refresh();}}
        }catch(Exception e){Status.Text=e.Message;}
    }
    async void SettingsClick(object sender,RoutedEventArgs args)
    {
        if(local!=null){await LocalSettings();return;}
        try{
            gate.CanSwitch();
            var account=new StackPanel{Spacing=12};account.Children.Add(new TextBlock{Text=library!.User.Username,FontSize=20});
            var oldPassword=new PasswordBox{Header="当前密码"};var newPassword=new PasswordBox{Header="新密码"};var passwordButton=new Button{Content="修改密码"};var message=new TextBlock{TextWrapping=TextWrapping.Wrap};
            passwordButton.Click+=async(_,_)=>{try{await OwnPost("/api/account/password",new{oldPassword=oldPassword.Password,newPassword=newPassword.Password});oldPassword.Password="";newPassword.Password="";message.Text="已修改";}catch(Exception e){message.Text=e.Message;}};
            account.Children.Add(oldPassword);account.Children.Add(newPassword);account.Children.Add(passwordButton);account.Children.Add(message);
            if(library.SmbEnrollmentAvailable && api is { } enrollmentOwner)
                account.Children.Add(SmbEnrollmentControls(enrollmentOwner));
            var folders=new StackPanel{Spacing=12};var rootData=await api!.Send<JsonElement>(HttpMethod.Get,"/api/library/roots");var roots=new ListView{DisplayMemberPath="Title",ItemsSource=rootData.GetProperty("roots").Deserialize<Root[]>(AccountStore.Json),Height=230};
            folders.Children.Add(roots);
            if ((library.User.Admin || library.DirectNas) && store is { } nasAccount)
                folders.Children.Add(NasRootControls(roots, nasAccount));
            if(library.User.Admin){var path=new TextBox{Header="资料夹路径"};var name=new TextBox{Header="名称"};var add=new Button{Content="添加资料夹"};var result=new TextBlock{TextWrapping=TextWrapping.Wrap};add.Click+=async(_,_)=>{try{await OwnPost("/api/admin/roots",new{path=path.Text,title=name.Text});result.Text="已添加";}catch(Exception e){result.Text=e.Message;}};folders.Children.Add(path);folders.Children.Add(name);folders.Children.Add(add);folders.Children.Add(result);}
            var server=new StackPanel{Spacing=12};server.Children.Add(new TextBlock{Text=connection!.Address,TextWrapping=TextWrapping.Wrap});
            var collections=new StackPanel{Spacing=12};var collectionName=new TextBox{Header="分类名称"};var create=new Button{Content="新建分类"};var collectionState=new TextBlock();create.Click+=async(_,_)=>{try{await OwnPost("/api/library/collections",new{id=Guid.NewGuid(),name=collectionName.Text});await Refresh();collectionName.Text="";collectionState.Text="已添加";}catch(Exception e){collectionState.Text=e.Message;}};var existing=new ComboBox{Header="分类",DisplayMemberPath="Name",ItemsSource=library.Collections,HorizontalAlignment=HorizontalAlignment.Stretch};var remove=new Button{Content="删除分类"};remove.Click+=async(_,_)=>{try{if(existing.SelectedItem is Collection c){await OwnDelete($"/api/library/collections/{Guid.Parse(c.Id):D}");await Refresh();existing.ItemsSource=library.Collections;collectionState.Text="已删除";}}catch(Exception e){collectionState.Text=e.Message;}};
            collections.Children.Add(collectionName);collections.Children.Add(create);collections.Children.Add(existing);collections.Children.Add(remove);collections.Children.Add(collectionState);
            var pages = new List<(string Title, UIElement Content)>
            {
                ("账号", account), ("资料夹", folders), ("服务器", server), ("分类", collections)
            };
            if (library.User.Admin) pages.Add(("用户", await UsersPanel()));
            var dialog = SettingsDialog(pages, "切换账号");
            if(await dialog.ShowAsync()==ContentDialogResult.Primary){gate.CanSwitch();try{await api.Logout();}finally{connection.Forget();api.Dispose();api=null;library=null;store=null;Covers.ItemsSource=null;Categories.RootNodes.Clear();LibraryPanel.Visibility=Visibility.Collapsed;LoginPanel.Visibility=Visibility.Visible;Password.Password="";Username.Text="";CredentialsFields.Visibility=Visibility.Visible;ConnectionFields.Visibility=Visibility.Collapsed;}}
        }catch(Exception e){Status.Text=e.Message;}
    }
    async Task<UIElement> UsersPanel()
    {
        var panel=new StackPanel{Spacing=10};var editions=await api!.Send<JsonElement>(HttpMethod.Get,"/api/admin/games");var data=await api.Send<JsonElement>(HttpMethod.Get,"/api/admin/users");var users=data.GetProperty("users").Deserialize<Account[]>(AccountStore.Json)!;
        var user=new ComboBox{Header="用户",ItemsSource=users,DisplayMemberPath="Username",HorizontalAlignment=HorizontalAlignment.Stretch};var username=new TextBox{Header="新账号"};var password=new PasswordBox{Header="密码"};var admin=new CheckBox{Content="管理员"};var create=new Button{Content="创建用户"};var disable=new Button{Content="停用用户"};var reset=new Button{Content="重设密码"};var game=new ComboBox{Header="游戏",ItemsSource=editions.GetProperty("games").Deserialize<AdminEdition[]>(AccountStore.Json),DisplayMemberPath="Title",HorizontalAlignment=HorizontalAlignment.Stretch};var grant=new Button{Content="授权"};var revoke=new Button{Content="撤销授权"};var message=new TextBlock{TextWrapping=TextWrapping.Wrap};
        async Task Act(Func<Task> action){try{await action();password.Password="";message.Text="已保存";}catch(Exception e){message.Text=e.Message;}}
        create.Click+=async(_,_)=>await Act(async()=>{await OwnPost("/api/admin/users",new{username=username.Text,password=password.Password,admin=admin.IsChecked==true});var fresh=await api.Send<JsonElement>(HttpMethod.Get,"/api/admin/users");user.ItemsSource=fresh.GetProperty("users").Deserialize<Account[]>(AccountStore.Json);});
        disable.Click+=async(_,_)=>await Act(async()=>{if(user.SelectedItem is Account u)await OwnPost($"/api/admin/users/{Safety.Id(u.Id)}/disable",new{});});
        reset.Click+=async(_,_)=>await Act(async()=>{if(user.SelectedItem is Account u)await OwnPost($"/api/admin/users/{Safety.Id(u.Id)}/password",new{password=password.Password});});
        async Task Grant(bool allowed){if(user.SelectedItem is Account u&&game.SelectedItem is AdminEdition g)await OwnPost("/api/admin/grants",new{userID=u.Id,gameID=g.Id,allowed});}
        grant.Click+=async(_,_)=>await Act(()=>Grant(true));revoke.Click+=async(_,_)=>await Act(()=>Grant(false));
        foreach(var child in new UIElement[]{user,username,password,admin,create,disable,reset,game,grant,revoke,message})panel.Children.Add(child);return panel;
    }
    Task<JsonElement> OwnPost(string route,object body)=>local!=null?local.Mutate(HttpMethod.Post,route,body):api!.Post(route,body);
    Task<JsonElement> OwnDelete(string route)=>local!=null?local.Mutate(HttpMethod.Delete,route):api!.Delete(route);
    async void LocalUseClick(object sender,RoutedEventArgs args){try{await EnterLocal();}catch(Exception e){LoginError.Text=e.Message;}}
    async Task EnterLocal()
    {
        gate.CanSwitch();var epoch=++identityEpoch;api?.Dispose();api=null;store=null;Username.Text="";Password.Password="";var profile=await StandaloneLibrary.Open(Connection.DataRoot);if(epoch!=identityEpoch)return;Connection.SaveMode("local");local=profile;library=local.Library();selected="all";Search.Text="";BuildTree();Filter();LoginPanel.Visibility=Visibility.Collapsed;LibraryPanel.Visibility=Visibility.Visible;
    }
    async Task<global::Windows.Storage.StorageFolder?> PickFolder()
    {
        var picker=new global::Windows.Storage.Pickers.FolderPicker();picker.FileTypeFilter.Add("*");WinRT.Interop.InitializeWithWindow.Initialize(picker,WinRT.Interop.WindowNative.GetWindowHandle(this));
        return await picker.PickSingleFolderAsync();
    }
    async Task PickLocalFolder()
    {
        var folder = await PickFolder();
        if (folder != null) await local!.AddFolder(folder.Path);
    }
    async Task ScanLocal()
    {
        var owner=local!;var list=new ListView{SelectionMode=ListViewSelectionMode.Multiple,DisplayMemberPath="Title",Height=240};var scan=new Button{Content="扫描"};var picker=new ComboBox{Header="Windows 启动入口",HorizontalAlignment=HorizontalAlignment.Stretch};var chosen=new Dictionary<string,string>();LocalCandidate? active=null;var message=new TextBlock{TextWrapping=TextWrapping.Wrap};
        void Show(){list.ItemsSource=owner.Candidates.Where(c=>!owner.Library().Games.Any(g=>g.Id==c.Id)).ToArray();foreach(var c in owner.Candidates)if(c.Entries.Length>0&&!chosen.ContainsKey(c.Id))chosen[c.Id]=c.Entries[0].Path;}
        Show();list.SelectionChanged+=(_,e)=>{if(e.AddedItems.LastOrDefault() is LocalCandidate c){active=c;picker.ItemsSource=c.Entries.Select(e=>e.Path).ToArray();picker.SelectedItem=chosen.GetValueOrDefault(c.Id);}};picker.SelectionChanged+=(_,_)=>{if(active!=null&&picker.SelectedItem is string path)chosen[active.Id]=path;};
        scan.Click+=async(_,_)=>{scan.IsEnabled=false;try{await owner.Scan();Show();}catch(Exception e){message.Text=e.Message;}finally{scan.IsEnabled=true;}};
        var panel=new StackPanel{Spacing=12,Width=430};panel.Children.Add(scan);panel.Children.Add(list);panel.Children.Add(picker);panel.Children.Add(message);
        try{if(await Dialog("添加游戏",panel,"添加").ShowAsync()==ContentDialogResult.Primary){var ids=list.SelectedItems.Cast<LocalCandidate>().Select(c=>c.Id).ToArray();if(ids.Length>0){await owner.AddSelected(ids,chosen);await Refresh();}}}catch(Exception e){Status.Text=e.Message;}
    }
    async Task LocalSettings()
    {
        try{gate.CanSwitch();var folders=new ListView{ItemsSource=local!.Folders,Height=180};var add=new Button{Content="添加资料夹"};var message=new TextBlock{TextWrapping=TextWrapping.Wrap};
            add.Click+=async(_,_)=>{try{await PickLocalFolder();folders.ItemsSource=local!.Folders;}catch(Exception e){message.Text=e.Message;}};
            var collections=new ComboBox{Header="分类",DisplayMemberPath="Name",ItemsSource=library!.Collections,HorizontalAlignment=HorizontalAlignment.Stretch};var removeCollection=new Button{Content="删除分类"};removeCollection.Click+=async(_,_)=>{try{if(collections.SelectedItem is Collection c){await OwnDelete($"/api/library/collections/{Guid.Parse(c.Id):D}");await Refresh();collections.ItemsSource=library!.Collections;}}catch(Exception e){message.Text=e.Message;}};
            var folderPage = new StackPanel { Spacing = 12, Children = { folders, add } };
            var collectionPage = new StackPanel { Spacing = 12, Children = { collections, removeCollection } };
            var dialog = SettingsDialog([("资料夹", folderPage), ("分类", collectionPage)], "连接服务器", message);
            if(await dialog.ShowAsync()==ContentDialogResult.Primary){gate.CanSwitch();identityEpoch++;Connection.SaveMode("server");local=null;library=null;Covers.ItemsSource=null;Categories.RootNodes.Clear();Username.Text="";Password.Password="";LibraryPanel.Visibility=Visibility.Collapsed;LoginPanel.Visibility=Visibility.Visible;connection=Connection.Load();Address.Text=connection?.Address??"";ConnectionFields.Visibility=Visibility.Visible;CredentialsFields.Visibility=Visibility.Collapsed;}
        }catch(Exception e){Status.Text=e.Message;}
    }

}
