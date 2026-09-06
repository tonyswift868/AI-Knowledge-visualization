using System;
using System.Windows.Forms;

internal static class LoginFormProbeFixture
{
    [STAThread]
    private static void Main()
    {
        var form = new Form { Text = "E107 isolated form probe fixture" };
        var username = new TextBox { Top = 15, Left = 15 };
        var password = new TextBox { Top = 50, Left = 15, UseSystemPasswordChar = true };
        form.Controls.Add(username);
        form.Controls.Add(password);
        form.Shown += delegate { Console.WriteLine(form.Handle.ToInt64()); Console.Out.Flush(); };
        Application.Run(form);
    }
}
