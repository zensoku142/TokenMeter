using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using VPet_Simulator.Core;
using static VPet_Simulator.Core.GraphInfo;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private async Task RunNotificationChecks(Dictionary<string, bool> checks, string output)
    {
        SaveState();
        string originalPreferences = File.ReadAllText(Path.Combine(dataDirectory, "layout.json"));
        var originalClock = notificationNow;
        bool originalMove = allowMove;
        bool? originalManualDock = manualDockedEdge;
        int originalSize = size;
        var originalPosition = new Point(Left, Top);
        double now = 1000;
        notificationTimer.Stop();
        saveTimer.Stop();
        PauseNotifications();
        notificationNow = () => now;
        allowMove = false;
        SyncAutonomy();
        var handle = new WindowInteropHelper(this).Handle;
        void Preferences(string json)
        {
            using var document = JsonDocument.Parse(json);
            LoadNotificationPreferences(document.RootElement);
        }
        void VisibilityCommand(bool value, bool stopTimer = true)
        {
            using var document = JsonDocument.Parse(JsonSerializer.Serialize(new { type = "visibility", visible = value }));
            Receive(document.RootElement);
            if (stopTimer) notificationTimer.Stop();
        }
        void Normal()
        {
            FinishNotification(restorePosition: false);
            pet!.MsgBar.ForceClose();
            warningSpeechPending = false;
            ++notificationGeneration;
            pet.CleanState();
            pet.DisplayToNomal();
            manualDockedEdge = null;
            cloudManualChoice = null;
            cloudDockedState = null;
            ResetCloudHover();
            UpdateQuotaCloud();
        }
        try
        {
            Preferences("{}");
            Normal();
            var work = WorkArea();
            Left = work.Left + (work.Width - Width) / 2;
            Top = work.Top + (work.Height - Height) / 2;
            checks["notificationLegacyDefaults"] = cloudMode == "edge" && cloudRandomMinutes == 5 &&
                !drinkReminderEnabled && !restReminderEnabled && drinkReminderMinutes == 30 && restReminderMinutes == 60;
            Preferences("{\"cloudMode\":17,\"cloudRandomMinutes\":-3,\"drinkReminderEnabled\":\"yes\",\"drinkReminderMinutes\":15.5,\"restReminderMinutes\":99999999999}");
            checks["notificationInvalidPreferencesUseDefaults"] = cloudMode == "edge" && cloudRandomMinutes == 5 &&
                !drinkReminderEnabled && drinkReminderMinutes == 30 && restReminderMinutes == 60;
            Preferences("{\"cloudMode\":\"hover_random\",\"cloudRandomMinutes\":10,\"drinkReminderEnabled\":true,\"drinkReminderMinutes\":45,\"restReminderEnabled\":true,\"restReminderMinutes\":90}");
            SaveState();
            string changedPreferences = File.ReadAllText(Path.Combine(dataDirectory, "layout.json"));
            Preferences("{}");
            Preferences(changedPreferences);
            checks["notificationPreferencesRoundTrip"] = cloudMode == "hover_random" && cloudRandomMinutes == 10 &&
                drinkReminderEnabled && drinkReminderMinutes == 45 && restReminderEnabled && restReminderMinutes == 90;
            checks["notificationTransientStateNotSaved"] = !changedPreferences.Contains("nextDrinkReminder") &&
                !changedPreferences.Contains("cloudManualChoice") && !changedPreferences.Contains("randomCloudUntil");
            Preferences("{}");
            var modeMenu = petMenu!.Items.OfType<MenuItem>().Single(x => x.Header.ToString() == "额度气泡展示");
            bool menuModesWork = true;
            foreach (var choice in modeMenu.Items.OfType<MenuItem>())
            {
                choice.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
                using var persisted = JsonDocument.Parse(File.ReadAllText(Path.Combine(dataDirectory, "layout.json")));
                menuModesWork &= choice.IsChecked && modeMenu.Items.OfType<MenuItem>().Count(x => x.IsChecked) == 1 &&
                    persisted.RootElement.GetProperty("cloudMode").GetString() == cloudMode;
            }
            checks["allCloudMenuModesApplyAndSave"] = menuModesWork;
            var drinkMenu = petMenu.Items.OfType<MenuItem>().Single(x => x.Header.ToString() == "喝水提醒");
            var enableDrink = drinkMenu.Items.OfType<MenuItem>().First();
            enableDrink.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            checks["drinkMenuToggleApplies"] = drinkReminderEnabled && enableDrink.IsChecked && nextDrinkReminder == now + 1800;
            enableDrink.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            checks["drinkMenuToggleDisables"] = !drinkReminderEnabled && !enableDrink.IsChecked;
            Preferences("{}");
            foreach (int minutes in RandomMinutes)
            {
                cloudRandomMinutes = minutes;
                var samples = Enumerable.Range(0, 30).Select(_ => NextRandomQuotaDelay()).ToArray();
                checks[$"randomQuotaRange{minutes}"] = samples.All(x => x >= minutes * 60 && x <= RandomMaximum(minutes) * 60) &&
                    samples.Distinct().Count() > 1;
            }
            cloudRandomMinutes = 5;

            SetCloudMode("hover");
            ResetCloudHover();
            SetCloudPointer(true);
            now += 0.29;
            AdvanceCloudHover(now);
            checks["hoverWaitsBeforeReveal"] = !quotaCloud!.IsVisible;
            now += 0.02;
            AdvanceCloudHover(now);
            checks["hoverRevealsQuota"] = quotaCloud.IsVisible && activeNotice == Notice.None;
            SetCloudPointer(false);
            now += 0.2;
            AdvanceCloudHover(now);
            SetCloudPointer(true);
            checks["hoverBridgeKeepsCloudClickable"] = quotaCloud.IsVisible;
            SetCloudPointer(false);
            now += 0.49;
            AdvanceCloudHover(now);
            checks["hoverDepartureGrace"] = quotaCloud.IsVisible;
            now += 0.02;
            AdvanceCloudHover(now);
            checks["hoverHidesAfterDeparture"] = !quotaCloud.IsVisible;

            SetCloudMode("random");
            nextRandomQuota = now;
            AdvanceNotifications(now);
            checks["randomQuotaStartsWithoutSpeech"] = activeNotice == Notice.Quota && quotaCloud.IsVisible &&
                pet!.MsgBar.Visibility != Visibility.Visible && !ambientTimer.IsEnabled && !pet.MoveTimer.Enabled;
            checks["randomQuotaKeepsCurrentValue"] = quotaCloud.PrimaryText == "65%";
            checks["quotaUsesExistingDefaultAnimation"] = pet!.DisplayType.Type == GraphType.Default;
            checks["noExtraReminderResources"] = !graph!.GraphsList.ContainsKey("reminder_tap");
            now += 8.01;
            AdvanceNotifications(now);
            checks["randomQuotaExpires"] = activeNotice == Notice.None && !quotaCloud.IsVisible;

            SetCloudMode("hover_random");
            ResetCloudHover();
            nextRandomQuota = now;
            AdvanceNotifications(now);
            SetCloudPointer(true);
            now += 0.31;
            AdvanceCloudHover(now);
            now += 8;
            AdvanceNotifications(now);
            checks["hoverExtendsRandomQuota"] = activeNotice == Notice.Quota && quotaCloud.IsVisible;
            SetCloudPointer(false);
            now += 0.51;
            AdvanceCloudHover(now);
            AdvanceNotifications(now);
            checks["randomQuotaEndsAfterHoverLeaves"] = activeNotice == Notice.None && !quotaCloud.IsVisible;
            quotaMenuItem!.IsChecked = false;
            quotaMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            SetCloudPointer(true);
            now += 0.31;
            AdvanceCloudHover(now);
            nextRandomQuota = now;
            AdvanceNotifications(now);
            checks["manualHideOverridesHoverAndRandom"] = !quotaCloud.IsVisible && activeNotice == Notice.None;
            SetCloudMode("hover");
            checks["modeChangeClearsManualOverride"] = cloudManualChoice == null;
            Normal();
            SetCloudMode("edge");
            drinkReminderEnabled = restReminderEnabled = true;
            ResetNotificationSchedule(now);
            double originalDrinkDue = nextDrinkReminder, originalRestDue = nextRestReminder;
            double originalRandomDue = nextRandomQuota;
            // 打开主程序用量面板会重复发送 visible=true；这不是真正的隐藏/恢复，不能重置倒计时。
            for (int i = 0; i < 5; i++)
            {
                now += 300;
                VisibilityCommand(true, stopTimer: false);
            }
            checks["repeatedPanelOpensPreserveReminderDeadlines"] = nextDrinkReminder == originalDrinkDue &&
                nextRestReminder == originalRestDue && nextRandomQuota == originalRandomDue;
            now = originalDrinkDue;
            // 使用真实 DispatcherTimer 触发，避免手动调用 AdvanceNotifications 掩盖计时器生命周期问题。
            notificationTimer.Start();
            await Task.Delay(1300);
            checks["realTimerShowsDrinkAfterRepeatedPanelOpens"] = activeNotice == Notice.Drink &&
                pet.MsgBar.Visibility == Visibility.Visible;
            notificationTimer.Stop();
            FinishNotification();
            for (int i = 0; i < 5; i++)
            {
                now += 300;
                VisibilityCommand(true, stopTimer: false);
            }
            checks["repeatedPanelOpensStillPreserveRestDeadline"] = nextRestReminder == originalRestDue;
            now = originalRestDue;
            notificationTimer.Start();
            await Task.Delay(1300);
            checks["realTimerShowsRestAfterRepeatedPanelOpens"] = activeNotice.HasFlag(Notice.Rest) &&
                pet.MsgBar.Visibility == Visibility.Visible;
            notificationTimer.Stop();
            FinishNotification();
            drinkReminderEnabled = restReminderEnabled = false;
            nextDrinkReminder = nextRestReminder = now;
            AdvanceNotifications(now);
            checks["disabledRemindersStaySilent"] = activeNotice == Notice.None;
            drinkReminderEnabled = restReminderEnabled = true;
            petPointerDown = true;
            AdvanceNotifications(now);
            checks["remindersWaitForGesture"] = activeNotice == Notice.None;
            petPointerDown = false;
            pet.DisplayTouchHead();
            AdvanceNotifications(now);
            checks["remindersWaitForTouchAnimation"] = activeNotice == Notice.None;
            Normal();
            petMenu!.IsOpen = true;
            AdvanceNotifications(now);
            checks["remindersWaitForMenu"] = activeNotice == Notice.None;
            petMenu.IsOpen = false;
            await Task.Delay(350);
            Normal();
            ShowUsageWarning("额度不足，请留意用量。");
            AdvanceNotifications(now);
            checks["warningPrecedesReminders"] = activeNotice == Notice.None &&
                (warningSpeechPending || pet.MsgBar.Visibility == Visibility.Visible);
            await Task.Delay(1200);
            Normal();
            ShowUsageWarning("中断警告测试");
            pet.DisplayTouchHead();
            await Task.Delay(80);
            checks["interruptedWarningDoesNotBlockReminder"] = !warningSpeechPending;
            Normal();
            AdvanceNotifications(now);
            checks["simultaneousRemindersMerge"] = activeNotice == (Notice.Drink | Notice.Rest) &&
                !ambientTimer.IsEnabled && nextDrinkReminder == now + 1800 && nextRestReminder == now + 3600;
            await Task.Delay(4000);
            checks["reminderSpeechContainsBothMessages"] = pet.MsgBar is MessageBar message &&
                message.TText.Text.Contains(DrinkText) && message.TText.Text.Contains(RestText);
            checks["reminderDoesNotShowCharacterName"] = pet.MsgBar.This.FindName("LName") is FrameworkElement nameLabel &&
                nameLabel.Visibility == Visibility.Collapsed && nameLabel.ActualHeight == 0;
            Capture(this, Path.Combine(output, "reminder-both.png"));
            pet.MsgBar.ForceClose();
            AdvanceNotifications(now);
            checks["closingSpeechFinishesReminder"] = activeNotice == Notice.None;
            foreach (int testSize in new[] { 160, 220, 320 })
            {
                ResizePet(testSize - size);
                StartNotification(Notice.Drink | Notice.Rest, now);
                var readableMessage = (MessageBar)pet.MsgBar;
                // 用最长的合并提示检查实际布局，不让逐字显示暂时缩短内容而掩盖换行或裁切问题。
                readableMessage.ShowTimer.Stop();
                readableMessage.TText.Text = DrinkText + "\n" + RestText;
                UpdateLayout();
                var textTransform = readableMessage.TText.TransformToAncestor(this);
                double textScale = Math.Abs(textTransform.Transform(new Point(0, 1)).Y -
                    textTransform.Transform(new Point(0, 0)).Y);
                var textBounds = textTransform.TransformBounds(new Rect(readableMessage.TText.RenderSize));
                checks[$"reminderTextReadable{testSize}"] = readableMessage.TText.FontSize * textScale >= 13.9;
                checks[$"reminderTextFitsWithoutResizingPet{testSize}"] = Width == testSize && Height == testSize &&
                    new Rect(0, 0, ActualWidth, ActualHeight).Contains(textBounds);
                Capture(this, Path.Combine(output, $"reminder-readable-{testSize}.png"));
                FinishNotification();
            }
            ResizePet(originalSize - size);
            nextDrinkReminder = now;
            AdvanceNotifications(now);
            checks["drinkDeadlineIsIndependent"] = activeNotice == Notice.Drink;
            checks["drinkUsesExistingDefaultAnimation"] = pet.DisplayType.Type == GraphType.Default;
            pet.MsgBar.ForceClose();
            AdvanceNotifications(now);
            nextRestReminder = now;
            AdvanceNotifications(now);
            checks["restDeadlineIsIndependent"] = activeNotice == Notice.Rest;
            checks["restUsesExistingDefaultAnimation"] = pet.DisplayType.Type == GraphType.Default;
            // 关闭、再次显示、再启动淡出计时器，直接覆盖过去 Close() 后不可复用的故障。
            pet.MsgBar.ForceClose();
            AdvanceNotifications(now);
            pet.MsgBar.Show(save.Name, "再次提醒");
            if (pet.MsgBar is MessageBar reusable)
            {
                reusable.CloseTimer.Start();
                reusable.CloseTimer.Stop();
                checks["speechTimersReusableAfterClose"] = reusable.Visibility == Visibility.Visible;
            }
            Normal();

            foreach (bool left in new[] { true, false })
            {
                Normal();
                TrySnapPetToEdge(left);
                await Task.Delay(200);
                var dockedPosition = new Point(Left, Top);
                nextDrinkReminder = now;
                nextRestReminder = now + 3600;
                AdvanceNotifications(now);
                checks[$"dockedReminderWaitsForManualUndock{left}"] = activeNotice == Notice.None &&
                    manualDockedEdge == left && DockedEdge == left && new Point(Left, Top) == dockedPosition;
                SaveState();
                using (var layout = JsonDocument.Parse(File.ReadAllText(Path.Combine(dataDirectory, "layout.json"))))
                    checks[$"dockedStateSaved{left}"] = layout.RootElement.GetProperty("dockedEdge").GetBoolean() == left;
                // 自检直接模拟用户拖离后的状态；真实拖动路径由 RunDragChecks 覆盖。
                manualDockedEdge = null;
                pet.CleanState();
                pet.DisplayToNomal();
                AdvanceNotifications(now);
                checks[$"pendingReminderStartsAfterManualUndock{left}"] = activeNotice == Notice.Drink;
                FinishNotification();
            }
            StartNotification(Notice.Rest, now);
            int previousGeneration = notificationGeneration;
            ResizePet(20);
            var resizedPosition = new Point(Left, Top);
            await Task.Delay(500);
            checks["resizeCancelsReminderReturn"] = activeNotice == Notice.None && notificationOrigin == null &&
                DockedEdge == null && new Point(Left, Top) == resizedPosition && notificationGeneration > previousGeneration;
            TrySnapPetToEdge(true);
            StartNotification(Notice.Rest, now);
            GetWindowRect(handle, out var beforeDrag);
            var start = new Point(beforeDrag.Left + 70, beforeDrag.Top + 70);
            StartPetGesture(start, new Point(250, 100));
            UpdatePetGesture(start + new Vector(100, 20));
            EndPetGesture(cancel: true);
            var draggedPosition = new Point(Left, Top);
            await Task.Delay(650);
            checks["dragCancelsReminderReturn"] = activeNotice == Notice.None && notificationOrigin == null &&
                new Point(Left, Top) == draggedPosition && DockedEdge == null;

            Normal();
            StartNotification(Notice.Drink, now);
            previousGeneration = notificationGeneration;
            VisibilityCommand(false);
            checks["hideCancelsReminderAndCallbacks"] = activeNotice == Notice.None && !notificationTimer.IsEnabled &&
                !cloudHoverTimer.IsEnabled && !IsVisible && pet.MsgBar.Visibility != Visibility.Visible && notificationGeneration > previousGeneration;
            now += 7200;
            VisibilityCommand(true);
            checks["showRestartsDeadlinesWithoutBacklog"] = nextDrinkReminder == now + 1800 &&
                nextRestReminder == now + 3600 && nextRandomQuota >= now + 300 && activeNotice == Notice.None;
            StartNotification(Notice.Rest, now);
            OnPowerModeChanged(this, new PowerModeChangedEventArgs(PowerModes.Suspend));
            await Task.Delay(80);
            checks["suspendCancelsReminder"] = notificationsSuspended && activeNotice == Notice.None && !notificationTimer.IsEnabled;
            now += 7200;
            OnPowerModeChanged(this, new PowerModeChangedEventArgs(PowerModes.Resume));
            await Task.Delay(80);
            notificationTimer.Stop();
            checks["resumeRestartsDeadlines"] = !notificationsSuspended && nextDrinkReminder == now + 1800 && nextRestReminder == now + 3600;
            StartNotification(Notice.Rest, now);
            previousGeneration = notificationGeneration;
            closing = true;
            PauseNotifications();
            checks["shutdownCancelsNotificationWork"] = activeNotice == Notice.None && !notificationTimer.IsEnabled &&
                !cloudHoverTimer.IsEnabled && notificationGeneration > previousGeneration;
            closing = false;
        }
        finally
        {
            closing = false;
            notificationsSuspended = false;
            EndPetGesture(cancel: true);
            FinishNotification(restorePosition: false);
            pet!.MsgBar.ForceClose();
            Preferences(originalPreferences);
            notificationNow = originalClock;
            size = originalSize;
            Width = Height = size;
            Left = originalPosition.X;
            Top = originalPosition.Y;
            allowMove = originalMove;
            cloudManualChoice = null;
            cloudDockedState = null;
            manualDockedEdge = originalManualDock;
            ResetCloudHover();
            pet.DisplayToNomal();
            ClampPosition();
            RefreshNotificationMenus();
            ResumeNotifications();
            SyncAutonomy();
            saveTimer.Start();
            UpdateQuotaCloud();
        }
    }
}
