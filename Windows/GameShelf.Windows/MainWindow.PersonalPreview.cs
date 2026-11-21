using GameShelf.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace GameShelf.Windows;

public sealed partial class MainWindow
{
    StackPanel NasRootControls(ListView roots, AccountStore account)
    {
        var bindings = new PersonalNasPreview(account);
        var panel = new StackPanel { Spacing = 6 };
        var choose = new Button { Content = "选择此电脑的 NAS 游戏库根目录" };
        var clear = new Button { Content = "移除此设备的目录" };
        var state = new TextBlock { TextWrapping = TextWrapping.Wrap };
        panel.Children.Add(choose);
        panel.Children.Add(clear);
        panel.Children.Add(state);

        void ShowRoot()
        {
            var selected = roots.SelectedItem as GameShelf.Core.Root;
            var folder = selected == null ? null : bindings.RootFolder(selected.Id);
            choose.IsEnabled = selected != null;
            clear.IsEnabled = folder != null;
            state.Text = folder ?? "从已有的 NAS 连接启动，存档由游戏管理。";
        }

        roots.SelectionChanged += (_, _) => ShowRoot();
        choose.Click += async (_, _) =>
        {
            if (roots.SelectedItem is not GameShelf.Core.Root root) return;
            choose.IsEnabled = false;
            try
            {
                var folder = await PickFolder();
                if (folder == null || !panel.IsLoaded || store != account || (roots.SelectedItem as GameShelf.Core.Root)?.Id != root.Id) return;
                gate.CanSwitch();
                await Task.Run(() => bindings.BindRoot(root.Id, folder.Path));
                ShowRoot();
            }
            catch (Exception error) { state.Text = error.Message; }
            finally { choose.IsEnabled = roots.SelectedItem != null; }
        };
        clear.Click += (_, _) =>
        {
            try
            {
                if (store != account || roots.SelectedItem is not GameShelf.Core.Root root) return;
                gate.CanSwitch();
                bindings.ClearRoot(root.Id);
                ShowRoot();
            }
            catch (Exception error) { state.Text = error.Message; }
        };
        if (roots.Items.Count == 1) roots.SelectedIndex = 0;
        ShowRoot();
        return panel;
    }

#if PERSONAL_NAS_PREVIEW
    StackPanel PersonalPreviewControls(LibraryGame game, ComboBox versions, ApiClient owner, AccountStore account)
    {
        var bindings = new PersonalNasPreview(account);
        var panel = new StackPanel { Spacing = 6 };
        var optIn = new CheckBox { Content = "设置此游戏的 NAS 目录" };
        var help = new TextBlock
        {
            Text = "直接从已挂载的 NAS 游戏资料夹运行。存档由游戏管理，两台设备请轮流运行。",
            TextWrapping = TextWrapping.Wrap
        };
        var state = new TextBlock { TextWrapping = TextWrapping.Wrap };
        var bind = new Button { Content = "立即绑定资料夹" };
        var clear = new Button { Content = "移除此版本绑定" };
        var controls = new StackPanel { Spacing = 6, Visibility = Visibility.Collapsed };
        controls.Children.Add(help);
        controls.Children.Add(bind);
        controls.Children.Add(clear);
        panel.Children.Add(optIn);
        panel.Children.Add(controls);
        panel.Children.Add(state);

        (LibraryVariant Variant, LibraryEntry Entry) Selection(LibraryGame current)
        {
            if (versions.SelectedItem is not EntryChoice { VariantID: { } id, Path: { } path })
                return Editions.Select(current with { PreferredEntries = null });
            var variant = current.Variants.Single(v => v.Id == id && v.Platform == "windows" && v.Available);
            return (variant, variant.Entries.Single(e => e.Path == path && Safety.NativeEntry(e.Path, e.Backend)));
        }

        LibraryGame CurrentGame()
        {
            gate.CanSwitch();
            if (api != owner || store != account || local != null)
                throw new InvalidOperationException("当前连接账号已变化。");
            return library!.Games.Single(item => item.Id == game.Id);
        }

        void ShowBinding()
        {
            try
            {
                var (variant, _) = Selection(game);
                var folder = bindings.Folder(variant.Id);
                state.Text = folder == null ? "可在「设置 → 资料夹」为整库选择 NAS 根目录。" : "已绑定此设备 · " + folder;
                bind.IsEnabled = true;
                clear.IsEnabled = folder != null;
            }
            catch (Exception error) { state.Text = error.Message; bind.IsEnabled = clear.IsEnabled = false; }
        }

        optIn.Checked += (_, _) => controls.Visibility = Visibility.Visible;
        optIn.Unchecked += (_, _) => controls.Visibility = Visibility.Collapsed;
        versions.SelectionChanged += (_, _) => ShowBinding();
        bind.Click += async (_, _) =>
        {
            bind.IsEnabled = false;
            try
            {
                var before = Selection(CurrentGame());
                var folder = await PickFolder();
                if (folder == null || !panel.IsLoaded || optIn.IsChecked != true) return;
                var current = CurrentGame();
                var after = Selection(current);
                if (before.Variant.Id != after.Variant.Id || before.Entry != after.Entry)
                    throw new InvalidOperationException("Windows 版本已变化，请重新选择资料夹。");
                bindings.Bind(current, after.Variant.Id, after.Entry, folder.Path);
                ShowBinding();
            }
            catch (Exception error) { state.Text = error.Message; }
            finally { bind.IsEnabled = true; }
        };
        clear.Click += (_, _) =>
        {
            try { bindings.Clear(Selection(CurrentGame()).Variant.Id); ShowBinding(); }
            catch (Exception error) { state.Text = error.Message; }
        };
        ShowBinding();
        return panel;
    }
#endif
}
