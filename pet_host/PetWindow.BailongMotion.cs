using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private readonly Dictionary<string, JsonElement> bailongMovements = new(StringComparer.Ordinal);
    private readonly DispatcherTimer bailongMoveTimer = new() { Interval = TimeSpan.FromMilliseconds(16) };
    private readonly Stopwatch bailongMoveClock = new();
    private Point bailongMoveOrigin, bailongMoveTarget;
    private double bailongMoveStartMs, bailongMoveStopMs;
    private bool bailongMovementMirrored, bailongMovementAutonomous;

    private void StartBailongMovement(string name, bool autonomous = false)
    {
        StopBailongMovement();
        const string prefix = "bailong.action.";
        if (character != "bailong" || !name.StartsWith(prefix, StringComparison.Ordinal) ||
            !bailongMovements.TryGetValue(name[prefix.Length..], out var item) || LogicalDockedEdge.HasValue) return;
        ClampPosition();
        var work = WorkArea();
        double right = Math.Max(work.Left, work.Right - Width);
        double bottom = Math.Max(work.Top, work.Bottom - Height);
        bailongMoveOrigin = new Point(Left, Top);
        bailongMoveStartMs = item.GetProperty("moveStartMs").GetDouble();
        bailongMoveStopMs = item.GetProperty("moveStopMs").GetDouble();
        int sourceDirection = item.GetProperty("sourceFacing").GetString() == "left" ? -1 : 1;
        int direction = sourceDirection;
        double minDistance = Math.Min(48, size * 0.2);
        if ((direction < 0 ? Left - work.Left : right - Left) < minDistance)
            direction = -direction;
        double horizontalRoom = Math.Max(0, direction < 0 ? Left - work.Left : right - Left);
        double dx = direction, dy = 0;
        if (item.TryGetProperty("movementAngleRange", out var angles))
        {
            int verticalDirection = Top - work.Top >= bottom - Top ? -1 : 1;
            double angle = (angles[0].GetDouble() + Random.Shared.NextDouble() *
                (angles[1].GetDouble() - angles[0].GetDouble())) * Math.PI / 180;
            dx = direction * Math.Cos(angle);
            dy = verticalDirection * Math.Sin(angle);
        }
        double speed = item.TryGetProperty("movementSpeed", out var value) ? value.GetDouble() :
            item.GetProperty("number").GetString() == "01" ? 0.5 : 1;
        double distance = size * (0.65 + Random.Shared.NextDouble() * 0.6) * speed *
            Math.Max(0.25, (bailongMoveStopMs - bailongMoveStartMs) / 2583);
        // 按原片标记的移动时间窗位移；整条轨迹按工作区截短，保持飞行角度且不跨出屏幕。
        distance = Math.Min(distance, horizontalRoom / Math.Max(0.001, Math.Abs(dx)));
        if (dy != 0)
            distance = Math.Min(distance, Math.Max(0, dy < 0 ? Top - work.Top : bottom - Top) / Math.Abs(dy));
        if (distance <= 0 || bailongMoveStopMs <= bailongMoveStartMs) return;
        var target = bailongMoveOrigin + new Vector(dx * distance, dy * distance);
        var dpi = VisualTreeHelper.GetDpi(this);
        // WPF 最终按物理像素定位；先对齐目标，保持小数落点与实际窗口位置一致。
        bailongMoveTarget = new Point(
            Math.Clamp(Math.Round(target.X * dpi.DpiScaleX) / dpi.DpiScaleX, work.Left, right),
            Math.Clamp(Math.Round(target.Y * dpi.DpiScaleY) / dpi.DpiScaleY, work.Top, bottom));
        bailongMovementAutonomous = autonomous;
        bailongMovementMirrored = direction != sourceDirection;
        if (bailongMovementMirrored)
        {
            // 屏幕边缘改为朝内移动，镜像整个人物，使动作朝向与实际位移一致。
            var mirror = new ScaleTransform(-1, 1, 250, 0);
            pet!.PetGrid.RenderTransform = mirror;
            pet.PetGrid2.RenderTransform = mirror;
        }
        bailongMoveClock.Restart();
        bailongMoveTimer.Start();
    }

    private void AdvanceBailongMovement(double? elapsedMilliseconds = null)
    {
        if (!bailongMoveTimer.IsEnabled) return;
        if (character != "bailong" || !visible || closing || switchingCharacter || notificationsSuspended ||
            petPointerDown || petMenu?.IsOpen == true || activeNotice != Notice.None ||
            LogicalDockedEdge.HasValue || (bailongMovementAutonomous && !allowMove))
        {
            StopBailongMovement();
            return;
        }
        double progress = Math.Clamp(((elapsedMilliseconds ?? bailongMoveClock.Elapsed.TotalMilliseconds) -
            bailongMoveStartMs) / (bailongMoveStopMs - bailongMoveStartMs), 0, 1);
        Left = bailongMoveOrigin.X + (bailongMoveTarget.X - bailongMoveOrigin.X) * progress;
        Top = bailongMoveOrigin.Y + (bailongMoveTarget.Y - bailongMoveOrigin.Y) * progress;
        if (progress >= 1) StopBailongMovement(restoreFacing: false);
    }

    private void StopBailongMovement(bool restoreFacing = true)
    {
        bailongMoveTimer.Stop();
        bailongMoveClock.Stop();
        if (!restoreFacing) return;
        // 收尾段播完或被交互打断后再恢复站姿朝向，避免在移动片段中途翻面。
        if (bailongMovementMirrored && character == "bailong" && pet != null)
        {
            pet.PetGrid.RenderTransform = Transform.Identity;
            pet.PetGrid2.RenderTransform = Transform.Identity;
        }
        bailongMovementMirrored = false;
    }
}
