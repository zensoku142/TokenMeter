using LinePutScript;
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using VPet_Simulator.Core;
using static VPet_Simulator.Core.GraphInfo;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private readonly DispatcherTimer petPressTimer = new();
    private const double EdgeSnapRange = 12;
    private bool petPointerDown;
    private bool petDragging;
    private Point petPressScreen;
    private Point petPressLocal;
    private Point petWindowScreen;
    private Size petDragThreshold;

    private void AttachPetDragging()
    {
        petPressTimer.Interval = TimeSpan.FromMilliseconds(PressLength);
        petPressTimer.Tick += (_, _) => BeginPetDrag();
        PreviewMouseDown += (_, e) => {
            if (e.ChangedButton != MouseButton.Left || !ready || closing || petPointerDown ||
                !IsPetVisual(e.OriginalSource)) return;
            if (StartPetGesture(PointToScreen(e.GetPosition(this)), e.GetPosition(pet!.MainGrid)))
                e.Handled = true;
        };
        PreviewMouseMove += (_, e) => {
            if (!petPointerDown) return;
            if (e.LeftButton != MouseButtonState.Pressed) EndPetGesture(cancel: true);
            else UpdatePetGesture(PointToScreen(e.GetPosition(this)));
            e.Handled = true;
        };
        PreviewMouseUp += (_, e) => {
            if (e.ChangedButton != MouseButton.Left || !petPointerDown) return;
            UpdatePetGesture(PointToScreen(e.GetPosition(this)));
            EndPetGesture(cancel: false);
            e.Handled = true;
        };
        LostMouseCapture += (_, _) => {
            if (petPointerDown && Mouse.Captured != this) EndPetGesture(cancel: true);
        };
    }

    private bool IsPetVisual(object source)
    {
        if (pet == null || source is not Visual visual) return false;
        // 原版画面命中源可能是 Image、Decorator 或 MainGrid；只检查 Image 的祖先会漏掉真实拖动。
        // 仍排除消息等覆盖层，避免把点击文字/按钮当作拖动角色。
        return (visual == pet || pet.IsAncestorOf(visual)) && visual != pet.UIGrid &&
            !pet.UIGrid.IsAncestorOf(visual) && visual != pet.LabelDisplay && !pet.LabelDisplay.IsAncestorOf(visual);
    }

    private bool StartPetGesture(Point screenPoint, Point localPoint)
    {
        if (!GetWindowRect(new WindowInteropHelper(this).Handle, out var window)) return false;
        quotaCloud?.NotifyActivity();
        // 捕获稳定的窗口而非动画 Image；图片切帧、鼠标移出角色轮廓都不能中断拖动。
        if (!Mouse.Capture(this, CaptureMode.SubTree)) return false;
        petPointerDown = true;
        petDragging = false;
        CancelAutonomousSequence();
        FinishNotification(restorePosition: false);
        ResetCloudHover();
        petPressScreen = screenPoint;
        petPressLocal = localPoint;
        petWindowScreen = new Point(window.Left, window.Top);
        var dpi = VisualTreeHelper.GetDpi(this);
        petDragThreshold = new Size(SystemParameters.MinimumHorizontalDragDistance * dpi.DpiScaleX,
            SystemParameters.MinimumVerticalDragDistance * dpi.DpiScaleY);
        ambientTimer.Stop();
        pet!.SetMoveMode(false, false, 1200000);
        pet.isPress = true;
        pet.CountNomal = 0;
        pet.LastInteractionTime = DateTime.Now;
        petPressTimer.Start();
        return true;
    }

    private void BeginPetDrag()
    {
        petPressTimer.Stop();
        if (!petPointerDown || petDragging || closing) return;
        petDragging = true;
        quotaCloud?.Hide();
        // 从贴边开始拖动即结束当前手动选择；原地放下也算重新贴边，不能漏掉没有位置变化的情况。
        if (LogicalDockedEdge.HasValue)
        {
            // 只有用户实际开始拖动才解除贴边锁；点击、提醒和自主动画都不能代替该操作。
            manualDockedEdge = null;
            cloudDockedState = false;
            cloudManualChoice = null;
            cloudEnabled = false;
        }
        pet!.ToolBar.Visibility = Visibility.Collapsed;
        pet.CleanState();
        // 原版 DisplayRaised 会强制把鼠标对齐头部锚点并安装图片级拖动事件。
        // 宿主只复用提起动画，位置始终相对按下点计算，避免起拖跳动和两套事件争抢。
        pet.Display(GraphType.Raised_Static, AnimatType.A_Start, (string name) => LoopRaised(name));
    }

    private void LoopRaised(string name)
    {
        if (petDragging && !closing)
            pet!.Display(name, AnimatType.B_Loop, () => LoopRaised(name));
    }

    private void UpdatePetGesture(Point screenPoint)
    {
        if (!petPointerDown) return;
        var delta = screenPoint - petPressScreen;
        // 普通按住移动超过系统阈值即可拖动，不必等450ms后仍恰好停在头部热区。
        if (!petDragging && (Math.Abs(delta.X) >= petDragThreshold.Width ||
                            Math.Abs(delta.Y) >= petDragThreshold.Height)) BeginPetDrag();
        if (petDragging)
            SetWindowPos(new WindowInteropHelper(this).Handle, IntPtr.Zero,
                (int)Math.Round(petWindowScreen.X + delta.X), (int)Math.Round(petWindowScreen.Y + delta.Y),
                0, 0, DragPositionFlags);
    }

    private void EndPetGesture(bool cancel)
    {
        petPressTimer.Stop();
        if (!petPointerDown) return;
        bool dragged = petDragging;
        petPointerDown = petDragging = false;
        // 先清状态再释放捕获，避免 LostMouseCapture 重入触发第二次点击或结束动画。
        if (Mouse.Captured == this) Mouse.Capture(null);
        pet!.isPress = false;
        SyncAutonomy();
        UpdateCloudPointer();
        if (closing || !visible) return;
        if (dragged)
        {
            // 宿主接管拖拽后不会再进入原版的松手检查；必须先贴边，再对普通落点回正。
            if (cancel || !TrySnapPetToEdge())
            {
                ClampDraggedWindow();
                pet.Display(GraphType.Raised_Static, AnimatType.C_End, pet.DisplayToNomal);
            }
            SaveState();
        }
        else if (!cancel)
        {
            // 轻点仍交给原版触摸区域，保留摸头、摸身体；操作菜单仅由右键打开。
            foreach (var area in pet.Core.TouchEvent)
                if (!area.IsPress && area.Touch(petPressLocal) && area.DoAction()) return;
            if (pet.DisplayType.Type != GraphType.Default)
            {
                pet.CleanState();
                if (!pet.IsIdel && pet.State != VPet_Simulator.Core.Main.WorkingState.Sleep &&
                    pet.DisplayStop(pet.DisplayToNomal)) return;
            }
            pet.DefaultClickAction?.Invoke();
        }
    }

    private bool TrySnapPetToEdge(bool? leftEdge = null)
    {
        var handle = new WindowInteropHelper(this).Handle;
        if (!GetWindowRect(handle, out var rect)) return false;
        var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
        // 原版贴边锚点基于 500px 画布；按实际窗口像素同步缩放热区与锚点，避免混用 WPF 单位和屏幕像素。
        double zoom = (rect.Right - rect.Left) / 500.0;
        // 吸附只占很窄的边缘，给原版 100 单位的爬墙触发区留出可停放空间。
        double threshold = EdgeSnapRange * zoom;
        bool left = leftEdge ?? rect.Left - work.Left <= threshold;
        if (leftEdge == null && !left && work.Right - rect.Right > threshold) return false;
        var animation = left ? GraphType.SideHide_Left_Main : GraphType.SideHide_Right_Main;
        if (graph!.FindName(animation) == null) return false;
        var side = graph.GraphConfig.Data["side"];
        int x = (int)Math.Round(left ? work.Left - side[(gdbe)"left"] * zoom
            : work.Right - side[(gdbe)"right"] * zoom);
        int y = Math.Clamp(rect.Top, work.Top, Math.Max(work.Top, work.Bottom - (rect.Bottom - rect.Top)));
        if (!SetWindowPos(handle, IntPtr.Zero, x, y, 0, 0, DragPositionFlags)) return false;
        manualDockedEdge = left;
        pet!.Display(animation, AnimatType.A_Start, pet.DisplayBLoopingForce);
        // 贴边动画建立后立即停掉内核移动计时器，避免下一次 Tick 把角色重新带回屏幕。
        SyncAutonomy();
        return true;
    }

    private void ClampDraggedWindow()
    {
        var handle = new WindowInteropHelper(this).Handle;
        if (!GetWindowRect(handle, out var rect)) return;
        var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
        int x = Math.Clamp(rect.Left, work.Left, Math.Max(work.Left, work.Right - (rect.Right - rect.Left)));
        int y = Math.Clamp(rect.Top, work.Top, Math.Max(work.Top, work.Bottom - (rect.Bottom - rect.Top)));
        SetWindowPos(handle, IntPtr.Zero, x, y, 0, 0, DragPositionFlags);
    }

    private async Task RunDragChecks(Dictionary<string, bool> checks)
    {
        int originalSize = size;
        bool? originalManualDock = manualDockedEdge;
        double originalLeft = Left, originalTop = Top;
        var handle = new WindowInteropHelper(this).Handle;
        bool positionsMatch = true, releaseClean = true, autonomousBlocked = true;
        try
        {
            var down = new MouseButtonEventArgs(Mouse.PrimaryDevice, Environment.TickCount, MouseButton.Left) {
                RoutedEvent = Mouse.PreviewMouseDownEvent, Source = pet!.MainGrid
            };
            pet.MainGrid.RaiseEvent(down);
            checks["mainGridPressUsesHostCapture"] = down.Handled && petPointerDown && Mouse.Captured == this;
            EndPetGesture(cancel: true);
            // 覆盖原生窗口、三种桌宠缩放，以及非累计的屏幕坐标；不向其它应用注入鼠标事件。
            foreach (int testSize in new[] { 160, 220, 320 })
            {
                size = testSize;
                Width = Height = testSize;
                var work = WorkArea();
                Left = work.Left + (work.Width - Width) / 2;
                Top = work.Top + (work.Height - Height) / 2;
                UpdateLayout();
                GetWindowRect(handle, out var before);
                var start = new Point(before.Left + 60, before.Top + 60);
                if (!StartPetGesture(start, new Point(250, 100))) { positionsMatch = false; continue; }
                MoveWindows(50, 50);
                GetWindowRect(handle, out var pressed);
                autonomousBlocked &= pressed.Left == before.Left && pressed.Top == before.Top;
                UpdatePetGesture(start + new Vector(80, -30));
                GetWindowRect(handle, out var first);
                UpdatePetGesture(start + new Vector(-25, 45));
                GetWindowRect(handle, out var second);
                positionsMatch &= first.Left == before.Left + 80 && first.Top == before.Top - 30 &&
                    second.Left == before.Left - 25 && second.Top == before.Top + 45;
                EndPetGesture(cancel: false);
                releaseClean &= !petPointerDown && !petDragging && Mouse.Captured != this && !pet!.isPress;
            }
            // 使用真实缩放入口和原生窗口坐标覆盖靠近边缘、拖出边缘及拖回屏幕，避免只验证未缩放的坐标公式。
            foreach (int testSize in new[] { 160, 220, 320 })
            {
                ResizePet(testSize - size);
                foreach (bool left in new[] { true, false })
                {
                    foreach (int inset in new[] { 10, -80, 30, 60 })
                    {
                        ClampPosition();
                        UpdateLayout();
                        var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
                        GetWindowRect(handle, out var before);
                        double zoom = (before.Right - before.Left) / 500.0;
                        var start = new Point(before.Left + 60, before.Top + 60);
                        int targetX = left ? work.Left + (int)Math.Round(inset * zoom)
                            : work.Right - (before.Right - before.Left) - (int)Math.Round(inset * zoom);
                        int targetY = work.Top + (work.Height - (before.Bottom - before.Top)) / 2;
                        if (inset < 0)
                            targetY = left ? work.Top - 40 : work.Bottom - (before.Bottom - before.Top) + 40;
                        bool started = StartPetGesture(start, new Point(250, 100));
                        UpdatePetGesture(start + new Vector(targetX - before.Left, targetY - before.Top));
                        EndPetGesture(cancel: false);
                        // 等动画回调运行后再核对，防止贴边被残留的提起/落地动画覆盖。
                        await Task.Delay(inset == 10 ? 1200 : 200);
                        GetWindowRect(handle, out var snapped);
                        var side = graph!.GraphConfig.Data["side"];
                        double expectedX = left ? work.Left - side[(gdbe)"left"] * zoom
                            : work.Right - side[(gdbe)"right"] * zoom;
                        bool shouldSnap = inset <= EdgeSnapRange;
                        if (!shouldSnap) expectedX = targetX;
                        int expectedY = Math.Clamp(targetY, work.Top, work.Bottom - (before.Bottom - before.Top));
                        string direction = left ? "Left" : "Right";
                        checks[$"edgeSnap{direction}{testSize}Inset{inset}"] = started &&
                            (shouldSnap ? pet!.DisplayType.Type == (left ? GraphType.SideHide_Left_Main : GraphType.SideHide_Right_Main)
                                : pet!.DisplayType.Type is GraphType.Raised_Static or GraphType.Default) &&
                            Math.Abs(snapped.Left - expectedX) <= 1 && snapped.Top == expectedY &&
                            !petPointerDown && !petDragging && Mouse.Captured != this;
                        if (!checks[$"edgeSnap{direction}{testSize}Inset{inset}"])
                            Console.Error.WriteLine($"Edge check {direction}/{testSize}/{inset}: animation={pet!.DisplayType.Type}, " +
                                $"position={snapped.Left},{snapped.Top}, expected={expectedX},{expectedY}, " +
                                $"started={started}, pointer={petPointerDown}, dragging={petDragging}, capture={Mouse.Captured == this}");
                        if (inset == 10 && captureDirectory != null)
                            Capture(this, System.IO.Path.Combine(captureDirectory, $"edge-{direction}-{testSize}.png"));
                        start = new Point(snapped.Left + 60, snapped.Top + 60);
                        targetX = work.Left + (work.Width - (snapped.Right - snapped.Left)) / 2;
                        started = StartPetGesture(start, new Point(250, 100));
                        UpdatePetGesture(start + new Vector(targetX - snapped.Left, 0));
                        EndPetGesture(cancel: false);
                        GetWindowRect(handle, out var restored);
                        checks[$"edgeDragBack{direction}{testSize}Inset{inset}"] = started && restored.Left == targetX &&
                            pet!.DisplayType.Type == GraphType.Raised_Static;
                    }
                }
            }
            int clicks = 0;
            Action countClick = () => clicks++;
            pet!.Event_TouchHead += countClick;
            try
            {
                GetWindowRect(handle, out var rect);
                var start = new Point(rect.Left + 60, rect.Top + 60);
                var config = graph!.GraphConfig;
                var head = new Point(config.TouchHeadLocate.X + config.TouchHeadSize.Width / 2,
                    config.TouchHeadLocate.Y + config.TouchHeadSize.Height / 2);
                StartPetGesture(start, head);
                UpdatePetGesture(start + new Vector(1, 1));
                EndPetGesture(cancel: false);
                checks["clickStillTouchesHead"] = clicks == 1;
                StartPetGesture(start, head);
                BeginPetDrag();
                Mouse.Capture(null);
                checks["lostCaptureCancels"] = !petPointerDown && !petDragging && clicks == 1;
                EndPetGesture(cancel: false);
                checks["noClickAfterDrag"] = clicks == 1 && pet.DefaultClickAction == null;
                StartPetGesture(start, head);
                using var hidden = System.Text.Json.JsonDocument.Parse("{\"type\":\"visibility\",\"visible\":false}");
                Receive(hidden.RootElement);
                checks["hideReleasesCapture"] = !petPointerDown && Mouse.Captured != this;
                using var shown = System.Text.Json.JsonDocument.Parse("{\"type\":\"visibility\",\"visible\":true}");
                Receive(shown.RootElement);
            }
            finally { pet.Event_TouchHead -= countClick; }
        }
        finally
        {
            EndPetGesture(cancel: true);
            size = originalSize;
            manualDockedEdge = originalManualDock;
            Width = Height = size;
            Left = originalLeft;
            Top = originalTop;
            ClampPosition();
        }
        checks["dragScreenCoordinatesAtAllSizes"] = positionsMatch;
        checks["dragReleaseClean"] = releaseClean;
        checks["noAutonomousMovementDuringDrag"] = autonomousBlocked;
    }

    // 指针位置和窗口矩形统一使用屏幕物理像素，避免缩放比例被重复乘除；只移动不改变焦点或大小。
    private const uint DragPositionFlags = 0x0001 | 0x0004 | 0x0010; // NOSIZE | NOZORDER | NOACTIVATE
    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetWindowRect(IntPtr window, out NativeRect rect);
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
}
