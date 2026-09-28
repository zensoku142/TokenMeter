using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using System.Threading.Tasks;
using VPet_Simulator.Core;
using static VPet_Simulator.Core.GraphInfo;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private readonly DispatcherTimer whaleSpringTimer = new() { Interval = TimeSpan.FromMilliseconds(16) };
    private readonly DispatcherTimer whaleThrowTimer = new() { Interval = TimeSpan.FromMilliseconds(16) };
    private readonly Stopwatch whaleGestureClock = new();
    private readonly List<(double Time, Point Position)> whaleTrail = new();
    private long whaleLastPhysicsTick;
    private double whaleSpringX, whaleSpringY, whaleTargetX, whaleTargetY, whaleSpringVx, whaleSpringVy;
    private double whaleThrowX, whaleThrowY, whaleThrowVx, whaleThrowVy;
    private bool WhaleMotionActive => whaleSpringTimer.IsEnabled || whaleThrowTimer.IsEnabled;

    private void InitializeWhalePhysics()
    {
        whaleSpringTimer.Tick += (_, _) => AdvanceWhaleSpring();
        whaleThrowTimer.Tick += (_, _) => AdvanceWhaleThrow();
    }

    private void StartWhaleGesture(Point pointer)
    {
        StopWhaleMotion();
        whaleSpringX = whaleTargetX = petWindowScreen.X;
        whaleSpringY = whaleTargetY = petWindowScreen.Y;
        whaleSpringVx = whaleSpringVy = 0;
        whaleTrail.Clear();
        whaleGestureClock.Restart();
        AddWhaleSample(pointer);
    }

    private void AddWhaleSample(Point pointer)
    {
        double now = whaleGestureClock.Elapsed.TotalMilliseconds;
        if (whaleTrail.Count == 0 || whaleTrail[^1].Position != pointer)
            whaleTrail.Add((now, pointer));
        whaleTrail.RemoveAll(sample => now - sample.Time > 200);
    }

    private void UpdateWhaleGesture(Point pointer, Vector delta)
    {
        if (whaleTrail.Count > 0 && Math.Abs(pointer.X - whaleTrail[^1].Position.X) > 4)
            SetWhaleFacing(pointer.X > whaleTrail[^1].Position.X);
        AddWhaleSample(pointer);
        whaleTargetX = petWindowScreen.X + delta.X;
        whaleTargetY = petWindowScreen.Y + delta.Y;
        if (petDragging && !whaleSpringTimer.IsEnabled)
        {
            whaleLastPhysicsTick = Stopwatch.GetTimestamp();
            whaleSpringTimer.Start();
        }
    }

    private double WhaleFrameSeconds()
    {
        long now = Stopwatch.GetTimestamp();
        double elapsed = (now - whaleLastPhysicsTick) / (double)Stopwatch.Frequency;
        whaleLastPhysicsTick = now;
        return Math.Clamp(elapsed, 0, 0.05);
    }

    private void AdvanceWhaleSpring()
    {
        double dt = WhaleFrameSeconds();
        // 与参考项目相同的过阻尼弹簧常数；角色跟手但不瞬移到指针。
        whaleSpringVx += ((whaleTargetX - whaleSpringX) * 200 - whaleSpringVx * 30) * dt;
        whaleSpringVy += ((whaleTargetY - whaleSpringY) * 200 - whaleSpringVy * 30) * dt;
        whaleSpringX += whaleSpringVx * dt;
        whaleSpringY += whaleSpringVy * dt;
        SetWindowPos(new WindowInteropHelper(this).Handle, IntPtr.Zero,
            (int)Math.Round(whaleSpringX), (int)Math.Round(whaleSpringY), 0, 0, DragPositionFlags);
    }

    private (double X, double Y)? WhaleReleaseVelocity()
    {
        double now = whaleGestureClock.Elapsed.TotalMilliseconds;
        var recent = whaleTrail.Where(sample => now - sample.Time <= 120).ToArray();
        // 只有松手前仍在快速移动才算甩抛；停顿后放下应保持普通拖拽落点。
        if (recent.Length < 2 || now - recent[^1].Time > 60) return null;
        double span = recent[^1].Time - recent[0].Time;
        if (span < 30) return null;
        Vector delta = recent[^1].Position - recent[0].Position;
        var dpi = VisualTreeHelper.GetDpi(this);
        double distance = new Vector(delta.X / dpi.DpiScaleX, delta.Y / dpi.DpiScaleY).Length;
        // 用 DIP 判断手势，避免高 DPI 屏幕上同样的拖动更容易越过甩抛门槛。
        if (distance < 80 || distance / span * 1000 < 1400) return null;
        double vx = delta.X / span * 1000, vy = delta.Y / span * 1000;
        double baseSpeed = Math.Sqrt(vx * vx + vy * vy);
        if (baseSpeed < 1) return null;
        double peak = baseSpeed;
        for (int i = 1; i < recent.Length; i++)
        {
            double ms = recent[i].Time - recent[i - 1].Time;
            if (ms >= 8) peak = Math.Max(peak, (recent[i].Position - recent[i - 1].Position).Length / ms * 1000);
        }
        double power = WhaleCatalog.GetProperty("physics").GetProperty("throwPower").GetDouble();
        double candidate = (baseSpeed + peak) / 2;
        double speed = 3600 * (1 - Math.Exp(-candidate / 3600)) * power;
        if (speed < 500 * power) return null;
        return (vx / baseSpeed * speed, vy / baseSpeed * speed);
    }

    private void FinishWhaleGesture(bool cancel)
    {
        whaleSpringTimer.Stop();
        whaleGestureClock.Stop();
        bool snapped = !cancel && TrySnapPetToEdge();
        if (snapped) return;
        if (cancel)
        {
            ClampDraggedWindow();
            pet!.Display(GraphType.Raised_Static, AnimatType.C_End, pet.DisplayToNomal);
            return;
        }
        if (WhaleReleaseVelocity() is { } release)
        {
            var handle = new WindowInteropHelper(this).Handle;
            if (GetWindowRect(handle, out var rect))
            {
                whaleThrowX = rect.Left;
                whaleThrowY = rect.Top;
                whaleThrowVx = release.X;
                whaleThrowVy = release.Y;
                whaleLastPhysicsTick = Stopwatch.GetTimestamp();
                whaleThrowTimer.Start();
                return;
            }
        }
        ClampDraggedWindow();
        pet!.Display(GraphType.Raised_Static, AnimatType.C_End, pet.DisplayToNomal);
    }

    private void AdvanceWhaleThrow()
    {
        double dt = WhaleFrameSeconds();
        var physics = WhaleCatalog.GetProperty("physics");
        double gravity = physics.GetProperty("gravity").GetDouble();
        double restitution = physics.GetProperty("restitution").GetDouble();
        double friction = physics.GetProperty("groundFriction").GetDouble();
        whaleThrowVy += gravity * dt;
        whaleThrowX += whaleThrowVx * dt;
        whaleThrowY += whaleThrowVy * dt;
        var handle = new WindowInteropHelper(this).Handle;
        if (!GetWindowRect(handle, out var window)) { StopWhaleMotion(); return; }
        int windowWidth = window.Right - window.Left, windowHeight = window.Bottom - window.Top;
        var screen = System.Windows.Forms.Screen.FromPoint(new System.Drawing.Point(
            (int)Math.Round(whaleThrowX + windowWidth / 2.0),
            (int)Math.Round(whaleThrowY + windowHeight / 2.0)));
        var work = screen.WorkingArea;
        var pixels = WhaleVisiblePixels();
        double minX = work.Left - pixels.Left * windowWidth;
        double maxX = work.Right - pixels.Right * windowWidth;
        double minY = work.Top - pixels.Top * windowHeight;
        double maxY = work.Bottom - pixels.Bottom * windowHeight;
        bool Neighbor(int x, int y) => System.Windows.Forms.Screen.AllScreens.Any(other =>
            other != screen && other.Bounds.Contains(new System.Drawing.Point(x, y)));
        int centerX = (int)Math.Round(whaleThrowX + windowWidth / 2.0);
        int centerY = (int)Math.Round(whaleThrowY + windowHeight / 2.0);
        // 屏幕接缝有邻屏就允许角色自然跨越；真正外缘才反弹，避免多屏外接矩形里的空洞。
        if (whaleThrowX < minX && !Neighbor(screen.Bounds.Left - 1, centerY))
        { whaleThrowX = minX; whaleThrowVx = Math.Abs(whaleThrowVx) * restitution; }
        else if (whaleThrowX > maxX && !Neighbor(screen.Bounds.Right + 1, centerY))
        { whaleThrowX = maxX; whaleThrowVx = -Math.Abs(whaleThrowVx) * restitution; }
        if (whaleThrowY < minY && physics.GetProperty("ceilingBounce").GetBoolean() &&
            !Neighbor(centerX, screen.Bounds.Top - 1))
        {
            whaleThrowY = minY;
            whaleThrowVy = Math.Abs(whaleThrowVy) * restitution;
        }
        else if (whaleThrowY >= maxY && !Neighbor(centerX, screen.Bounds.Bottom + 1))
        {
            whaleThrowY = maxY;
            double impact = whaleThrowVy;
            whaleThrowVx *= Math.Max(0, 1 - friction * dt);
            whaleThrowVy = Math.Abs(whaleThrowVy) < 40 ? 0 : -Math.Abs(whaleThrowVy) * restitution;
            if (impact > 300) PulseWhale(Math.Max(0.55, 0.8 - (impact - 300) / 1200 * 0.25));
        }
        SetWindowPos(handle, IntPtr.Zero, (int)Math.Round(whaleThrowX), (int)Math.Round(whaleThrowY),
            0, 0, DragPositionFlags);
        if (whaleThrowY >= maxY - 1 && Math.Abs(whaleThrowVy) < 1 && Math.Abs(whaleThrowVx) < 15 &&
            !Neighbor(centerX, screen.Bounds.Bottom + 1))
        {
            whaleThrowTimer.Stop();
            pet!.Display(GraphType.Raised_Static, AnimatType.C_End, () => {
                pet.DisplayToNomal();
                SaveState();
                SyncAutonomy();
            });
        }
    }

    private void StopWhaleMotion()
    {
        whaleSpringTimer.Stop();
        whaleThrowTimer.Stop();
        whaleGestureClock.Stop();
    }

    private void PulseWhale(double minimumHeight = 0.55)
    {
        pet!.RenderTransformOrigin = new Point(0.5, 1);
        var squash = new ScaleTransform(1, 1);
        pet.RenderTransform = squash;
        var ease = new BackEase { Amplitude = 0.45, EasingMode = EasingMode.EaseOut };
        squash.BeginAnimation(ScaleTransform.ScaleXProperty, new DoubleAnimation(1 + (1 - minimumHeight) * 0.35, 1,
            TimeSpan.FromMilliseconds(260)) { EasingFunction = ease });
        squash.BeginAnimation(ScaleTransform.ScaleYProperty, new DoubleAnimation(minimumHeight, 1,
            TimeSpan.FromMilliseconds(260)) { EasingFunction = ease });
    }

    private async Task<bool> CheckWhaleCornerDrag()
    {
        double originalLeft = Left, originalTop = Top;
        bool? originalDock = manualDockedEdge;
        try
        {
            manualDockedEdge = null;
            pet!.DisplayToNomal();
            var handle = new WindowInteropHelper(this).Handle;
            var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
            Left = work.Left + (work.Width - Width) / 2;
            Top = work.Top + (work.Height - Height) / 2;
            UpdateLayout();
            if (!GetWindowRect(handle, out var rect)) return false;
            var pointer = new Point(rect.Left + 100, rect.Top + 100);
            if (!StartPetGesture(pointer, new Point(250, 250))) return false;
            UpdatePetGesture(pointer + new Vector(work.Right - rect.Right + 40,
                work.Bottom - rect.Bottom + 15));
            await Task.Delay(300);
            EndPetGesture(cancel: false);
            await Task.Delay(1200);
            return !closing && IsVisible && pet!.IsWorking;
        }
        finally
        {
            EndPetGesture(cancel: true);
            StopWhaleMotion();
            manualDockedEdge = originalDock;
            Left = originalLeft;
            Top = originalTop;
            if (!closing) pet?.DisplayToNomal();
            UpdateQuotaCloud();
        }
    }

    private async Task<bool> CheckWhaleThrow()
    {
        double originalLeft = Left, originalTop = Top;
        bool? originalDock = manualDockedEdge;
        try
        {
            manualDockedEdge = null;
            var handle = new WindowInteropHelper(this).Handle;
            var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
            Left = work.Left + (work.Width - Width) / 2;
            Top = work.Top + (work.Height - Height) / 2;
            UpdateLayout();
            if (!GetWindowRect(handle, out var rect)) return false;
            var pointer = new Point(rect.Left + 100, rect.Top + 100);
            if (!StartPetGesture(pointer, new Point(250, 250))) return false;
            UpdatePetGesture(pointer + new Vector(60, -20));
            // 自检没有真实按下鼠标；让出 Dispatcher 会收到未按键的 MouseMove 并取消模拟手势。
            // 仅在这段 60ms 的合成采样中阻塞，松手后仍由真实物理计时器验证位移。
            System.Threading.Thread.Sleep(30);
            UpdatePetGesture(pointer + new Vector(180, -75));
            System.Threading.Thread.Sleep(30);
            UpdatePetGesture(pointer + new Vector(320, -130));
            EndPetGesture(cancel: false);
            bool launched = whaleThrowTimer.IsEnabled;
            GetWindowRect(handle, out var released);
            await Task.Delay(250);
            GetWindowRect(handle, out var flying);
            bool passed = launched && (released.Left != flying.Left || released.Top != flying.Top) && !closing;
            if (!passed) Console.Error.WriteLine($"Whale throw check: launched={launched}, " +
                $"released={released.Left},{released.Top}, flying={flying.Left},{flying.Top}, " +
                $"trail={whaleTrail.Count}, velocity={whaleThrowVx:0},{whaleThrowVy:0}");
            return passed;
        }
        finally
        {
            EndPetGesture(cancel: true);
            StopWhaleMotion();
            manualDockedEdge = originalDock;
            Left = originalLeft;
            Top = originalTop;
            if (!closing) pet?.DisplayToNomal();
            UpdateQuotaCloud();
        }
    }
}
