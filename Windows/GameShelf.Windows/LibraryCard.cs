using System.ComponentModel;
using GameShelf.Core;
namespace GameShelf.Windows;
public sealed class LibraryCard(LibraryGame game,double width):INotifyPropertyChanged
{
    public string Id=>game.Id;
    public string Title=>game.Title;
    public string TimeLabel=>game.TimeLabel;
    double width=width;
    public double Width {get=>width;set{if(Math.Abs(width-value)<0.5)return;width=value;PropertyChanged?.Invoke(this,new(nameof(Width)));PropertyChanged?.Invoke(this,new(nameof(CoverHeight)));}}
    public double CoverHeight=>Width*1.42;
    public event PropertyChangedEventHandler? PropertyChanged;
}
