using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;

namespace TokenMeter.Pet;

internal static class Program
{
    private static readonly object OutputLock = new();
    private static StreamWriter? output;

    [STAThread]
    private static int Main(string[] args)
    {
        string? dataDirectory = Value(args, "--data-dir");
        if (dataDirectory == null) return 2;
        Directory.CreateDirectory(dataDirectory);
        bool demo = Array.IndexOf(args, "--demo") >= 0;
        bool smoke = Array.IndexOf(args, "--smoke-test") >= 0;
        string? captureDirectory = Value(args, "--capture-dir");
        int.TryParse(Value(args, "--parent-pid"), out int parentId);
        // 原版内核会写 Console；协议使用独立输出句柄，防止调试文本被当成命令。
        output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
        Console.SetOut(Console.Error);
        var app = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
        app.Resources.MergedDictionaries.Add(new ResourceDictionary {
            Source = new Uri("/VPet-Simulator.Core;component/Display/Theme.xaml", UriKind.Relative)
        });
        app.Resources.MergedDictionaries.Add(new ResourceDictionary {
            Source = new Uri("/VPet-Simulator.Core;component/Display/basestyle.xaml", UriKind.Relative)
        });
        app.DispatcherUnhandledException += (_, e) => {
            File.AppendAllText(Path.Combine(dataDirectory, "host-error.log"), e.Exception + "\n");
            Send(new { @event = "error", message = "桌宠运行异常，已返回悬浮球。" });
            e.Handled = true;
            app.Shutdown(1);
        };
        var window = new PetWindow(dataDirectory, demo, smoke, captureDirectory);
        app.MainWindow = window;
        if (!demo && !smoke)
        {
            _ = Task.Run(async () => {
                try
                {
                    using var input = new StreamReader(Console.OpenStandardInput(), Encoding.UTF8);
                    while (await input.ReadLineAsync() is { } line)
                    {
                        if (line.Length > 65536) continue;
                        try
                        {
                            using var document = JsonDocument.Parse(line);
                            var command = document.RootElement.Clone();
                            await app.Dispatcher.InvokeAsync(() => window.Receive(command));
                        }
                        catch (JsonException) { }
                    }
                }
                finally
                {
                    // 父进程退出或管道断开必须同时结束桌宠，避免留下独立常驻进程。
                    if (!app.Dispatcher.HasShutdownStarted)
                        _ = app.Dispatcher.BeginInvoke(() => app.Shutdown());
                }
            });
        }
        if (parentId > 0)
        {
            var parentWatch = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            parentWatch.Tick += (_, _) => {
                try { using var parent = Process.GetProcessById(parentId); if (!parent.HasExited) return; }
                catch (ArgumentException) { }
                parentWatch.Stop();
                app.Shutdown();
            };
            parentWatch.Start();
        }
        window.Show();
        return app.Run();
    }

    internal static void Send(object value)
    {
        lock (OutputLock)
        {
            try { output?.WriteLine(JsonSerializer.Serialize(value)); }
            catch (IOException) { Application.Current.Dispatcher.BeginInvoke(() => Application.Current.Shutdown()); }
        }
    }

    private static string? Value(string[] args, string name)
    {
        int index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }
}
