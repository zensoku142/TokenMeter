using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using VPet_Simulator.Core;

// 仅测试进程使用反射驱动真实宿主，不向生产通信协议增加调试指令。
internal static class Program
{
    private static readonly Type PetType = Assembly.Load("TokenMeter.Pet").GetType("TokenMeter.Pet.PetWindow")!;
    private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic;
    private static Window petWindow = null!;
    private static StreamWriter output = null!;
    private static int measuredCollections, measuredMoves, measuredTicks;
    private static readonly SemaphoreSlim BuildSlots = (SemaphoreSlim)typeof(PNGAnimation)
        .GetField("BuildSlots", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
    private static bool preparationsBlocked;
    private static readonly List<string> playedState = new();
    private static string[] expectedState = Array.Empty<string>();

    private static object? Get(string name) => PetType.GetField(name, Fields)!.GetValue(petWindow);
    private static void Set(string name, object value) => PetType.GetField(name, Fields)!.SetValue(petWindow, value);
    private static object? Call(string name, params object[] args) => PetType.GetMethod(name, Fields)!.Invoke(petWindow, args);
    private static DispatcherTimer Timer(string name) => (DispatcherTimer)Get(name)!;
    private static bool Deferred(IGraph animation) => animation is
        APNGAnimation { DeferredLoading: true } or PNGAnimation { DeferredLoading: true };
    private static string StatePart(GraphInfo info) => $"{info.Name}:{info.Type}:{info.Animat}:{info.ModeType}";
    private static void RecordState(GraphInfo info)
    {
        if (info.Name == "state") lock (playedState) playedState.Add(StatePart(info));
    }

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
        petWindow = (Window)Activator.CreateInstance(PetType, args[0], false, false, null,
            args[1] == "default" ? null : args[1], 0L)!;
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
        if (action == "resize") { Call("ResizePet", command.GetProperty("delta").GetInt32()); return Snapshot(); }
        if (action == "switch") { _ = Call("ChangeCharacterAsync", command.GetProperty("character").GetString()!); return Snapshot(); }
        if (action == "play") { Call("PlayBailongAction", command.GetProperty("id").GetString()!); return Snapshot(); }
        if (action == "play_whale") { _ = Call("PlayWhaleActionAsync", command.GetProperty("name").GetString()!); return Snapshot(); }
        if (action == "graph_event")
        {
            Call("OnPetGraphDisplayed", ((VPet_Simulator.Core.Main)Get("pet")!).DisplayType);
            return Snapshot();
        }
        if (action == "action_ready")
        {
            var graph = (GraphCore)Get("graph")!;
            var animation = graph.GraphsALL.First(item => item.GraphInfo.Name == "bailong.action." +
                command.GetProperty("id").GetString());
            return new { ready = animation.IsReady, failed = animation.IsFail };
        }
        if (action == "deferred_ready")
        {
            var graph = (GraphCore)Get("graph")!;
            var animation = graph.GraphsALL[command.GetProperty("index").GetInt32()];
            return new { ready = animation.IsReady, failed = animation.IsFail };
        }
        if (action == "play_deferred")
        {
            var graph = (GraphCore)Get("graph")!;
            var animation = graph.GraphsALL.Last(item => Deferred(item) && !item.IsReady &&
                !item.IsLoop && item.GraphInfo.Type is
                    (GraphInfo.GraphType.Idel or GraphInfo.GraphType.StateONE or GraphInfo.GraphType.StateTWO));
            petWindow.IsHitTestVisible = false;
            Set("allowMove", true);
            Set("manualDockedEdge", null!);
            Call("StartAutonomousSequence", new List<IGraph> { animation });
            return new { name = animation.GraphInfo.Name, index = graph.GraphsALL.IndexOf(animation),
                pending = Get("autonomousSequence") != null };
        }
        if (action == "state_start")
        {
            var graph = (GraphCore)Get("graph")!;
            var first = graph.GraphsALL.First(item => item.GraphInfo.Type == GraphInfo.GraphType.StateONE &&
                item.GraphInfo.Animat == GraphInfo.AnimatType.A_Start && item.GraphInfo.ModeType == IGameSave.ModeType.Happy);
            var sequence = (List<IGraph>)Call("BuildAutonomousSequence",
                (first.GraphInfo.Type, first.GraphInfo.Name, first.GraphInfo.ModeType))!;
            lock (playedState)
            {
                playedState.Clear();
                expectedState = sequence.Select(item => StatePart(item.GraphInfo)).ToArray();
            }
            var main = (VPet_Simulator.Core.Main)Get("pet")!;
            main.GraphDisplayHandler -= RecordState;
            main.GraphDisplayHandler += RecordState;
            petWindow.IsHitTestVisible = false;
            Set("allowMove", true);
            Set("manualDockedEdge", null!);
            Call("StartAutonomousSequence", sequence);
            return Snapshot();
        }
        if (action == "state_result")
        {
            lock (playedState) return new { active = Get("autonomousSequence") != null,
                display = ((VPet_Simulator.Core.Main)Get("pet")!).DisplayType.Type.ToString(),
                expected = expectedState, played = playedState.ToArray() };
        }
        if (action == "block_preparations" && !preparationsBlocked)
        {
            // 测试专用的解码闸门让等待期间的取消路径可重复，不依赖机器快慢。
            if (!BuildSlots.Wait(10000)) throw new TimeoutException("First build slot was not released");
            if (!BuildSlots.Wait(10000)) { BuildSlots.Release(); throw new TimeoutException("Second build slot was not released"); }
            preparationsBlocked = true;
        }
        if (action == "release_preparations" && preparationsBlocked)
        {
            preparationsBlocked = false;
            BuildSlots.Release(2);
        }
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
        var core = (GraphCore)Get("graph")!;
        var main = (VPet_Simulator.Core.Main)visual;
        var scale = visual.RenderTransform as ScaleTransform;
        var point = petWindow.PointToScreen(new Point());
        var dpi = VisualTreeHelper.GetDpi(petWindow);
        var pixels = (string)Get("character")! == "vpet" ? new Rect(0, 0, 1, 1) :
            (Rect)Call("PetVisiblePixels", Get("character")!)!;
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
            character = Get("character"), size = Get("size"), display = main.DisplayType.Name,
            manualPending = Get("bailongManualFrame") != null,
            autonomousActive = Get("autonomousSequence") != null,
            deferred = core.GraphsALL.Count(Deferred),
            deferredUnready = core.GraphsALL.Count(animation => Deferred(animation) && !animation.IsReady),
            requiredReady = ((IGraph[])Call("RequiredAnimations", core)!).All(animation => animation.IsReady),
            fullCollections = GC.CollectionCount(2) - measuredCollections, moves = measuredMoves, physicsTicks = measuredTicks,
            dpi = dpi.DpiScaleX, work = new { x = work.X, y = work.Y, width = work.Width, height = work.Height }
        };
    }

    private static void Reply(object result) => output.WriteLine(JsonSerializer.Serialize(result));
}
