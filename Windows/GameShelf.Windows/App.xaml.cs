using Microsoft.UI.Xaml;
namespace GameShelf.Windows;
public partial class App:Application
{
    Window? window;
    public App()
    {
        UnhandledException+=(_,e)=>StartupLog(e.Exception.ToString());
        StartupLog("App starting");InitializeComponent();StartupLog("Application resources loaded");
    }
    internal static void StartupLog(string text)
    {
        // Startup only: never include login requests, passwords or lease share objects.
        try{var path=Connection.DataRoot;Directory.CreateDirectory(path);File.AppendAllText(Path.Combine(path,"startup.log"),text+Environment.NewLine);}catch{}
    }
    protected override void OnLaunched(LaunchActivatedEventArgs args){StartupLog("Creating window");window=new MainWindow();StartupLog("Activating window");window.Activate();StartupLog("Window activated");}
}
