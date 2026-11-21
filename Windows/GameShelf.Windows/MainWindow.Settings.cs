using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace GameShelf.Windows;

public sealed partial class MainWindow
{
    ContentDialog SettingsDialog(
        IReadOnlyList<(string Title, UIElement Content)> pages,
        string primary,
        TextBlock? status = null)
    {
        var layout = new Grid { Width = 560, Height = 420, ColumnSpacing = 20 };
        layout.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(132) });
        layout.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

        var navigation = new ListView
        {
            ItemsSource = pages.Select(page => page.Title).ToArray(),
            SelectionMode = ListViewSelectionMode.Single,
            IsItemClickEnabled = false
        };
        layout.Children.Add(navigation);

        var heading = new TextBlock { FontSize = 24 };
        var scroll = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollMode = ScrollMode.Disabled
        };
        var detail = new Grid { RowSpacing = 12 };
        detail.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        detail.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        detail.Children.Add(heading);
        Grid.SetRow(scroll, 1);
        detail.Children.Add(scroll);
        if (status is not null)
        {
            detail.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetRow(status, 2);
            detail.Children.Add(status);
        }
        Grid.SetColumn(detail, 1);
        layout.Children.Add(detail);

        navigation.SelectionChanged += (_, _) =>
        {
            if (navigation.SelectedIndex < 0) return;
            var page = pages[navigation.SelectedIndex];
            heading.Text = page.Title;
            scroll.Content = page.Content;
            scroll.ChangeView(null, 0, null, true);
        };
        navigation.SelectedIndex = 0;
        return Dialog("设置", layout, primary);
    }
}
