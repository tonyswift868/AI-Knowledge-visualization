// Diagnostic only. Never reads Name/Value/password text or invokes a button.
using System;
using System.Diagnostics;
using System.Threading;
using System.Windows.Automation;

internal static class LoginFormProbe
{
    [MTAThread]
    private static int Main(string[] args)
    {
        if (args.Length != 2) return 2;
        var handle = new IntPtr(long.Parse(args[0]));
        int pid = int.Parse(args[1]);
        var timer = Stopwatch.StartNew();
        int editsSeen = 0;
        while (timer.Elapsed.TotalSeconds < 25)
        {
            try
            {
                var window = AutomationElement.FromHandle(handle);
                if (window.Current.ProcessId != pid) return 3;
                var edits = window.FindAll(TreeScope.Descendants,
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Edit));
                editsSeen = Math.Max(editsSeen, edits.Count);
                AutomationElement username = null;
                bool password = false;
                foreach (AutomationElement edit in edits)
                {
                    var state = edit.Current;
                    if (!state.IsEnabled || state.IsOffscreen || !state.IsKeyboardFocusable) continue;
                    if (state.IsPassword) password = true;
                    else
                    {
                        object pattern;
                        if (edit.TryGetCurrentPattern(ValuePattern.Pattern, out pattern)
                            && !((ValuePattern)pattern).Current.IsReadOnly) username = edit;
                    }
                }
                if (username != null && password)
                {
                    // Focus acknowledgment, without typing or reading a value.
                    username.SetFocus();
                    if (username.Current.HasKeyboardFocus)
                    {
                        Console.WriteLine("FORM_CONTROLS_FOCUS_READY");
                        return 0;
                    }
                }
            }
            catch (ElementNotAvailableException) { }
            catch (InvalidOperationException) { }
            Thread.Sleep(100);
        }
        Console.WriteLine("FORM_NOT_OBSERVED edits=" + editsSeen);
        return 4;
    }
}
