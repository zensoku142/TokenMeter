using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using VPet_Simulator.Core;
using static VPet_Simulator.Core.GraphInfo;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private const string DrinkText = "该喝点水啦，记得补充水分。";
    private const string RestText = "休息一下吧，起来活动活动，看看远处。";
    private static readonly string[] CloudModes = { "edge", "hover", "random", "hover_random" };
    private static readonly int[] RandomMinutes = { 3, 5, 10 };
    private static readonly int[] DrinkMinutes = { 15, 30, 45, 60 };
    private static readonly int[] RestMinutes = { 30, 45, 60, 90 };
    private string cloudMode = "edge";
    private int cloudRandomMinutes = 5;
    private bool drinkReminderEnabled;
    private int drinkReminderMinutes = 30;
    private bool restReminderEnabled;
    private int restReminderMinutes = 60;
    private bool? cloudManualChoice;
    private bool cloudPointerOver;
    private bool hoverCloudVisible;
    private double hoverDeadline;
    private double nextRandomQuota, nextDrinkReminder, nextRestReminder, randomCloudUntil;
    private bool notificationsSuspended;
    private bool warningSpeechPending;
    private string? pendingWarningAnimation;
    private int notificationGeneration;
    private Notice activeNotice;
    private bool ownsReminderText;
    private (Point Position, bool Edge)? notificationOrigin;
    private readonly DispatcherTimer notificationTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly DispatcherTimer cloudHoverTimer = new();
    private readonly List<(MenuItem Item, Func<bool> Selected)> notificationChoices = new();
    // 单调时钟不受校时影响；检查可替换时间源，无需等待真实的几十分钟。
    private Func<double> notificationNow = () => Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;

    [Flags]
    private enum Notice { None = 0, Quota = 1, Drink = 2, Rest = 4 }

    private bool HasHoverCloud => cloudMode is "hover" or "hover_random";
    private bool HasRandomCloud => cloudMode is "random" or "hover_random";
    private bool CanMoveAutonomously => ready && visible && !closing && !notificationsSuspended &&
        allowMove && !petPointerDown && petMenu?.IsOpen != true && activeNotice == Notice.None;
    private bool? LogicalDockedEdge => notificationOrigin?.Edge ?? DockedEdge;

    private void SyncAutonomy()
    {
        ambientTimer.IsEnabled = CanMoveAutonomously;
        pet?.SetMoveMode(CanMoveAutonomously, false, 1200000);
    }

    private void InitializeNotifications()
    {
        notificationTimer.Tick += (_, _) => AdvanceNotifications(notificationNow());
        cloudHoverTimer.Tick += (_, _) => AdvanceCloudHover(notificationNow());
        pet!.MouseEnter += (_, _) => UpdateCloudPointer();
        pet.MouseLeave += (_, _) => UpdateCloudPointer();
        quotaCloud!.MouseEnter += (_, _) => UpdateCloudPointer();
        quotaCloud.MouseLeave += (_, _) => UpdateCloudPointer();
        SystemEvents.PowerModeChanged += OnPowerModeChanged;
        ResetNotificationSchedule(notificationNow());
        if (visible) notificationTimer.Start();
    }

    private void AddNotificationMenus()
    {
        var mode = new MenuItem { Header = "额度气泡展示" };
        petMenu!.Items.Add(mode);
        string[] labels = { "贴边自动", "悬停展示", "随机展示", "悬停＋随机" };
        for (int i = 0; i < CloudModes.Length; i++)
        {
            string value = CloudModes[i];
            AddNotificationChoice(mode, labels[i], () => cloudMode == value, () => SetCloudMode(value));
        }
        var frequency = new MenuItem { Header = "额度随机间隔" };
        petMenu.Items.Add(frequency);
        foreach (int minutes in RandomMinutes)
            AddNotificationChoice(frequency, $"{minutes}–{RandomMaximum(minutes)} 分钟",
                () => cloudRandomMinutes == minutes, () => {
                    cloudRandomMinutes = minutes;
                    nextRandomQuota = notificationNow() + NextRandomQuotaDelay();
                });
        mode.SubmenuOpened += (_, _) => RefreshNotificationMenus();
        frequency.SubmenuOpened += (_, _) => RefreshNotificationMenus();
        foreach (bool drink in new[] { true, false })
        {
            var reminder = new MenuItem { Header = drink ? "喝水提醒" : "休息提醒" };
            petMenu.Items.Add(reminder);
            AddNotificationChoice(reminder, "启用", () => drink ? drinkReminderEnabled : restReminderEnabled, () => {
                if (drink) drinkReminderEnabled = !drinkReminderEnabled;
                else restReminderEnabled = !restReminderEnabled;
                if ((activeNotice & (drink ? Notice.Drink : Notice.Rest)) != 0) FinishNotification();
                ResetReminderDeadline(drink);
            });
            reminder.Items.Add(new Separator());
            foreach (int minutes in drink ? DrinkMinutes : RestMinutes)
                AddNotificationChoice(reminder, $"每 {minutes} 分钟",
                    () => (drink ? drinkReminderMinutes : restReminderMinutes) == minutes, () => {
                        if (drink) drinkReminderMinutes = minutes;
                        else restReminderMinutes = minutes;
                        ResetReminderDeadline(drink);
                    });
        }
        RefreshNotificationMenus();
    }

    private void AddNotificationChoice(MenuItem parent, string title, Func<bool> selected, Action choose)
    {
        var item = new MenuItem { Header = title, IsCheckable = true };
        notificationChoices.Add((item, selected));
        item.Click += (_, e) => {
            e.Handled = true;
            choose();
            RefreshNotificationMenus();
            UpdateQuotaCloud();
            SaveState();
        };
        parent.Items.Add(item);
    }

    private void RefreshNotificationMenus()
    {
        foreach (var (item, selected) in notificationChoices) item.IsChecked = selected();
    }

    private void SetCloudMode(string value)
    {
        if (!CloudModes.Contains(value)) return;
        if (activeNotice == Notice.Quota) FinishNotification();
        cloudMode = value;
        cloudManualChoice = null;
        ResetCloudHover();
        nextRandomQuota = notificationNow() + NextRandomQuotaDelay();
        UpdateCloudPointer();
        UpdateQuotaCloud();
    }

    private static int RandomMaximum(int minutes) => minutes == 3 ? 5 : minutes * 2;
    private double NextRandomQuotaDelay() => Random.Shared.NextDouble() *
        (RandomMaximum(cloudRandomMinutes) - cloudRandomMinutes) * 60 + cloudRandomMinutes * 60;

    private void ResetReminderDeadline(bool drink)
    {
        if (drink) nextDrinkReminder = notificationNow() + drinkReminderMinutes * 60;
        else nextRestReminder = notificationNow() + restReminderMinutes * 60;
    }

    private void ResetNotificationSchedule(double now)
    {
        nextRandomQuota = now + NextRandomQuotaDelay();
        nextDrinkReminder = now + drinkReminderMinutes * 60;
        nextRestReminder = now + restReminderMinutes * 60;
    }

    private void UpdateCloudPointer() => SetCloudPointer(pet?.IsMouseOver == true || quotaCloud?.IsMouseOver == true);

    private void SetCloudPointer(bool over)
    {
        if (!HasHoverCloud || over == cloudPointerOver || notificationsSuspended || !visible) return;
        cloudPointerOver = over;
        // 两个窗口之间保留离开宽限，鼠标移向额度云朵时不能把双击目标提前隐藏。
        hoverDeadline = notificationNow() + (over ? 0.3 : 0.5);
        cloudHoverTimer.Stop();
        cloudHoverTimer.Interval = TimeSpan.FromSeconds(over ? 0.3 : 0.5);
        cloudHoverTimer.Start();
    }

    private void AdvanceCloudHover(double now)
    {
        if (now < hoverDeadline) return;
        cloudHoverTimer.Stop();
        hoverCloudVisible = HasHoverCloud && cloudPointerOver && visible && !notificationsSuspended;
        UpdateQuotaCloud();
    }

    private void ResetCloudHover()
    {
        cloudHoverTimer.Stop();
        cloudPointerOver = hoverCloudVisible = false;
        hoverDeadline = double.PositiveInfinity;
    }

    private bool AutomaticCloudVisible(bool docked) => cloudMode == "edge" ? docked :
        (HasHoverCloud && hoverCloudVisible) || (HasRandomCloud && notificationNow() < randomCloudUntil);

    private bool CanStartNotification => ready && visible && IsVisible && !closing && !notificationsSuspended &&
        !petPointerDown && petMenu?.IsOpen != true && !warningSpeechPending && activeNotice == Notice.None &&
        pet!.MsgBar.Visibility != Visibility.Visible && pet.DisplayType.Type is
            GraphType.Default or GraphType.Idel or GraphType.StateONE or GraphType.StateTWO or
            GraphType.SideHide_Left_Main or GraphType.SideHide_Left_Rise or
            GraphType.SideHide_Right_Main or GraphType.SideHide_Right_Rise;

    private void AdvanceNotifications(double now)
    {
        if (!ready || !visible || !IsVisible || closing || notificationsSuspended) return;
        if (activeNotice != Notice.None)
        {
            if (petMenu?.IsOpen == true || petPointerDown) return;
            if (activeNotice == Notice.Quota ? now >= randomCloudUntil && !hoverCloudVisible :
                ownsReminderText && pet!.MsgBar.Visibility != Visibility.Visible)
                FinishNotification();
            return;
        }
        if (!CanStartNotification) return;
        Notice due = Notice.None;
        if (drinkReminderEnabled && now >= nextDrinkReminder) due |= Notice.Drink;
        if (restReminderEnabled && now >= nextRestReminder) due |= Notice.Rest;
        if (due != Notice.None)
        {
            StartNotification(due, now);
            if (due.HasFlag(Notice.Drink)) nextDrinkReminder = now + drinkReminderMinutes * 60;
            if (due.HasFlag(Notice.Rest)) nextRestReminder = now + restReminderMinutes * 60;
            // 生活提醒之后重新抽取额度间隔，避免刚说完话又马上弹出另一种提示。
            nextRandomQuota = now + NextRandomQuotaDelay();
        }
        else if (HasRandomCloud && now >= nextRandomQuota)
        {
            nextRandomQuota = now + NextRandomQuotaDelay();
            if (cloudManualChoice != false && quotaCloud?.IsVisible != true && pendingUsage != null)
                StartNotification(Notice.Quota, now);
        }
    }

    private void StartNotification(Notice notice, double now)
    {
        CancelAutonomousSequence();
        activeNotice = notice;
        ++notificationGeneration;
        SyncAutonomy();
        if (DockedEdge is bool edge) notificationOrigin = (new Point(Left, Top), edge);
        pet!.CleanState();
        // 临时回到屏幕内才能完整显示原版文字框；保存和自动显隐仍使用原始贴边状态。
        ClampPosition();
        if (notice == Notice.Quota) randomCloudUntil = now + 8;
        else
        {
            string text = notice == Notice.Drink ? DrinkText : notice == Notice.Rest ? RestText : DrinkText + "\n" + RestText;
            ownsReminderText = true;
            pet.MsgBar.Show(save.Name, text);
        }
        // 三种主动提示统一保持原有常规姿态，不再载入或播放额外的轻拍动作。
        pet.DisplayToNomal();
        UpdateQuotaCloud();
    }

    private void FinishNotification(bool restorePosition = true)
    {
        if (activeNotice == Notice.None) return;
        ++notificationGeneration;
        if (ownsReminderText) pet!.MsgBar.ForceClose();
        ownsReminderText = false;
        randomCloudUntil = 0;
        if (notificationOrigin is { } origin && restorePosition)
        {
            Left = origin.Position.X;
            Top = origin.Position.Y;
            if (!closing && visible && !notificationsSuspended) TrySnapPetToEdge(origin.Edge);
        }
        else if (!closing && visible && !notificationsSuspended) pet!.DisplayToNomal();
        if (notificationOrigin != null && !restorePosition)
        {
            cloudManualChoice = null;
            cloudDockedState = null;
        }
        notificationOrigin = null;
        activeNotice = Notice.None;
        SyncAutonomy();
        UpdateQuotaCloud();
    }

    private void ShowUsageWarning(string text)
    {
        CancelAutonomousSequence();
        FinishNotification();
        if (!visible || notificationsSuspended) return;
        int generation = ++notificationGeneration;
        warningSpeechPending = false;
        string? animation = pet!.SayRndFunction(text);
        pendingWarningAnimation = animation;
        if (!string.IsNullOrWhiteSpace(animation) && pet.DisplayType.Type == GraphType.Default)
        {
            warningSpeechPending = true;
            // 保留原有随机说话动作，但由宿主校验生命周期，隐藏后不能再被旧动画回调弹出消息。
            pet.Display(animation, AnimatType.A_Start, () => {
                if (Dispatcher.HasShutdownStarted) return;
                Dispatcher.BeginInvoke(() => {
                    if (generation != notificationGeneration || closing || !visible || notificationsSuspended) return;
                    warningSpeechPending = false;
                    pet.MsgBar.Show(save.Name, text, animation);
                    pet.DisplayBLoopingForce(animation);
                });
            });
        }
        else pet.MsgBar.Show(save.Name, text, animation);
    }

    private void PauseNotifications()
    {
        CancelAutonomousSequence();
        notificationTimer.Stop();
        ResetCloudHover();
        FinishNotification();
        ++notificationGeneration;
        warningSpeechPending = false;
        pet?.MsgBar?.ForceClose();
    }

    private void ResumeNotifications()
    {
        ResetNotificationSchedule(notificationNow());
        if (ready && visible && !closing && !notificationsSuspended) notificationTimer.Start();
    }

    private void OnPowerModeChanged(object sender, PowerModeChangedEventArgs e)
    {
        if (Dispatcher.HasShutdownStarted || e.Mode is not (PowerModes.Suspend or PowerModes.Resume)) return;
        Dispatcher.BeginInvoke(() => {
            if (closing) return;
            notificationsSuspended = e.Mode == PowerModes.Suspend;
            if (e.Mode == PowerModes.Suspend) { PauseNotifications(); quotaCloud?.Hide(); }
            else if (e.Mode == PowerModes.Resume) { pet?.DisplayToNomal(); ResumeNotifications(); UpdateQuotaCloud(); }
            SyncAutonomy();
        });
    }

    private void DisposeNotifications()
    {
        PauseNotifications();
        SystemEvents.PowerModeChanged -= OnPowerModeChanged;
    }

    private void LoadNotificationPreferences(JsonElement root)
    {
        bool Enabled(string key) => root.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.True;
        int Minutes(string key, int[] choices, int fallback) => root.TryGetProperty(key, out var value) &&
            value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out int number) && choices.Contains(number) ? number : fallback;
        // 旧布局和手工损坏的单个偏好都回退默认，不能影响其它布局字段或阻止桌宠启动。
        cloudMode = root.TryGetProperty("cloudMode", out var mode) && mode.ValueKind == JsonValueKind.String &&
            CloudModes.Contains(mode.GetString()) ? mode.GetString()! : "edge";
        cloudRandomMinutes = Minutes("cloudRandomMinutes", RandomMinutes, 5);
        drinkReminderEnabled = Enabled("drinkReminderEnabled");
        drinkReminderMinutes = Minutes("drinkReminderMinutes", DrinkMinutes, 30);
        restReminderEnabled = Enabled("restReminderEnabled");
        restReminderMinutes = Minutes("restReminderMinutes", RestMinutes, 60);
    }
}
