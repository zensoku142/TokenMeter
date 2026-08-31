using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using static VPet_Simulator.Core.GraphInfo;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private async Task RunCloudChecks(Dictionary<string, bool> checks, string output)
    {
        int originalSize = size;
        bool originalCloudEnabled = cloudEnabled;
        bool? originalCloudDockedState = cloudDockedState;
        bool? originalManualChoice = cloudManualChoice;
        double originalLeft = Left, originalTop = Top;
        var handle = new WindowInteropHelper(this).Handle;
        void Usage(string provider, string primary, string status = "", bool warning = false, bool? pricingPeak = null, object? theme = null)
        {
            using var message = JsonDocument.Parse(JsonSerializer.Serialize(new {
                type = "usage", provider, primary, secondary = "演示数据", status, warning, pricing_peak = pricingPeak, theme
            }));
            Receive(message.RootElement.Clone());
        }
        try
        {
            cloudEnabled = false;
            cloudDockedState = null;
            pet!.CleanState();
            pet.DisplayToNomal();
            await Task.Delay(100);
            checks["cloudHiddenAwayFromEdge"] = !quotaCloud!.IsVisible;
            var bubble = quotaCloud.BubbleGeometry;
            checks["cloudHasTwoSeparateThoughtBubbles"] = bubble.GetFlattenedPathGeometry().Figures.Count == 3 &&
                bubble.FillContains(new Point(124, 117)) && bubble.FillContains(new Point(150, 134)) &&
                !bubble.FillContains(new Point(133, 126));
            quotaCloud.NotifyActivity();
            checks["cloudHiddenStopsIdleTimer"] = !quotaCloud.IsIdleTimerRunning;
            Usage("Codex · 演示数据", "剩余 65%");
            checks["cloudQuotaIsPercentageOnly"] = quotaCloud.PrimaryText == "65%" && quotaCloud.RemainingPercent == 65;
            checks["cloudWaveDoesNotRunWhileHidden"] = !quotaCloud.IsWaveRunning;
            bool noFocusChange = true;
            foreach (int testSize in new[] { 160, 220, 320 })
            {
                ResizePet(testSize - size);
                foreach (bool left in new[] { true, false })
                {
                    var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
                    GetWindowRect(handle, out var rect);
                    SetWindowPos(handle, IntPtr.Zero, left ? work.Left : work.Right - (rect.Right - rect.Left),
                        work.Top + (work.Height - (rect.Bottom - rect.Top)) / 2, 0, 0, DragPositionFlags);
                    IntPtr focused = GetForegroundWindow();
                    TrySnapPetToEdge(left);
                    await Task.Delay(1100);
                    noFocusChange &= GetForegroundWindow() == focused;
                    var cloudHandle = new WindowInteropHelper(quotaCloud).Handle;
                    GetWindowRect(cloudHandle, out var cloudRect);
                    string side = left ? "Left" : "Right";
                    checks[$"cloud{side}{testSize}"] = quotaCloud.IsVisible && DockedEdge == left &&
                        cloudRect.Left >= work.Left && cloudRect.Right <= work.Right &&
                        cloudRect.Top >= work.Top && cloudRect.Bottom <= work.Bottom;
                    checks[$"cloudInitiallyOpaque{side}{testSize}"] = Math.Abs(quotaCloud.DisplayOpacity - 1) < 0.01;
                    checks[$"cloudCloseTo{side}Edge{testSize}"] = Math.Abs(
                        left ? cloudRect.Left - work.Left : work.Right - cloudRect.Right) <= 1;
                    var dpi = VisualTreeHelper.GetDpi(this);
                    checks[$"cloudCompact{side}{testSize}"] =
                        cloudRect.Right - cloudRect.Left <= QuotaCloudWindow.BaseWidth * dpi.DpiScaleX + 1;
                    checks[$"cloudTallerHeight{side}{testSize}"] = Math.Abs(cloudRect.Bottom - cloudRect.Top -
                        QuotaCloudWindow.BaseHeight * Math.Clamp(testSize / 220.0, 0.95, 1.0) * dpi.DpiScaleY) <= 1;
                    CaptureCloudPreview(Path.Combine(output, $"cloud-{side}-{testSize}.png"));
                    pet.Display(left ? GraphType.SideHide_Left_Rise : GraphType.SideHide_Right_Rise,
                        AnimatType.A_Start, pet.DisplayBLoopingForce);
                    await Task.Delay(150);
                    checks[$"cloudRemainsOn{side}Rise{testSize}"] = quotaCloud.IsVisible;
                    ResizePet(testSize == 320 ? -160 : 20);
                    await Task.Delay(150);
                    checks[$"cloudResizeKeeps{side}Dock{testSize}"] = quotaCloud.IsVisible && DockedEdge == left;
                    ResizePet(testSize - size);
                }
            }
            checks["cloudDoesNotActivate"] = noFocusChange;
            int style = CloudWindowStyle(new WindowInteropHelper(quotaCloud).Handle, -20);
            checks["cloudAcceptsMouseWithoutActivation"] = (style & 0x08000020) == 0x08000000 && quotaCloud.IsHitTestVisible;
            bool quotaWasVisible = quota!.IsVisible;
            quota.Hide();
            quotaCloud.RaiseEvent(new MouseButtonEventArgs(Mouse.PrimaryDevice, Environment.TickCount, MouseButton.Left) {
                RoutedEvent = Mouse.MouseDownEvent
            });
            checks["cloudSingleClickDoesNotOpenPanel"] = !quota.IsVisible;
            quotaCloud.RaiseEvent(new MouseButtonEventArgs(Mouse.PrimaryDevice, Environment.TickCount, MouseButton.Right) {
                RoutedEvent = Control.MouseDoubleClickEvent
            });
            checks["cloudRightDoubleClickDoesNotOpenPanel"] = !quota.IsVisible;
            var doubleClick = new MouseButtonEventArgs(Mouse.PrimaryDevice, Environment.TickCount, MouseButton.Left) {
                RoutedEvent = Control.MouseDoubleClickEvent
            };
            quotaCloud.RaiseEvent(doubleClick);
            // 演示模式的 open_panel 显示原额度窗口；沿用生产回调，不能绕过宿主另开一套面板。
            checks["cloudDoubleClickUsesOpenPanelAction"] = doubleClick.Handled && quota.IsVisible && !petPointerDown;
            if (!quotaWasVisible) quota.Hide();
            ResizePet(220 - size);
            foreach (bool left in new[] { true, false })
            {
                var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
                GetWindowRect(handle, out var rect);
                SetWindowPos(handle, IntPtr.Zero, rect.Left, work.Top, 0, 0, DragPositionFlags);
                TrySnapPetToEdge(left);
                await Task.Delay(1100);
                GetWindowRect(new WindowInteropHelper(quotaCloud).Handle, out var cloudRect);
                checks[$"cloudTopAvoidance{left}"] = quotaCloud.IsVisible && cloudRect.Top >= work.Top &&
                    (left ? cloudRect.Left >= work.Left + (rect.Right - rect.Left) * 0.4 - 1
                        : cloudRect.Right <= work.Right - (rect.Right - rect.Left) * 0.4 + 1);
                CaptureCloudPreview(Path.Combine(output, left ? "cloud-top-Left.png" : "cloud-top-Right.png"));
            }
            var centerWork = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
            GetWindowRect(handle, out var centered);
            SetWindowPos(handle, IntPtr.Zero, centered.Left, centerWork.Top + centerWork.Height / 2,
                0, 0, DragPositionFlags);
            var stillPointer = PointToScreen(Mouse.GetPosition(this));
            quotaCloud.NotifyPointerMovement(stillPointer);
            quotaCloud.NotifyActivity();
            CaptureCloudPreview(Path.Combine(output, "cloud-active.png"));
            await Task.Delay(1800);
            checks["cloudDoesNotFadeImmediately"] = Math.Abs(quotaCloud.DisplayOpacity - 1) < 0.01;
            Usage("Codex · 演示数据", "剩余 65%");
            UpdateQuotaCloud();
            quotaCloud.NotifyPointerMovement(stillPointer);
            await Task.Delay(2000);
            checks["cloudFadesAfterIdleDespiteUsageAndAnimation"] = Math.Abs(quotaCloud.DisplayOpacity - 0.65) < 0.02 &&
                !quotaCloud.IsIdleTimerRunning;
            CaptureCloudPreview(Path.Combine(output, "cloud-idle.png"));
            quotaCloud.NotifyPointerMovement(stillPointer + new Vector(1, 0));
            checks["cloudMouseMovementRestoresOpacity"] = Math.Abs(quotaCloud.DisplayOpacity - 1) < 0.01 && quotaCloud.IsIdleTimerRunning;
            pet!.RaiseEvent(new MouseEventArgs(Mouse.PrimaryDevice, Environment.TickCount) {
                RoutedEvent = Mouse.MouseMoveEvent
            });
            checks["petMouseMovementRefreshesCloudIdle"] = Math.Abs(quotaCloud.DisplayOpacity - 1) < 0.01 && quotaCloud.IsIdleTimerRunning;

            quotaMenuItem!.IsChecked = false;
            quotaMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            Usage("Codex · 演示数据", "剩余 65%");
            UpdateQuotaCloud();
            await Task.Delay(150);
            checks["cloudMenuHidesWithoutOpeningWindow"] = !cloudEnabled && !quotaCloud.IsVisible && quota?.IsVisible != true &&
                !quotaCloud.IsIdleTimerRunning && !quotaCloud.IsWaveRunning;
            using (var layout = JsonDocument.Parse(File.ReadAllText(Path.Combine(dataDirectory, "layout.json"))))
                checks["cloudManualChoiceIsNotGlobalPreference"] = !layout.RootElement.TryGetProperty("cloudEnabled", out _);
            pet.Display(GraphType.SideHide_Right_Rise, AnimatType.A_Start, pet.DisplayBLoopingForce);
            await Task.Delay(150);
            checks["dockedManualCloseSurvivesRise"] = !cloudEnabled && !quotaCloud.IsVisible;
            quotaMenuItem.IsChecked = true;
            quotaMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            checks["cloudMenuShowsWithoutOpeningWindow"] = cloudEnabled && quotaCloud.IsVisible && quota?.IsVisible != true &&
                Math.Abs(quotaCloud.DisplayOpacity - 1) < 0.01 && quotaCloud.IsIdleTimerRunning;

            pet.CleanState();
            pet.DisplayToNomal();
            GetWindowRect(handle, out var floatingRect);
            SetWindowPos(handle, IntPtr.Zero, centerWork.Left + (centerWork.Width - (floatingRect.Right - floatingRect.Left)) / 2,
                centerWork.Top + centerWork.Height / 2, 0, 0, DragPositionFlags);
            UpdateQuotaCloud();
            await Task.Delay(150);
            checks["undockingDefaultsCloudHidden"] = DockedEdge == null && !cloudEnabled && !quotaCloud.IsVisible;
            quotaMenuItem.IsChecked = true;
            quotaMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            GetWindowRect(handle, out floatingRect);
            GetWindowRect(new WindowInteropHelper(quotaCloud).Handle, out var floatingCloud);
            checks["floatingCloudCanBeOpenedAtHead"] = cloudEnabled && quotaCloud.IsVisible && DockedEdge == null &&
                Math.Abs((floatingCloud.Left + floatingCloud.Right) / 2.0 - (floatingRect.Left + floatingRect.Right) / 2.0) <= 1 &&
                floatingCloud.Top < floatingRect.Top;
            CaptureCloudPreview(Path.Combine(output, "cloud-floating.png"));
            pet.Display(GraphType.Touch_Body, AnimatType.A_Start, pet.DisplayToNomal);
            Usage("Codex · 演示数据", "剩余 65%");
            await Task.Delay(150);
            checks["floatingManualShowSurvivesAnimationAndUsage"] = cloudEnabled && quotaCloud.IsVisible;
            SetWindowPos(handle, IntPtr.Zero, floatingRect.Left + 40, floatingRect.Top + 20, 0, 0, DragPositionFlags);
            GetWindowRect(new WindowInteropHelper(quotaCloud).Handle, out var movedCloud);
            checks["floatingCloudFollowsPet"] = movedCloud.Left == floatingCloud.Left + 40 && movedCloud.Top == floatingCloud.Top + 20;
            quotaMenuItem.IsChecked = false;
            quotaMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            UpdateQuotaCloud();
            checks["floatingCloudCanBeClosed"] = !cloudEnabled && !quotaCloud.IsVisible;
            TrySnapPetToEdge(true);
            await Task.Delay(150);
            checks["dockingAutomaticallyShowsCloud"] = DockedEdge == true && cloudEnabled && quotaCloud.IsVisible;
            quotaMenuItem.IsChecked = false;
            quotaMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            GetWindowRect(handle, out var redockRect);
            bool redragged = StartPetGesture(new Point(redockRect.Left + 60, redockRect.Top + 60), new Point(250, 100));
            BeginPetDrag();
            EndPetGesture(cancel: false);
            await Task.Delay(150);
            checks["redockingAtSamePositionResetsManualClose"] = redragged && cloudEnabled && quotaCloud.IsVisible;
            TrySnapPetToEdge(false);
            await Task.Delay(150);
            var firstWave = quotaCloud.LiquidGeometry;
            await Task.Delay(120);
            checks["cloudWaveAnimatesWhenVisible"] = quotaCloud.IsWaveRunning && !ReferenceEquals(firstWave, quotaCloud.LiquidGeometry);
            foreach (int percent in new[] { 0, 25, 65, 100 })
            {
                Usage("Codex · 演示数据", $"剩余 {percent}%");
                checks[$"cloudLiquid{percent}"] = quotaCloud.PrimaryText == $"{percent}%" &&
                    quotaCloud.RemainingPercent == percent && quotaCloud.IsWaveRunning == (percent > 0 && percent < 100) &&
                    quotaCloud.LiquidGeometry.IsEmpty() == (percent == 0);
                CaptureCloudPreview(Path.Combine(output, $"cloud-liquid-{percent}.png"));
            }
            checks["cloudWaterExcludesThoughtBubbles"] = !quotaCloud.LiquidGeometry.FillContains(new Point(124, 117)) &&
                !quotaCloud.LiquidGeometry.FillContains(new Point(150, 134));
            Usage("Codex · 演示数据", "65%");
            checks["cloudAcceptsPlainPercentage"] = quotaCloud.PrimaryText == "65%" && quotaCloud.RemainingPercent == 65;
            Usage("Codex · 演示数据", "剩余 NaN%");
            checks["cloudRejectsNonFiniteQuota"] = quotaCloud.PrimaryText == "--" && quotaCloud.RemainingPercent == null &&
                !quotaCloud.IsWaveRunning && quotaCloud.LiquidGeometry.IsEmpty();
            Usage("DeepSeek · 演示数据", "余额 ¥12.80");
            checks["cloudReceivesBalance"] = quotaCloud.PrimaryText == "余额 ¥12.80" && quotaCloud.RemainingPercent == null &&
                !quotaCloud.IsWaveRunning && quotaCloud.LiquidGeometry.IsEmpty();
            CaptureCloudPreview(Path.Combine(output, "cloud-balance.png"));
            foreach (bool peak in new[] { false, true })
            {
                Usage("DeepSeek · 演示数据", "余额 ¥12.80", pricingPeak: peak);
                checks[$"cloudDeepSeekOutline{peak}"] = quotaCloud.OutlineColor == (peak
                    ? Color.FromRgb(255, 176, 0) : Color.FromRgb(47, 114, 232)) && !quotaCloud.IsWaveRunning;
                CaptureCloudPreview(Path.Combine(output, peak ? "cloud-peak.png" : "cloud-offpeak.png"));
            }
            Usage("MiMo · 演示数据", "余额 ¥12.80", pricingPeak: true);
            checks["cloudPricingDoesNotLeakToOtherProviders"] = quotaCloud.OutlineColor == Color.FromRgb(216, 222, 229);
            Usage("DeepSeek · 演示数据", "余额 ¥12.80");
            checks["cloudPricingDisabledRestoresOutline"] = quotaCloud.OutlineColor == Color.FromRgb(216, 222, 229);
            quotaCloud.NotifyActivity();
            CaptureCloudPreview(Path.Combine(output, "cloud-hover.png"));
            Usage("Codex · 演示数据", "剩余 5%", "剩余额度不足", true);
            checks["cloudReceivesLowQuota"] = quotaCloud.PrimaryText == "5%" && quotaCloud.RemainingPercent == 5;
            CaptureCloudPreview(Path.Combine(output, "cloud-low-quota.png"));
            Usage("Codex · 演示数据", "--", "用量暂不可用", true);
            checks["cloudReceivesUnavailable"] = quotaCloud.PrimaryText == "--" && quotaCloud.RemainingPercent == null &&
                !quotaCloud.IsWaveRunning && quotaCloud.LiquidGeometry.IsEmpty();
            CaptureCloudPreview(Path.Combine(output, "cloud-unavailable.png"));
            Usage("DeepSeek · 演示数据", "余额 ¥123,456.78");
            CaptureCloudPreview(Path.Combine(output, "cloud-long-balance.png"));
            Usage("Codex · 演示数据", "剩余 65%");
            checks["cloudQuotaRestoresNeutralOutline"] = quotaCloud.OutlineColor == Color.FromRgb(216, 222, 229);
            var customTheme = new { accent = "#8A4FFF", accent_hover = "#9D6BFF", water_top = "#BDA1FF",
                water_deep = "#6439B9", water_back = "#9D6BFF", peak = "#F2AB3B", on_accent = "#FFFFFF" };
            Usage("Codex · 演示数据", "剩余 65%", theme: customTheme);
            checks["cloudWaterUsesThemeAccent"] = quotaCloud.LiquidColor == Color.FromRgb(138, 79, 255) && quotaCloud.PrimaryText == "65%";
            CaptureCloudPreview(Path.Combine(output, "cloud-theme-quota.png"));
            Usage("DeepSeek · 演示数据", "余额 ¥12.80", pricingPeak: false);
            checks["cloudOffpeakUsesThemeAccent"] = quotaCloud.OutlineColor == Color.FromRgb(138, 79, 255);
            CaptureCloudPreview(Path.Combine(output, "cloud-theme-offpeak.png"));
            Usage("DeepSeek · 演示数据", "余额 ¥12.80", pricingPeak: true);
            checks["cloudPeakUsesThemeWarning"] = quotaCloud.OutlineColor == Color.FromRgb(242, 171, 59);
            CaptureCloudPreview(Path.Combine(output, "cloud-theme-peak.png"));
            Usage("DeepSeek · 演示数据", "余额 ¥12.80", pricingPeak: true, theme: new { accent = "invalid", peak = "#GGGGGG" });
            checks["cloudInvalidThemeKeepsPreviousColors"] = quotaCloud.LiquidColor == Color.FromRgb(138, 79, 255) &&
                quotaCloud.OutlineColor == Color.FromRgb(242, 171, 59);
            Usage("Codex · 演示数据", "剩余 65%");
            GetWindowRect(handle, out var beforeDrag);
            StartPetGesture(new Point(beforeDrag.Left + 60, beforeDrag.Top + 60), new Point(250, 100));
            BeginPetDrag();
            checks["cloudHidesImmediatelyOnDrag"] = !quotaCloud.IsVisible;
            checks["cloudWavePausesOnDrag"] = !quotaCloud.IsWaveRunning;
            checks["cloudIdlePausesOnDrag"] = !quotaCloud.IsIdleTimerRunning;
            EndPetGesture(cancel: true);
            TrySnapPetToEdge(false);
            await Task.Delay(150);
            using (var hidden = JsonDocument.Parse("{\"type\":\"visibility\",\"visible\":false}"))
                Receive(hidden.RootElement);
            await Task.Delay(100);
            checks["cloudStaysHiddenAfterQueuedAnimation"] = !quotaCloud.IsVisible;
            checks["cloudWavePausesWithHost"] = !quotaCloud.IsWaveRunning;
            checks["cloudIdlePausesWithHost"] = !quotaCloud.IsIdleTimerRunning;
            using (var shown = JsonDocument.Parse("{\"type\":\"visibility\",\"visible\":true}"))
                Receive(shown.RootElement);
            await Task.Delay(100);
            checks["cloudHiddenAfterNormalRestore"] = !quotaCloud.IsVisible;

            // 无需实际改变系统显示设置，也要覆盖负坐标副屏、顶部避让和多种 DPI 的位置计算。
            bool boundsValid = true;
            foreach (var work in new[] { new Rect(0, 0, 1920, 1040), new Rect(-2560, -240, 2560, 1400) })
            foreach (double dpi in new[] { 1.0, 1.25, 1.5, 2.0 })
            foreach (bool left in new[] { true, false })
            foreach (bool top in new[] { true, false })
            {
                double length = 220 * dpi;
                var petBounds = new Rect(left ? work.Left - length * 0.438 : work.Right - length * 0.562,
                    top ? work.Top : work.Bottom - length, length, length);
                var bounds = QuotaCloudWindow.BoundsFor(petBounds, work,
                    new Size(QuotaCloudWindow.BaseWidth * dpi, QuotaCloudWindow.BaseHeight * dpi), left);
                boundsValid &= work.Contains(bounds) && (!top || (left ? bounds.Left >= work.Left + length * 0.4
                    : bounds.Right <= work.Right - length * 0.4 + 0.01));
            }
            checks["cloudBoundsAcrossDpiAndNegativeMonitors"] = boundsValid;
            var freeWork = new Rect(-1920, 0, 1920, 1080);
            var freePet = new Rect(-1100, 400, 220, 220);
            var freeBounds = QuotaCloudWindow.BoundsFor(freePet, freeWork,
                new Size(QuotaCloudWindow.BaseWidth, QuotaCloudWindow.BaseHeight), null);
            checks["floatingCloudBoundsAreCentered"] = freeWork.Contains(freeBounds) &&
                Math.Abs(freeBounds.Left + freeBounds.Width / 2 - (freePet.Left + freePet.Width / 2)) < 0.01;
            var topPet = new Rect(-230, 0, 220, 220);
            var topBounds = QuotaCloudWindow.BoundsFor(topPet, freeWork,
                new Size(QuotaCloudWindow.BaseWidth, QuotaCloudWindow.BaseHeight), null);
            checks["floatingCloudAvoidsFaceAtTopRight"] = freeWork.Contains(topBounds) && topBounds.Right <= topPet.Left;
        }
        finally
        {
            EndPetGesture(cancel: true);
            cloudEnabled = originalCloudEnabled;
            cloudDockedState = originalCloudDockedState;
            cloudManualChoice = originalManualChoice;
            pet!.CleanState();
            pet.DisplayToNomal();
            size = originalSize;
            Width = Height = size;
            Left = originalLeft;
            Top = originalTop;
            ClampPosition();
            UpdateQuotaCloud();
        }
    }

    private void CaptureCloudPreview(string path)
    {
        UpdateLayout();
        quotaCloud!.UpdateLayout();
        var handle = new WindowInteropHelper(this).Handle;
        GetWindowRect(handle, out var petRect);
        GetWindowRect(new WindowInteropHelper(quotaCloud).Handle, out var cloudRect);
        var petBounds = new Rect(petRect.Left, petRect.Top, petRect.Right - petRect.Left, petRect.Bottom - petRect.Top);
        var cloudBounds = new Rect(cloudRect.Left, cloudRect.Top, cloudRect.Right - cloudRect.Left, cloudRect.Bottom - cloudRect.Top);
        var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
        var region = Rect.Union(petBounds, cloudBounds);
        region.Inflate(12, 12);
        region.Intersect(new Rect(work.X, work.Y, work.Width, work.Height));
        // 仅渲染本宿主的真实控件，并按屏幕边界裁切；不会截取用户桌面或其它应用内容。
        var visual = new DrawingVisual();
        using (var drawing = visual.RenderOpen())
        {
            drawing.DrawRectangle(Brushes.WhiteSmoke, null, new Rect(0, 0, region.Width, region.Height));
            petBounds.Offset(-region.X, -region.Y);
            cloudBounds.Offset(-region.X, -region.Y);
            drawing.DrawRectangle(new VisualBrush(this), null, petBounds);
            drawing.DrawRectangle(new VisualBrush(quotaCloud), null, cloudBounds);
        }
        // 按实际像素输出，避免放大的验收图让常驻提示看起来比实机更抢眼。
        var bitmap = new RenderTargetBitmap((int)Math.Ceiling(region.Width), (int)Math.Ceiling(region.Height),
            96, 96, PixelFormats.Pbgra32);
        bitmap.Render(visual);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var stream = File.Create(path);
        encoder.Save(stream);
    }

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
    private static extern int CloudWindowStyle(IntPtr window, int index);
}
