using GameShelf.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace GameShelf.Windows;

public sealed partial class MainWindow
{
    UIElement SmbEnrollmentControls(ApiClient owner)
    {
        var panel = new StackPanel { Spacing = 8 };
        var username = new TextBox { Header = "SMB 用户名", MaxLength = 64 };
        var connect = new Button { Content = "验证 NAS 存档访问" };
        var message = new TextBlock { TextWrapping = TextWrapping.Wrap };
        panel.Children.Add(username);
        panel.Children.Add(connect);
        panel.Children.Add(message);
        connect.Click += async (_,_) =>
        {
            if (api != owner) return;
            connect.IsEnabled = false;
            message.Text = "";
            try
            {
                const string path = "/api/account/smb-enrollment";
                var status = await owner.Send<SmbEnrollmentStatus>(HttpMethod.Get,path);
                if (api != owner) return;
                if (status.State == "unverified")
                {
                    var challenge = await owner.Send<SmbEnrollmentChallenge>(HttpMethod.Post,path+"/challenge",
                        new { username=username.Text.Trim() });
                    var mappedRoot = await ShellMappingBackend.ExistingShareRoot(challenge.Share,CancellationToken.None);
                    var proof = await challenge.ReadProof(mappedRoot,CancellationToken.None);
                    if (api != owner) return;
                    status = await owner.Send<SmbEnrollmentStatus>(HttpMethod.Post,path+"/proof",
                        new { challengeID=challenge.ChallengeID, proof });
                }
                if (api != owner) return;
                if (status.State == "verified")
                    status = await owner.Send<SmbEnrollmentStatus>(HttpMethod.Post,path+"/workspace",new {});
                if (api != owner) return;
                if (status.State != "ready" || string.IsNullOrEmpty(status.Share))
                    throw new InvalidDataException("NAS 存档工作区未就绪。");
                await Refresh();
                if (api == owner)
                    message.Text = $"请在资源管理器中将 NAS 的「{status.Share}」共享映射为网络驱动器。";
            }
            catch (Exception error)
            {
                if (api == owner) message.Text = error.Message;
            }
            finally
            {
                connect.IsEnabled = true;
            }
        };
        return panel;
    }
}
