using System;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;

// 仅测试进程使用反射驱动真实宿主，不向生产通信协议增加调试指令。
internal static class Program
{
    private static readonly Type PetType = Assembly.Load("TokenMeter.Pet").GetType("TokenMeter.Pet.PetWindow")!;
    private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic;
    private static Window petWindow = null!;
    private static StreamWriter output = null!;
    private static int measuredCollections, measuredMoves, measuredTicks;

    private static object? Get(string name) => PetType.GetField(name, Fields)!.GetValue(petWindow);
    private static void Set(string name, object value) => PetType.GetField(name, Fields)!.SetValue(petWindow, value);
    private static object? Call(string name, params object[] args) => PetType.GetMethod(name, Fields)!.Invoke(petWindow, args);
    private static DispatcherTimer Timer(string name) => (DispatcherTimer)Get(name)!;

    private static void ChangeVisibility(bool visible)
    {
        using var command = JsonDocument.Parse(JsonSerializer.Serialize(new { type = "visibility", visible }));
        Call("Receive", command.RootElement);
    }

    [STAThread]
    private static int Main(string[] args)
    {
        output = new StreamWriter(Console.OpenStandardOutput()) { AutoFlush = true };
        Console.SetOut(Console.Error);
        var app = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
        foreach (string name in new[] { "Theme", "basestyle" })
            app.Resources.MergedDictionaries.Add(new ResourceDictionary {
                Source = new Uri($"/VPet-Simulator.Core;component/Display/{name}.xaml", UriKind.Relative)
            });
        petWindow = (Window)Activator.CreateInstance(PetType, args[0], false, false, null, args[1], 0L)!;
        app.MainWindow = petWindow;
        petWindow.Loaded += async (_, _) => {
            while (!(bool)Get("ready")!) await Task.Delay(20);
            // ready 在宿主初始化中途置位；让该次 Dispatcher 回调完成后再接受指令。
            await Task.Delay(100);
            petWindow.LocationChanged += (_, _) => ++measuredMoves;
            Timer("whaleThrowTimer").Tick += (_, _) => ++measuredTicks;
            Reply(Snapshot());
            _ = Task.Run(async () => {
                while (await Console.In.ReadLineAsync() is { } line)
                {
                    using var command = JsonDocument.Parse(line);
                    var data = command.RootElement.Clone();
                    var result = await app.Dispatcher.InvokeAsync(() => Execute(data));
                    Reply(result);
                    if (data.GetProperty("action").GetString() == "close") break;
                }
            });
        };
        petWindow.Show();
        return app.Run();
    }

    private static object Execute(JsonElement command)
    {
        string action = command.GetProperty("action").GetString()!;
        if (action == "close") { petWindow.Close(); return new { closed = true }; }
        if (action == "hide") { ChangeVisibility(false); return Snapshot(); }
        if (action == "show") { ChangeVisibility(true); return Snapshot(); }
        if (action == "measure")
        {
            measuredCollections = GC.CollectionCount(2);
            measuredMoves = measuredTicks = 0;
        }
        if (action == "gesture")
        {
            var point = petWindow.PointToScreen(new Point(100, 100));
            if (!(bool)Call("StartPetGesture", point, new Point(250, 250))!)
                throw new InvalidOperationException("Cannot capture the test gesture");
            // 合成采样期间不让出 Dispatcher，避免没有真实按键的 MouseMove 取消手势。
            Call("UpdatePetGesture", point + new Vector(60, -20));
            System.Threading.Thread.Sleep(30);
            Call("UpdatePetGesture", point + new Vector(180, -75));
            System.Threading.Thread.Sleep(30);
            Call("UpdatePetGesture", point + new Vector(320, -130));
            if (command.TryGetProperty("pause", out var pause)) System.Threading.Thread.Sleep(pause.GetInt32());
            Call("EndPetGesture", false);
            return Snapshot();
        }
        if (action == "place" || action == "launch" || action == "drag" || action == "step")
        {
            Set("petPointerDown", false);
            Set("petDragging", false);
            Call("StopWhaleMotion");
            Set("whaleLastImpact", 0L);
            Set("manualDockedEdge", null!);
            ChangeVisibility(true);
            petWindow.Left = command.GetProperty("x").GetDouble();
            petWindow.Top = command.GetProperty("y").GetDouble();
            if (command.TryGetProperty("size", out var size))
            {
                Set("size", size.GetInt32());
                petWindow.Width = petWindow.Height = size.GetInt32();
            }
            petWindow.UpdateLayout();
            var point = petWindow.PointToScreen(new Point());
            Set("whaleThrowX", point.X);
            Set("whaleThrowY", point.Y);
            Set("whaleThrowVx", command.TryGetProperty("vx", out var vx) ? vx.GetDouble() : 0.0);
            Set("whaleThrowVy", command.TryGetProperty("vy", out var vy) ? vy.GetDouble() : 0.0);
            if (action == "drag")
            {
                Set("petPointerDown", true);
                Set("petDragging", true);
                Set("whaleSpringX", point.X);
                Set("whaleSpringY", point.Y);
                Set("whaleTargetX", point.X + command.GetProperty("dx").GetDouble());
                Set("whaleTargetY", point.Y);
                Set("whaleSpringVx", 0.0);
                Set("whaleSpringVy", 0.0);
                Set("whaleLastPhysicsTick", System.Diagnostics.Stopwatch.GetTimestamp());
                Timer("whaleSpringTimer").Start();
            }
            else if (action != "place")
            {
                Set("whaleLastPhysicsTick", System.Diagnostics.Stopwatch.GetTimestamp());
                Timer("whaleThrowTimer").Start();
                if (action == "step")
                {
                    Call("AdvanceWhaleThrowStep", command.GetProperty("dt").GetDouble());
                    Timer("whaleThrowTimer").Stop();
                }
            }
            Call("SyncAutonomy");
        }
        return Snapshot();
    }

    private static object Snapshot()
    {
        var visual = (FrameworkElement)Get("pet")!;
        var scale = visual.RenderTransform as ScaleTransform;
        var point = petWindow.PointToScreen(new Point());
        var dpi = VisualTreeHelper.GetDpi(petWindow);
        var pixels = (Rect)Call("PetVisiblePixels", Get("character")!)!;
        double width = petWindow.ActualWidth * dpi.DpiScaleX, height = petWindow.ActualHeight * dpi.DpiScaleY;
        var bounds = new Rect(point.X + pixels.Left * width, point.Y + pixels.Top * height,
            pixels.Width * width, pixels.Height * height);
        var work = System.Windows.Forms.Screen.FromHandle(new WindowInteropHelper(petWindow).Handle).WorkingArea;
        return new {
            x = point.X, y = point.Y, bounds = new { x = bounds.X, y = bounds.Y, width = bounds.Width, height = bounds.Height },
            vx = Get("whaleThrowVx"), vy = Get("whaleThrowVy"),
            throwing = Timer("whaleThrowTimer").IsEnabled,
            scaleX = scale?.ScaleX ?? 1, scaleY = scale?.ScaleY ?? 1,
            impact = Get("whaleLastImpact"), visible = petWindow.IsVisible,
            fullCollections = GC.CollectionCount(2) - measuredCollections, moves = measuredMoves, physicsTicks = measuredTicks,
            dpi = dpi.DpiScaleX, work = new { x = work.X, y = work.Y, width = work.Width, height = work.Height }
        };
    }

    private static void Reply(object result) => output.WriteLine(JsonSerializer.Serialize(result));
}
