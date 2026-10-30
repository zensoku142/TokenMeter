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
    private int whaleMotionGeneration;
    private long whaleLastImpact;
    private bool WhaleMotionActive => whaleSpringTimer.IsEnabled || whaleThrowTimer.IsEnabled;
    private bool UsesThrowPhysics => character is "whale" or "bailong";
    // 白龙娘复用现有物理参数，但独立安装时不依赖鲸鱼娘的素材或动作清单。
    private (double Gravity, double Restitution, double Friction, bool CeilingBounce, double Power) ThrowPhysicsSettings
    {
        get
        {
            if (character != "whale") return (1400, 0.78, 2.5, true, 1);
            var physics = WhaleCatalog.GetProperty("physics");
            return (physics.GetProperty("gravity").GetDouble(), physics.GetProperty("restitution").GetDouble(),
                physics.GetProperty("groundFriction").GetDouble(), physics.GetProperty("ceilingBounce").GetBoolean(),
                physics.GetProperty("throwPower").GetDouble());
        }
    }

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
        if (character == "whale" && whaleTrail.Count > 0 && Math.Abs(pointer.X - whaleTrail[^1].Position.X) > 4)
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
        int steps = Math.Max(1, (int)Math.Ceiling(dt * 120));
        // 小步积分保持弹簧跟手稳定，避免低帧率时一次大步更新产生抖动。
        for (int i = 0; i < steps; i++)
        {
            double step = dt / steps;
            whaleSpringVx += ((whaleTargetX - whaleSpringX) * 200 - whaleSpringVx * 30) * step;
            whaleSpringVy += ((whaleTargetY - whaleSpringY) * 200 - whaleSpringVy * 30) * step;
            whaleSpringX += whaleSpringVx * step;
            whaleSpringY += whaleSpringVy * step;
        }
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
        double power = ThrowPhysicsSettings.Power;
        double candidate = (baseSpeed + peak) / 2;
        double speed = 3600 * (1 - Math.Exp(-candidate / 3600)) * power;
        if (speed < 500 * power) return null;
        return (vx / baseSpeed * speed, vy / baseSpeed * speed);
    }

    private void FinishWhaleGesture(bool cancel)
    {
        whaleSpringTimer.Stop();
        whaleGestureClock.Stop();
        var release = cancel ? null : WhaleReleaseVelocity();
        // 快速甩向屏幕外缘应进入碰撞反弹，普通慢拖放下才沿用贴边吸附。
        bool snapped = !cancel && release == null && TrySnapPetToEdge();
        if (snapped) return;
        if (cancel)
        {
            ClampDraggedWindow();
            pet!.Display(GraphType.Raised_Static, AnimatType.C_End, pet.DisplayToNomal);
            return;
        }
        if (release is { } velocity)
        {
            var handle = new WindowInteropHelper(this).Handle;
            if (GetWindowRect(handle, out var rect))
            {
                whaleThrowX = rect.Left;
                whaleThrowY = rect.Top;
                whaleThrowVx = velocity.X;
                whaleThrowVy = velocity.Y;
                ++whaleMotionGeneration;
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
        int steps = Math.Max(1, (int)Math.Ceiling(dt * 120));
        for (int i = 0; i < steps && whaleThrowTimer.IsEnabled; i++) AdvanceWhaleThrowStep(dt / steps);
        // 子步只计算重力和屏幕边界，每帧只提交一次窗口位置，避免透明窗重复重绘及气泡布局。
        SetWindowPos(new WindowInteropHelper(this).Handle, IntPtr.Zero,
            (int)Math.Round(whaleThrowX), (int)Math.Round(whaleThrowY), 0, 0, DragPositionFlags);
    }

    private void AdvanceWhaleThrowStep(double dt)
    {
        var physics = ThrowPhysicsSettings;
        double gravity = physics.Gravity;
        double restitution = physics.Restitution;
        double friction = physics.Friction;
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
        var pixels = PetVisiblePixels(character);
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
        {
            whaleThrowX = minX;
            if (whaleThrowVx < 0) { PulseWhaleImpact(new Vector(-1, 0), -whaleThrowVx); whaleThrowVx *= -restitution; }
        }
        else if (whaleThrowX > maxX && !Neighbor(screen.Bounds.Right + 1, centerY))
        {
            whaleThrowX = maxX;
            if (whaleThrowVx > 0) { PulseWhaleImpact(new Vector(1, 0), whaleThrowVx); whaleThrowVx *= -restitution; }
        }
        if (whaleThrowY < minY && physics.CeilingBounce &&
            !Neighbor(centerX, screen.Bounds.Top - 1))
        {
            whaleThrowY = minY;
            if (whaleThrowVy < 0) { PulseWhaleImpact(new Vector(0, -1), -whaleThrowVy); whaleThrowVy *= -restitution; }
        }
        else if (whaleThrowY >= maxY && !Neighbor(centerX, screen.Bounds.Bottom + 1))
        {
            whaleThrowY = maxY;
            double impact = whaleThrowVy;
            whaleThrowVx *= Math.Max(0, 1 - friction * dt);
            if (impact > 0) whaleThrowVy = impact < 40 ? 0 : -impact * restitution;
            PulseWhaleImpact(new Vector(0, 1), impact);
        }
        if (whaleThrowY >= maxY - 1 && Math.Abs(whaleThrowVy) < 1 && Math.Abs(whaleThrowVx) < 15 &&
            !Neighbor(centerX, screen.Bounds.Bottom + 1))
        {
            whaleThrowTimer.Stop();
            int generation = whaleMotionGeneration;
            pet!.Display(GraphType.Raised_Static, AnimatType.C_End, () => {
                // 落地动画尚未结束时可能再次被抓起，旧回调不能覆盖新动作。
                if (!Dispatcher.HasShutdownStarted) Dispatcher.BeginInvoke(() => {
                    if (generation != whaleMotionGeneration || closing || !UsesThrowPhysics || !visible) return;
                    pet.DisplayToNomal();
                    SaveState();
                    SyncAutonomy();
                });
            });
        }
    }

    private void StopWhaleMotion()
    {
        ++whaleMotionGeneration;
        whaleSpringTimer.Stop();
        whaleThrowTimer.Stop();
        whaleGestureClock.Stop();
        if (pet != null) pet.RenderTransform = Transform.Identity;
    }

    private void PulseWhaleImpact(Vector normal, double speed)
    {
        var dpi = VisualTreeHelper.GetDpi(this);
        double dipSpeed = speed / (Math.Abs(normal.X) > Math.Abs(normal.Y) ? dpi.DpiScaleX : dpi.DpiScaleY);
        long now = Environment.TickCount64;
        // 微小落地回弹不重复播放形变，连续接触也给上一段反馈留下恢复时间。
        if (dipSpeed < 120 || now - whaleLastImpact < 100) return;
        whaleLastImpact = now;
        PulseWhale(Math.Clamp(0.85 - dipSpeed / 5000, 0.55, 0.82), normal);
    }

    private void PulseWhale(double minimumHeight = 0.55, Vector? normal = null)
    {
        bool horizontal = normal is { } direction && Math.Abs(direction.X) > Math.Abs(direction.Y);
        pet!.RenderTransformOrigin = horizontal ? new Point(normal!.Value.X > 0 ? 1 : 0, 0.5) :
            new Point(0.5, normal?.Y < 0 ? 0 : 1);
        var squash = new ScaleTransform(1, 1);
        pet.RenderTransform = squash;
        var ease = new BackEase { Amplitude = 0.45, EasingMode = EasingMode.EaseOut };
        double stretch = 1 + (1 - minimumHeight) * 0.35;
        squash.BeginAnimation(ScaleTransform.ScaleXProperty, new DoubleAnimation(horizontal ? minimumHeight : stretch, 1,
            TimeSpan.FromMilliseconds(260)) { EasingFunction = ease });
        squash.BeginAnimation(ScaleTransform.ScaleYProperty, new DoubleAnimation(horizontal ? stretch : minimumHeight, 1,
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
