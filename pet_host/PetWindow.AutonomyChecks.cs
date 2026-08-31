using System;
using System.Collections.Generic;
using System.Diagnostics;
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
    private async Task RunAutonomyChecks(Dictionary<string, bool> checks, string output)
    {
        bool originalMove = allowMove;
        int originalSize = size;
        var originalPosition = new Point(Left, Top);
        var originalMode = save.Mode;
        var choices = AutonomousChoices();
        notificationTimer.Stop();
        saveTimer.Stop();
        allowMove = true;
        pet!.MsgBar.ForceClose();
        warningSpeechPending = false;
        ++notificationGeneration;
        SyncAutonomy();
        void VisibilityCommand(bool value)
        {
            using var command = JsonDocument.Parse(JsonSerializer.Serialize(new { type = "visibility", visible = value }));
            Receive(command.RootElement);
            notificationTimer.Stop();
        }
        try
        {
            checks["allIdleResourceFamiliesIncluded"] = new[] { "amusement_B", "aside", "Boring", "Bubbles",
                "happy_like520", "Meow", "meowlook", "Squat", "Tennis", "yawning" }.All(name =>
                    Directory.Exists(Path.Combine(resources, "pet/vup/IDEL", name)));
            checks["allIdleFamiliesHaveAutonomousEntries"] = choices.Where(x => x.Type == GraphType.Idel)
                .Select(x => x.Name).Distinct().Count() == 10;
            checks["allIdleMoodsAvailableWithoutGrowth"] = choices.Select(x => x.Mode).Distinct().Count() == 3 &&
                choices.Any(x => x.Type == GraphType.StateONE) && !EnableFunction && !pet.EventTimer.Enabled;
            remainingAutonomousChoices.Clear();
            var round = Enumerable.Range(0, choices.Count).Select(_ => NextAutonomousChoice()!.Value).ToArray();
            checks["autonomyRoundCoversEveryActionAndMood"] = round.Distinct().Count() == choices.Count &&
                choices.All(x => round.Contains(x)) && remainingAutonomousChoices.Count == 0;
            checks["autonomyRefillsAfterFullRound"] = NextAutonomousChoice() != null &&
                remainingAutonomousChoices.Count == choices.Count - 1;

            var work = WorkArea();
            pet.DisplayToNomal();
            Left = work.Left + (work.Width - Width) / 2;
            Top = work.Top + (work.Height - Height) / 2;
            foreach (var choice in choices)
            {
                var sequence = BuildAutonomousSequence(choice);
                bool valid = sequence.Count > 0 && sequence.All(x => x.IsReady && !x.IsFail && !x.IsLoop &&
                    x.GraphInfo.Name == choice.Name && (choice.Type == GraphType.Idel ? x.GraphInfo.Type == GraphType.Idel :
                        x.GraphInfo.Type is GraphType.StateONE or GraphType.StateTWO));
                StartAutonomousSequence(sequence);
                await Task.Delay(30);
                checks[$"autonomyStarts{choice.Name}{choice.Mode}"] = valid && autonomousSequence != null &&
                    pet.DisplayType.Name == choice.Name && save.Mode == originalMode;
                if (choice.Type == GraphType.StateONE)
                {
                    var stages = sequence.Select(x => (x.GraphInfo.Type, x.GraphInfo.Animat)).ToArray();
                    checks[$"stateSequenceKeepsPostureOrder{choice.Mode}"] = stages.SequenceEqual(new[] {
                        (GraphType.StateONE, AnimatType.A_Start), (GraphType.StateONE, AnimatType.B_Loop),
                        (GraphType.StateTWO, AnimatType.A_Start), (GraphType.StateTWO, AnimatType.B_Loop),
                        (GraphType.StateTWO, AnimatType.C_End), (GraphType.StateONE, AnimatType.B_Loop),
                        (GraphType.StateONE, AnimatType.C_End)
                    });
                }
                CancelAutonomousSequence();
            }

            // 验证真实完整播放及回到常规姿态，不能只验证起始帧或模拟结束回调。
            foreach (var choice in new[] { choices.First(x => x.Name == "amusement"),
                choices.First(x => x.Type == GraphType.StateONE && x.Mode == IGameSave.ModeType.Happy) })
            {
                var sequence = BuildAutonomousSequence(choice);
                var played = new List<GraphInfo>();
                void Record(GraphInfo info)
                {
                    if (info.Name == choice.Name) lock (played) played.Add(info);
                }
                pet.GraphDisplayHandler += Record;
                try
                {
                    StartAutonomousSequence(sequence);
                    var deadline = Stopwatch.StartNew();
                    while (autonomousSequence != null && deadline.Elapsed.TotalSeconds < 20) await Task.Delay(50);
                    lock (played) checks[$"autonomyCompletes{choice.Name}"] = autonomousSequence == null &&
                        played.SequenceEqual(sequence.Select(x => x.GraphInfo)) && pet.DisplayType.Type == GraphType.Default;
                    if (!checks[$"autonomyCompletes{choice.Name}"])
                        Console.Error.WriteLine($"Autonomy completion {choice.Name}: active={autonomousSequence != null}, " +
                            $"display={pet.DisplayType.Type}, elapsed={deadline.Elapsed.TotalSeconds:F1}, " +
                            $"played={string.Join(',', played.Select(x => x.Type.ToString() + '/' + x.Animat))}, " +
                            $"expected={string.Join(',', sequence.Select(x => x.GraphInfo.Type.ToString() + '/' + x.GraphInfo.Animat))}");
                }
                finally { pet.GraphDisplayHandler -= Record; CancelAutonomousSequence(); }
            }
            foreach (string name in new[] { "bubbles", "meow", "tennis", "aside" })
            {
                var choice = choices.First(x => x.Name == name);
                var sequence = BuildAutonomousSequence(choice);
                StartAutonomousSequence(sequence);
                await Task.Delay(1800);
                Capture(this, Path.Combine(output, $"autonomy-{name}.png"));
                CancelAutonomousSequence();
            }

            var sample = BuildAutonomousSequence(choices.First(x => x.Name == "amusement"));
            StartAutonomousSequence(sample);
            int previous = autonomousGeneration;
            autonomyMenuItem!.IsChecked = false;
            autonomyMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            PlayAutonomousFrame(previous, 1);
            RunAutonomousBehavior(1);
            checks["autonomyOffCancelsCurrentAndFutureActions"] = autonomousSequence == null &&
                !ambientTimer.IsEnabled && pet.DisplayType.Type == GraphType.Default;
            autonomyMenuItem.IsChecked = true;
            autonomyMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            StartAutonomousSequence(sample);
            petMenu!.IsOpen = true;
            checks["autonomyMenuCancelsOldSequence"] = autonomousSequence == null && !ambientTimer.IsEnabled;
            petMenu.IsOpen = false;
            await Task.Delay(300);
            StartAutonomousSequence(sample);
            previous = autonomousGeneration;
            var handle = new WindowInteropHelper(this).Handle;
            GetWindowRect(handle, out var rect);
            bool captured = StartPetGesture(new Point(rect.Left + 60, rect.Top + 60), new Point(250, 100));
            PlayAutonomousFrame(previous, 1);
            checks["autonomyGestureCancelsPendingFrames"] = captured && autonomousSequence == null && petPointerDown;
            EndPetGesture(cancel: true);
            StartAutonomousSequence(sample);
            pet.DisplayTouchHead();
            await Task.Delay(80);
            checks["autonomyCoreInteractionCancelsOldSequence"] = autonomousSequence == null &&
                pet.DisplayType.Type == GraphType.Touch_Head;

            pet.DisplayToNomal();
            StartAutonomousSequence(sample);
            previous = autonomousGeneration;
            StartNotification(Notice.Drink, notificationNow());
            PlayAutonomousFrame(previous, 1);
            RunAutonomousBehavior(1);
            checks["remindersPreemptAutonomyWithoutTap"] = autonomousSequence == null && activeNotice == Notice.Drink &&
                pet.DisplayType.Type == GraphType.Default && pet.MsgBar.Visibility == Visibility.Visible;
            FinishNotification();
            StartAutonomousSequence(sample);
            ShowUsageWarning("用量提示优先于自主活动。");
            checks["warningsPreemptAutonomy"] = autonomousSequence == null;
            await Task.Delay(1500);
            pet.MsgBar.ForceClose();
            warningSpeechPending = false;
            ++notificationGeneration;
            pet.DisplayToNomal();
            StartAutonomousSequence(sample);
            previous = autonomousGeneration;
            VisibilityCommand(false);
            PlayAutonomousFrame(previous, 1);
            checks["hiddenAutonomyDoesNotResumeFromOldCallback"] = autonomousSequence == null && !IsVisible;
            VisibilityCommand(true);

            foreach (bool left in new[] { true, false })
            {
                TrySnapPetToEdge(left);
                RunAutonomousBehavior(1);
                checks[$"autonomyCanLeaveDock{left}"] = autonomousSequence != null && DockedEdge == null &&
                    WorkArea().Contains(new Rect(Left, Top, Width, Height));
                CancelAutonomousSequence();
            }
            foreach (int testSize in new[] { 160, 220, 320 })
            foreach (bool left in new[] { true, false })
            {
                ResizePet(testSize - size);
                pet.DisplayToNomal();
                SyncAutonomy();
                GetWindowRect(handle, out var bounds);
                var screen = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
                int gap = (int)Math.Round((bounds.Right - bounds.Left) / 500.0 * 30);
                int x = left ? screen.Left + gap : screen.Right - (bounds.Right - bounds.Left) - gap;
                int y = screen.Top + (screen.Height - (bounds.Bottom - bounds.Top)) / 2;
                SetWindowPos(handle, IntPtr.Zero, x, y, 0, 0, DragPositionFlags);
                bool notSnapped = !TrySnapPetToEdge();
                RunAutonomousBehavior(0);
                var deadline = Stopwatch.StartNew();
                while (!pet.MoveTimer.Enabled && deadline.Elapsed.TotalSeconds < 3) await Task.Delay(50);
                await Task.Delay(200);
                GetWindowRect(handle, out var climbed);
                checks[$"nearWallClimbsInsteadOfSnapping{left}{testSize}"] = notSnapped && pet.MoveTimer.Enabled &&
                    pet.DisplayType.Type == GraphType.Move && pet.DisplayType.Name == (left ? "climb.left" : "climb.right") &&
                    !DockedEdge.HasValue && climbed.Top != y;
                if (testSize == 220) Capture(this, Path.Combine(output, $"autonomy-climb-{left}.png"));
                pet.CleanState();
                pet.SetMoveMode(false, false, 1200000);
                pet.DisplayToNomal();
                ClampPosition();
                await Task.Delay(150);
            }
            checks["autonomyDoesNotChangeGrowthOrMood"] = save.Mode == originalMode && !pet.EventTimer.Enabled &&
                graph!.FindName(GraphType.Sleep) == null && graph.FindName(GraphType.Work) == null;
            File.WriteAllText(Path.Combine(output, "autonomy-catalog.json"), JsonSerializer.Serialize(choices.Select(x => new {
                type = x.Type.ToString(), name = x.Name, mode = x.Mode.ToString(),
                stages = BuildAutonomousSequence(x).Select(frame => frame.GraphInfo.Animat.ToString()).ToArray()
            }), new JsonSerializerOptions { WriteIndented = true }));
        }
        finally
        {
            CancelAutonomousSequence();
            FinishNotification(restorePosition: false);
            pet.MsgBar.ForceClose();
            warningSpeechPending = false;
            ++notificationGeneration;
            remainingAutonomousChoices.Clear();
            allowMove = originalMove;
            size = originalSize;
            Width = Height = size;
            pet.DisplayToNomal();
            Left = originalPosition.X;
            Top = originalPosition.Y;
            ClampPosition();
            SyncAutonomy();
            ResumeNotifications();
            saveTimer.Start();
            UpdateQuotaCloud();
        }
    }
}
