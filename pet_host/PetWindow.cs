using LinePutScript;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using VPet_Simulator.Core;
using PetMain = VPet_Simulator.Core.Main;
using static VPet_Simulator.Core.GraphInfo;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow : Window, IController
{
    private readonly string dataDirectory;
    private readonly bool demo;
    private readonly bool smoke;
    private readonly string? captureDirectory;
    private readonly string resources = Path.Combine(AppContext.BaseDirectory, "resources");
    private readonly DispatcherTimer saveTimer = new() { Interval = TimeSpan.FromSeconds(30) };
    private readonly DispatcherTimer ambientTimer = new() { Interval = TimeSpan.FromSeconds(15) };
    private readonly TextBlock loading = new() { Text = "正在加载 VPet…", Foreground = Brushes.White,
        Background = Brushes.DimGray, Padding = new Thickness(12) };
    // 原版渲染接口仍需要名字与动画状态，但不再读取、计算或保存养成数值。
    private readonly GameSave save = new("萝莉斯");
    private PetMain? pet;
    private GraphCore? graph;
    private QuotaWindow? quota;
    private QuotaCloudWindow? quotaCloud;
    private ContextMenu? petMenu;
    private MenuItem? quotaMenuItem;
    private MenuItem? autonomyMenuItem;
    private bool closing;
    private bool ready;
    private bool visible = true;
    private bool allowMove = true;
    private bool quotaPinned = true;
    private bool cloudEnabled;
    private bool? cloudDockedState;
    private int size = 220;
    private JsonElement? pendingUsage;
    private string? lastWarning;
    private Point? quotaPosition;

    public PetWindow(string dataDirectory, bool demo, bool smoke, string? captureDirectory)
    {
        this.dataDirectory = dataDirectory;
        this.demo = demo;
        this.smoke = smoke;
        this.captureDirectory = captureDirectory;
        Title = "TokenMeter · VPet 精简版";
        WindowStyle = WindowStyle.None;
        ResizeMode = ResizeMode.NoResize;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        // 独立演示保留任务栏入口，便于找回窗口并验证真实鼠标操作；集成模式仍只使用主程序托盘。
        ShowInTaskbar = demo;
        ShowActivated = false;
        SourceInitialized += (_, _) => {
            if (demo) return;
            var handle = new WindowInteropHelper(this).Handle;
            // ShowInTaskbar 只控制任务栏；透明无边框窗还需 TOOLWINDOW 并移除 APPWINDOW，才能排除 Alt+Tab。
            // 不加 NOACTIVATE 或鼠标穿透，保留原有触摸、拖动和右键菜单交互。
            SetWindowLong(handle, -20, (GetWindowLong(handle, -20) | 0x80) & ~0x40000);
        };
        Topmost = true;
        Content = loading;
        LoadState();
        Width = Height = size;
        ClampPosition();
        Loaded += async (_, _) => await LoadPet();
        LocationChanged += (_, _) => UpdateQuotaCloud();
        SizeChanged += (_, _) => { UpdateMessageTextSize(); UpdateQuotaCloud(); };
        Closing += (_, _) => {
            if (closing) return;
            closing = true;
            if (petMenu != null) petMenu.IsOpen = false;
            EndPetGesture(cancel: true);
            DisposeNotifications();
            SaveState();
            saveTimer.Stop();
            ambientTimer.Stop();
            if (pet != null) pet.GraphDisplayHandler -= OnPetGraphDisplayed;
            quotaCloud?.Close();
            quota?.CloseForShutdown();
            pet?.Dispose();
            graph?.Dispose();
        };
    }

    private async Task LoadPet()
    {
        try
        {
            GraphCore.CachePath = Path.Combine(dataDirectory, "cache");
            var loader = new PetLoader(new LpsDocument(File.ReadAllText(Path.Combine(resources, "pet", "vup.lps"))),
                new DirectoryInfo(Path.Combine(resources, "pet")));
            // 250px 足够常驻显示；直接使用原版内核的分辨率缓存，不修改原始动画素材。
            graph = await Task.Run(() => loader.Graph(250, Dispatcher));
            if (closing) { graph.Dispose(); return; }
            pet = new PetMain(new GameCore { Controller = this, Graph = graph, Save = save });
            // 原版事件计时器混合了睡眠和养成逻辑；改用宿主计时器仅触发走动与待机动作。
            pet.EventTimer.Stop();
            await Task.Run(() => pet.LoadALL());
            if (closing) return;
            if (pet.ErrorMessage.Count != 0 || !pet.IsWorking)
                throw new InvalidDataException("VPet animation load failed: " + string.Join("\n", pet.ErrorMessage));
            pet.Resources = Application.Current.Resources;
            pet.ToolBar.Resources = Application.Current.Resources;
            pet.MsgBar.This.Resources = Application.Current.Resources;
            // 内核仍保留角色名以兼容原版资源；提醒只显示正文，不展示称呼或预留标题空行。
            if (pet.MsgBar.This.FindName("LName") is FrameworkElement messageName)
                messageName.Visibility = Visibility.Collapsed;
            UpdateMessageTextSize();
            // 轻点仅用于抚摸等角色交互；所有操作统一由右键菜单提供。
            pet.DefaultClickAction = null;
            AttachPetDragging();
            pet.SetMoveMode(allowMove, false, 1200000);
            BuildMenus();
            Content = pet;
            // 独立额度窗只用于没有主程序的演示/测试；正常运行统一打开原有主程序面板。
            if (demo || smoke)
            {
                quota = new QuotaWindow(demo, () => Request("open_panel"), () => {
                    quotaPinned = false;
                    SaveState();
                }, ShowCredits) { Owner = this };
                quota.Left = Math.Clamp(Left + Width + 10, SystemParameters.WorkArea.Left,
                    SystemParameters.WorkArea.Right - quota.Width);
                quota.Top = Math.Clamp(Top, SystemParameters.WorkArea.Top,
                    SystemParameters.WorkArea.Bottom - quota.Height);
                if (quotaPosition is { } savedPosition)
                {
                    quota.Left = Math.Clamp(savedPosition.X, SystemParameters.WorkArea.Left,
                        SystemParameters.WorkArea.Right - quota.Width);
                    quota.Top = Math.Clamp(savedPosition.Y, SystemParameters.WorkArea.Top,
                        SystemParameters.WorkArea.Bottom - quota.Height);
                }
            }
            quotaCloud = new QuotaCloudWindow(demo, () => Request("open_panel")) { Owner = this };
            pet.GraphDisplayHandler += OnPetGraphDisplayed;
            pet.MouseEnter += (_, _) => quotaCloud.NotifyActivity();
            pet.MouseMove += (_, e) => quotaCloud.NotifyPointerMovement(PointToScreen(e.GetPosition(this)));
            pet.MouseLeave += (_, _) => quotaCloud.NotifyActivity();
            ready = true;
            InitializeNotifications();
            if (pendingUsage is { } usage) UpdateUsage(usage);
            UpdateQuotaCloud();
            saveTimer.Tick += (_, _) => SaveState();
            saveTimer.Start();
            // 冒烟检查会主动调用真实动作；后台随机不能干扰其坐标和生命周期断言。
            ambientTimer.Tick += (_, _) => { if (!smoke) RunAutonomousBehavior(); };
            SyncAutonomy();
            Program.Send(new { @event = "ready", animations = graph.GraphsALL.Count });
            if (captureDirectory != null || smoke)
                await RunVisualCheck();
        }
        catch (Exception ex)
        {
            File.AppendAllText(Path.Combine(dataDirectory, "host-error.log"), ex + "\n");
            Program.Send(new { @event = "error", message = "桌宠资源加载失败，已返回悬浮球。" });
            Application.Current.Shutdown(1);
        }
    }

    private void BuildMenus()
    {
        var toolbar = pet!.ToolBar;
        // 从可视树移除整个原版工具栏，不能只隐藏按钮，否则点击角色时仍可能再次弹出。
        toolbar.Visibility = Visibility.Collapsed;
        pet.UIGrid.Children.Remove(toolbar);
        pet.TimeUIHandle -= toolbar.M_TimeUIHandle;
        pet.UIGrid.Children.Remove(pet.WorkTimer);
        petMenu = new ContextMenu { PlacementTarget = this, Placement = PlacementMode.MousePoint };
        ContextMenu = petMenu;
        MenuItem Add(string title, Action action)
        {
            var item = new MenuItem { Header = title };
            item.Click += (_, _) => action();
            petMenu.Items.Add(item);
            return item;
        }
        Add("查看用量面板", () => Request("open_panel"));
        quotaMenuItem = Add("显示额度气泡", () => {
            // 以点击时的状态应用手动选择，避免尚未处理的动画回调把本次开关覆盖掉。
            cloudDockedState = LogicalDockedEdge.HasValue;
            cloudManualChoice = quotaMenuItem!.IsChecked;
            cloudEnabled = quotaMenuItem!.IsChecked;
            if (!cloudEnabled && activeNotice == Notice.Quota) FinishNotification();
            UpdateQuotaCloud();
            SaveState();
        });
        quotaMenuItem.IsCheckable = true;
        AddNotificationMenus();
        petMenu.Items.Add(new Separator());
        autonomyMenuItem = Add("自主活动", () => {
            allowMove = autonomyMenuItem!.IsChecked;
            if (!allowMove) CancelAutonomousSequence();
            // WPF 可能先关闭弹出菜单再发送 Click；两种事件顺序都要应用最新的开关值。
            SyncAutonomy();
            SaveState();
        });
        autonomyMenuItem.IsCheckable = true;
        Add("放大桌宠", () => ResizePet(20));
        Add("缩小桌宠", () => ResizePet(-20));
        Add("TokenMeter 设置", () => Request("open_settings"));
        Add("返回悬浮球", () => Request("disable_pet"));
        petMenu.Items.Add(new Separator());
        Add("默认角色来源与授权", ShowCredits);
        Add("退出 TokenMeter", () => Request("quit"));
        petMenu.Opened += (_, _) => {
            CancelAutonomousSequence();
            UpdateQuotaCloud();
            quotaMenuItem.IsChecked = cloudEnabled;
            quotaCloud?.NotifyActivity();
            autonomyMenuItem.IsChecked = allowMove;
            RefreshNotificationMenus();
            // 菜单打开期间暂停自主移动，避免点击目标随宠物移动；关闭后按用户开关恢复。
            ambientTimer.Stop();
            pet.SetMoveMode(false, false, 1200000);
        };
        petMenu.Closed += (_, _) => {
            quotaCloud?.NotifyActivity();
            SyncAutonomy();
            UpdateCloudPointer();
        };
        petMenu.MouseMove += (_, e) => quotaCloud?.NotifyPointerMovement(petMenu.PointToScreen(e.GetPosition(petMenu)));
        PreviewMouseDown += (_, e) => {
            if (e.ChangedButton != MouseButton.Right || !IsPetVisual(e.OriginalSource)) return;
            EndPetGesture(cancel: true);
            e.Handled = true;
        };
        PreviewMouseUp += (_, e) => {
            if (e.ChangedButton != MouseButton.Right || !IsPetVisual(e.OriginalSource)) return;
            if (ready && !closing) petMenu.IsOpen = true;
            e.Handled = true;
        };
    }

    internal void Receive(JsonElement command)
    {
        if (closing || command.ValueKind != JsonValueKind.Object || !command.TryGetProperty("type", out var type)) return;
        switch (type.GetString())
        {
            case "usage":
                pendingUsage = command;
                if (ready) UpdateUsage(command);
                break;
            case "visibility":
                bool requestedVisible = command.GetProperty("visible").GetBoolean();
                // 打开用量面板也会发送 visible=true；只有真正切换显隐才重置提醒，避免频繁查看用量让提醒一直延期。
                if (requestedVisible == visible) break;
                visible = requestedVisible;
                if (!visible) EndPetGesture(cancel: true);
                if (!visible && petMenu != null) petMenu.IsOpen = false;
                if (visible) ResumeNotifications();
                else PauseNotifications();
                SyncAutonomy();
                if (visible) Show();
                else { quotaCloud?.Hide(); Hide(); quota?.Hide(); }
                if (pet != null)
                {
                    pet.EventTimer.Stop();
                    if (visible) pet.DisplayToNomal();
                    else
                    {
                        // 完全隐藏后停止动画任务和移动；恢复时由原版状态机重新接续。
                        pet.isPress = false;
                        pet.CleanState();
                        foreach (var animation in graph!.GraphsALL) animation.Stop(true);
                    }
                }
                break;
            case "shutdown": Application.Current.Shutdown(); break;
        }
    }

    private void UpdateUsage(JsonElement usage)
    {
        string Text(string key) => usage.TryGetProperty(key, out var item) && item.ValueKind == JsonValueKind.String
            ? item.GetString() ?? "" : "";
        quota?.SetUsage(Text("provider"), Text("primary"), Text("secondary"), Text("status"));
        bool warning = usage.TryGetProperty("warning", out var value) && value.ValueKind == JsonValueKind.True;
        bool? peak = usage.TryGetProperty("pricing_peak", out var pricing) &&
            pricing.ValueKind is JsonValueKind.True or JsonValueKind.False ? pricing.GetBoolean() : null;
        if (usage.TryGetProperty("theme", out var theme) && theme.ValueKind == JsonValueKind.Object)
            quotaCloud!.SetTheme(theme);
        quotaCloud!.SetUsage(Text("provider"), Text("primary"), Text("secondary"), Text("status"), warning, peak);
        string key = Text("provider") + ":" + Text("status");
        // 仅在警告首次出现时提示，不能每次额度轮询都打断宠物动作。
        if (warning && key != lastWarning) ShowUsageWarning(Text("status"));
        lastWarning = warning ? key : null;
    }

    private bool? DockedEdge => pet?.DisplayType.Type switch {
        GraphType.SideHide_Left_Main or GraphType.SideHide_Left_Rise => true,
        GraphType.SideHide_Right_Main or GraphType.SideHide_Right_Rise => false,
        _ => null
    };

    private void OnPetGraphDisplayed(GraphInfo _)
    {
        // 动画回调可能来自后台线程；排回 UI 后读取最新状态，避免过期回调重新显示已隐藏的云朵。
        if (!Dispatcher.HasShutdownStarted) Dispatcher.BeginInvoke(() => {
            // 挥手等内核互动也会切换动画；保留新动作，同时取消未走完的自主序列。
            if (autonomousSequence != null && pet?.DisplayType != autonomousFrame?.GraphInfo)
                CancelAutonomousSequence(returnToNormal: false);
            // 用户互动可能中断警告的开场动作；不能让等待标记永久阻止后续生活提醒。
            if (warningSpeechPending && pet?.DisplayType.Name != pendingWarningAnimation)
            {
                warningSpeechPending = false;
                ++notificationGeneration;
            }
            UpdateQuotaCloud();
        });
    }

    private void UpdateQuotaCloud()
    {
        if (quotaCloud == null || closing) return;
        if (!ready || !visible || !IsVisible || petDragging || notificationsSuspended)
        {
            quotaCloud.Hide();
            return;
        }
        bool? edge = DockedEdge;
        // 手动显隐只覆盖当前状态；普通动画和刷新不重置，真正进入/离开贴边时才恢复默认。
        bool docked = LogicalDockedEdge.HasValue;
        if (cloudDockedState != docked)
        {
            cloudDockedState = docked;
            cloudManualChoice = null;
        }
        cloudEnabled = cloudManualChoice ?? AutomaticCloudVisible(docked);
        if (!cloudEnabled)
        {
            quotaCloud.Hide();
            return;
        }
        var handle = new WindowInteropHelper(this).Handle;
        if (!GetWindowRect(handle, out var rect)) return;
        var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
        quotaCloud.ShowNextTo(new Rect(rect.Left, rect.Top, rect.Right - rect.Left, rect.Bottom - rect.Top),
            new Rect(work.X, work.Y, work.Width, work.Height), edge, size / 220.0, VisualTreeHelper.GetDpi(this));
    }

    private void Request(string name)
    {
        if (petMenu != null) petMenu.IsOpen = false;
        if (demo || smoke)
        {
            if (name == "quit" || name == "disable_pet") Application.Current.Shutdown();
            else { quota?.Show(); quota?.Activate(); }
            return;
        }
        Program.Send(new { @event = name });
    }

    private void ShowCredits() => MessageBox.Show(this,
        "默认角色与动画：虚拟主播模拟器制作组 / VPet\nhttps://github.com/LorisYounger/VPet\n\n" +
        "当前为非商业集成试用。代码遵循 Apache-2.0；动画另行授权。\n" +
        "商业用途需联系原作者，分发动画须保留授权信息且不得收费分发。\n" +
        "完整授权见程序目录 THIRD_PARTY_NOTICES.md 与 VPet-README.md。",
        "VPet 来源与授权", MessageBoxButton.OK, MessageBoxImage.Information);

    private void UpdateMessageTextSize()
    {
        // 对话框随 500 单位角色画布一起缩放；补偿小桌宠的比例，保证实际提示字号至少为 14。
        if (pet?.MsgBar is MessageBar message)
            message.TText.FontSize = Math.Max(24, 14.0 * 500 / size);
    }

    private void ResizePet(int delta)
    {
        CancelAutonomousSequence();
        FinishNotification(restorePosition: false);
        bool? edge = DockedEdge;
        size = Math.Clamp(size + delta, 160, 320);
        Width = Height = size;
        // 贴边角色缩放后仍需使用对应侧的锚点；普通回正会使角色与云朵脱离屏幕边缘。
        if (edge == null || !TrySnapPetToEdge(edge)) ClampPosition();
        UpdateQuotaCloud();
        SaveState();
    }

    private void LoadState()
    {
        Left = SystemParameters.WorkArea.Right - 460;
        Top = SystemParameters.WorkArea.Bottom - 280;
        try
        {
            string layout = Path.Combine(dataDirectory, "layout.json");
            if (!File.Exists(layout)) return;
            using var data = JsonDocument.Parse(File.ReadAllText(layout));
            LoadNotificationPreferences(data.RootElement);
            Left = data.RootElement.GetProperty("x").GetDouble();
            Top = data.RootElement.GetProperty("y").GetDouble();
            size = Math.Clamp(data.RootElement.GetProperty("size").GetInt32(), 160, 320);
            allowMove = !data.RootElement.TryGetProperty("allowMove", out var autonomous) || autonomous.GetBoolean();
            quotaPinned = data.RootElement.GetProperty("quotaPinned").GetBoolean();
            // 手动显隐是临时覆盖；只恢复展示模式和提醒偏好。
            if (data.RootElement.TryGetProperty("quotaX", out var qx) &&
                data.RootElement.TryGetProperty("quotaY", out var qy) &&
                double.IsFinite(qx.GetDouble()) && double.IsFinite(qy.GetDouble()))
                quotaPosition = new Point(qx.GetDouble(), qy.GetDouble());
        }
        catch (Exception ex) when (ex is IOException or JsonException or InvalidOperationException or KeyNotFoundException or FormatException)
        {
            // 损坏的桌宠布局不能影响主应用，也不清除已有用量数据。
            Console.Error.WriteLine("Pet state could not be restored: " + ex.Message);
        }
    }

    private void SaveState()
    {
        if (!ready) return;
        try
        {
            var position = notificationOrigin?.Position ?? new Point(Left, Top);
            WriteAtomic("layout.json", JsonSerializer.Serialize(new { x = position.X, y = position.Y, size, allowMove, quotaPinned,
                cloudMode, cloudRandomMinutes, drinkReminderEnabled, drinkReminderMinutes, restReminderEnabled, restReminderMinutes,
                quotaX = quota?.Left ?? quotaPosition?.X ?? 0, quotaY = quota?.Top ?? quotaPosition?.Y ?? 0 }));
        }
        catch (IOException ex) { Console.Error.WriteLine("Pet state could not be saved: " + ex.Message); }
    }

    private void WriteAtomic(string name, string text)
    {
        string path = Path.Combine(dataDirectory, name);
        File.WriteAllText(path + ".tmp", text);
        File.Move(path + ".tmp", path, true);
    }

    private Rect WorkArea()
    {
        var screen = System.Windows.Forms.Screen.FromHandle(new WindowInteropHelper(this).Handle);
        var dpi = VisualTreeHelper.GetDpi(this);
        var work = screen.WorkingArea;
        return new Rect(work.X / dpi.DpiScaleX, work.Y / dpi.DpiScaleY,
            work.Width / dpi.DpiScaleX, work.Height / dpi.DpiScaleY);
    }

    private void ClampPosition()
    {
        var work = WorkArea();
        Left = Math.Clamp(double.IsFinite(Left) ? Left : work.Left, work.Left, Math.Max(work.Left, work.Right - Width));
        Top = Math.Clamp(double.IsFinite(Top) ? Top : work.Top, work.Top, Math.Max(work.Top, work.Bottom - Height));
    }

    public double ZoomRatio => Dispatcher.Invoke(() => Width / 500.0);
    public int PressLength => 450;
    // 关闭原版养成计算，同时停止触摸时的体力/心情变化提示；动画与鼠标互动仍由内核处理。
    public bool EnableFunction => false;
    public int InteractionCycle => 40;
    public bool RePositionActive { get; set; } = true;
    public void ShowPanel() => Dispatcher.BeginInvoke(() => Request("open_panel"));
    public void ResetPosition() => Dispatcher.Invoke(ClampPosition);
    public bool CheckPosition() => Dispatcher.Invoke(() => !WorkArea().Contains(new Rect(Left, Top, Width, Height)));
    public double GetWindowsDistanceLeft() => Dispatcher.Invoke(() => Left - WorkArea().Left);
    public double GetWindowsDistanceRight() => Dispatcher.Invoke(() => WorkArea().Right - Left - Width);
    public double GetWindowsDistanceUp() => Dispatcher.Invoke(() => Top - WorkArea().Top);
    public double GetWindowsDistanceDown() => Dispatcher.Invoke(() => WorkArea().Bottom - Top - Height);
    public void MoveWindows(double x, double y) => Dispatcher.Invoke(() => {
        if (closing || !visible || notificationsSuspended || activeNotice != Notice.None || autonomousSequence != null ||
            petPointerDown || petMenu?.IsOpen == true) return;
        Left += x * ZoomRatio;
        Top += y * ZoomRatio;
    });

    private async Task RunVisualCheck()
    {
        string output = captureDirectory ?? Path.Combine(dataDirectory, "smoke");
        Directory.CreateDirectory(output);
        await Task.Delay(1600);
        Capture(this, Path.Combine(output, "pet.png"));
        if (quota?.IsVisible == true) Capture(quota, Path.Combine(output, "quota.png"));
        if (!smoke) return;
        var checks = new Dictionary<string, bool>();
        var handle = new WindowInteropHelper(this).Handle;
        int style = GetWindowLong(handle, -20);
        checks["hostWindowSwitcherPolicy"] = ShowInTaskbar == demo &&
            (demo ? (style & 0x40080) == 0x40000 : (style & 0x40080) == 0x80);
        // 重新显示不能让 WPF 恢复独立应用窗口样式；用真实 HWND 检查，而非只检查托管属性。
        Hide();
        Show();
        await Task.Delay(100);
        checks["hostWindowSwitcherPolicySurvivesReshow"] =
            (GetWindowLong(handle, -20) & 0x40080) == (style & 0x40080);
        checks["noStandaloneQuotaOnStartup"] = quota?.IsVisible != true;
        foreach (var kind in new[] { GraphType.Default, GraphType.Touch_Head, GraphType.Touch_Body })
            checks[kind.ToString()] = graph!.FindName(kind) != null;
        checks["raised"] = graph!.FindName(GraphType.Raised_Static) != null;
        checks["autonomyWithoutGrowth"] = !pet!.EventTimer.Enabled && graph.FindName(GraphType.Sleep) == null &&
            graph.FindName(GraphType.Work) == null && graph.FindName(GraphType.Move) != null;
        checks["noFeedingResources"] = !Directory.Exists(Path.Combine(resources, "food")) &&
            graph.FindGraph("eat", AnimatType.Single, save.Mode) == null;
        checks["noBottomToolbar"] = !pet!.UIGrid.Children.Contains(pet.ToolBar) && pet.DefaultClickAction == null;
        checks["contextMenuActions"] = petMenu!.Items.OfType<MenuItem>().Select(item => item.Header.ToString())
            .SequenceEqual(new[] { "查看用量面板", "显示额度气泡", "额度气泡展示", "额度随机间隔", "喝水提醒", "休息提醒",
                "自主活动", "放大桌宠", "缩小桌宠",
                "TokenMeter 设置", "返回悬浮球", "默认角色来源与授权", "退出 TokenMeter" });
        double strengthBefore = save.Strength, feelingBefore = save.Feeling, expBefore = save.Exp;
        await RunDragChecks(checks);
        pet!.DisplayTouchHead();
        await Task.Delay(450);
        Capture(this, Path.Combine(output, "touch-head.png"));
        var rightDown = new MouseButtonEventArgs(Mouse.PrimaryDevice, Environment.TickCount, MouseButton.Right) {
            RoutedEvent = Mouse.PreviewMouseDownEvent, Source = pet.MainGrid
        };
        pet.MainGrid.RaiseEvent(rightDown);
        var rightUp = new MouseButtonEventArgs(Mouse.PrimaryDevice, Environment.TickCount, MouseButton.Right) {
            RoutedEvent = Mouse.PreviewMouseUpEvent, Source = pet.MainGrid
        };
        pet.MainGrid.RaiseEvent(rightUp);
        await Task.Delay(150);
        checks["rightClickOpensContextMenu"] = rightDown.Handled && rightUp.Handled && petMenu.IsOpen;
        checks["contextMenuPausesAutonomy"] = !ambientTimer.IsEnabled && !pet.MoveTimer.Enabled;
        if (petMenu.ActualWidth > 0 && petMenu.ActualHeight > 0) Capture(petMenu, Path.Combine(output, "context-menu.png"));
        bool oldEnabled = cloudEnabled;
        quotaMenuItem!.IsChecked = !oldEnabled;
        quotaMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
        checks["cloudMenuToggleWorks"] = cloudEnabled == !oldEnabled && quota?.IsVisible != true;
        quotaMenuItem.IsChecked = oldEnabled;
        quotaMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
        petMenu.IsOpen = false;
        // 弹出菜单的 Closed 可能在退场动画后触发，验证恢复状态前等待该生命周期完成。
        var closeDeadline = DateTime.UtcNow.AddSeconds(2);
        while (ambientTimer.IsEnabled != (allowMove && visible) && DateTime.UtcNow < closeDeadline)
            await Task.Delay(50);
        checks["contextMenuRestoresAutonomy"] = ambientTimer.IsEnabled == (allowMove && visible);
        Capture(this, Path.Combine(output, "no-toolbar.png"));
        using (var update = JsonDocument.Parse("{\"type\":\"usage\",\"provider\":\"Codex · 演示数据\",\"primary\":\"剩余 65%\",\"secondary\":\"2 小时后重置\",\"status\":\"\",\"warning\":false}"))
            Receive(update.RootElement.Clone());
        await Task.Delay(150);
        await RunCloudChecks(checks, output);
        await RunNotificationChecks(checks, output);
        await RunAutonomyChecks(checks, output);
        if (quota?.ActualWidth > 0) Capture(quota, Path.Combine(output, "quota.png"));
        SaveState();
        checks["noGrowthChanges"] = !EnableFunction && save.Strength == strengthBefore &&
            save.Feeling == feelingBefore && save.Exp == expBefore;
        File.WriteAllText(Path.Combine(output, "report.json"), JsonSerializer.Serialize(new {
            animations = graph!.GraphsALL.Count, checks, errors = pet.ErrorMessage,
            memoryMiB = Process.GetCurrentProcess().WorkingSet64 / 1048576.0,
            layoutWritten = File.Exists(Path.Combine(dataDirectory, "layout.json"))
        }, new JsonSerializerOptions { WriteIndented = true }));
        Application.Current.Shutdown(checks.Values.All(x => x) ? 0 : 1);
    }

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
    private static extern int GetWindowLong(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongW")]
    private static extern int SetWindowLong(IntPtr window, int index, int value);

    private static void Capture(FrameworkElement window, string path)
    {
        window.UpdateLayout();
        var image = new RenderTargetBitmap((int)window.ActualWidth, (int)window.ActualHeight, 96, 96, PixelFormats.Pbgra32);
        image.Render(window);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(image));
        using var stream = File.Create(path);
        encoder.Save(stream);
    }
}
