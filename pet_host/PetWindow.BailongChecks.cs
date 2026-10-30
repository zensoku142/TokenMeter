using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using VPet_Simulator.Core;
using static VPet_Simulator.Core.GraphInfo;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private async Task RunBailongAutonomyChecks(Dictionary<string, bool> checks)
    {
        bool originalMove = allowMove;
        bool originalHitTest = IsHitTestVisible;
        // 自动验收期间不接收真实鼠标输入，避免用户经过测试窗口时中断代表性完整播放。
        IsHitTestVisible = false;
        var originalPosition = new Point(Left, Top);
        var work = WorkArea();
        notificationTimer.Stop();
        saveTimer.Stop();
        allowMove = true;
        manualDockedEdge = null;
        pet!.MsgBar.ForceClose();
        warningSpeechPending = false;
        Left = work.Left + (work.Width - Width) / 2;
        Top = work.Top + (work.Height - Height) / 2;
        pet.DisplayToNomal();
        var choices = AutonomousChoices();
        void Prepare((GraphType Type, string Name, IGameSave.ModeType Mode) choice)
        {
            CancelAutonomousSequence();
            pet.DisplayToNomal();
            remainingAutonomousChoices.Clear();
            remainingAutonomousChoices.Add(choice);
        }
        try
        {
            checks["autonomousPoolHasAll35UniqueActions"] = choices.Count == 35 &&
                choices.Select(choice => choice.Name).Distinct().Count() == 35 &&
                choices.All(choice => choice.Type == GraphType.Common);
            remainingAutonomousChoices.Clear();
            var round = Enumerable.Range(0, choices.Count).Select(_ => NextAutonomousChoice()!.Value).ToArray();
            checks["autonomousRoundCoversAll35WithoutRepeats"] = round.Distinct().Count() == 35 &&
                choices.All(choice => round.Contains(choice));
            checks["autonomousRoundRefills"] = NextAutonomousChoice() != null && remainingAutonomousChoices.Count == 34;
            foreach (var choice in choices)
            {
                Prepare(choice);
                RunAutonomousBehavior();
                await Task.Delay(35);
                checks["autonomousStarts_" + choice.Name] = autonomousSequence != null &&
                    pet.DisplayType.Name == choice.Name && autonomousFrame?.IsReady == true;
            }
            CancelAutonomousSequence();

            foreach (var corner in new[] { new Point(work.Left, work.Top), new Point(work.Right - Width, work.Top),
                new Point(work.Left, work.Bottom - Height), new Point(work.Right - Width, work.Bottom - Height) })
            {
                foreach (string id in bailongMovements.Keys)
                {
                    Prepare(choices.Single(choice => choice.Name == "bailong.action." + id));
                    Left = corner.X;
                    Top = corner.Y;
                    RunAutonomousBehavior();
                    var origin = bailongMoveOrigin;
                    var target = bailongMoveTarget;
                    AdvanceBailongMovement(bailongMoveStartMs - 1);
                    bool holdsDuringLead = Left == origin.X && Top == origin.Y;
                    AdvanceBailongMovement((bailongMoveStartMs + bailongMoveStopMs) / 2);
                    bool moves = (new Point(Left, Top) - origin).Length > 0.1;
                    bool diagonal = !id.StartsWith("move03") || Math.Abs(Top - origin.Y) > 0.1;
                    AdvanceBailongMovement(bailongMoveStopMs);
                    bool inside = Left >= work.Left - 0.1 && Top >= work.Top - 0.1 &&
                        Left + Width <= work.Right + 0.1 && Top + Height <= work.Bottom + 0.1;
                    checks[$"movement_{id}_{corner.X}_{corner.Y}"] = holdsDuringLead && moves && diagonal && inside &&
                        !bailongMoveTimer.IsEnabled && Math.Abs(Left - target.X) < 0.1 && Math.Abs(Top - target.Y) < 0.1;
                    if (!checks[$"movement_{id}_{corner.X}_{corner.Y}"])
                        Console.Error.WriteLine($"Bailong movement {id} at {corner}: lead={holdsDuringLead}, moves={moves}, " +
                            $"diagonal={diagonal}, inside={inside}, timer={bailongMoveTimer.IsEnabled}, " +
                            $"origin={origin}, target={target}, actual={Left},{Top}, display={pet.DisplayType.Name}");
                    CancelAutonomousSequence();
                    checks[$"movementCancel_{id}"] = pet.PetGrid.RenderTransform == Transform.Identity;
                }
            }
            Left = work.Left + (work.Width - Width) / 2;
            Top = work.Top + (work.Height - Height) / 2;
            foreach (string id in new[] { "work02", "drag", "move01right", "move03left" })
            {
                var choice = choices.Single(choice => choice.Name == "bailong.action." + id);
                Prepare(choice);
                var expected = BuildAutonomousSequence(choice).Select(frame => frame.GraphInfo.Name).ToArray();
                var played = new List<string>();
                void Record(GraphInfo info)
                {
                    if (info.Name.StartsWith("bailong.action.")) lock (played) played.Add(info.Name);
                }
                pet.GraphDisplayHandler += Record;
                try
                {
                    RunAutonomousBehavior();
                    var clock = Stopwatch.StartNew();
                    while (autonomousSequence != null && clock.Elapsed.TotalSeconds < 20) await Task.Delay(50);
                    lock (played) checks["autonomousCompletes_" + id] = autonomousSequence == null &&
                        pet.DisplayType.Type == GraphType.Default && played.SequenceEqual(expected) && !bailongMoveTimer.IsEnabled;
                    if (!checks["autonomousCompletes_" + id])
                        Console.Error.WriteLine($"Bailong completion {id}: active={autonomousSequence != null}, " +
                            $"display={pet.DisplayType.Name}, timer={bailongMoveTimer.IsEnabled}, " +
                            $"elapsed={clock.Elapsed.TotalSeconds:0.00}, played={string.Join(',', played)}, " +
                            $"expected={string.Join(',', expected)}");
                }
                finally { pet.GraphDisplayHandler -= Record; CancelAutonomousSequence(); }
            }

            var moving = choices.Single(choice => choice.Name == "bailong.action.move02");
            Prepare(moving);
            RunAutonomousBehavior();
            int previous = autonomousGeneration;
            StartNotification(Notice.Drink, notificationNow());
            PlayAutonomousFrame(previous, 1);
            checks["reminderCancelsAutonomousMovement"] = autonomousSequence == null && !bailongMoveTimer.IsEnabled;
            FinishNotification();
            pet.MsgBar.ForceClose();

            Prepare(moving);
            RunAutonomousBehavior();
            autonomyMenuItem!.IsChecked = false;
            autonomyMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));
            RunAutonomousBehavior();
            checks["autonomyOffStopsAllActionsAndMovement"] = autonomousSequence == null &&
                !bailongMoveTimer.IsEnabled && !ambientTimer.IsEnabled;
            autonomyMenuItem.IsChecked = true;
            autonomyMenuItem.RaiseEvent(new RoutedEventArgs(MenuItem.ClickEvent));

            Prepare(moving);
            RunAutonomousBehavior();
            petMenu!.IsOpen = true;
            checks["menuStopsAutonomousMovement"] = autonomousSequence == null && !bailongMoveTimer.IsEnabled;
            petMenu.IsOpen = false;
            await Task.Delay(300);

            Prepare(moving);
            RunAutonomousBehavior();
            using (var command = JsonDocument.Parse("{\"type\":\"visibility\",\"visible\":false}")) Receive(command.RootElement);
            checks["hideStopsAutonomousMovement"] = autonomousSequence == null && !bailongMoveTimer.IsEnabled;
            using (var command = JsonDocument.Parse("{\"type\":\"visibility\",\"visible\":true}")) Receive(command.RootElement);
        }
        finally
        {
            CancelAutonomousSequence();
            remainingAutonomousChoices.Clear();
            allowMove = originalMove;
            IsHitTestVisible = originalHitTest;
            Left = originalPosition.X;
            Top = originalPosition.Y;
            pet.DisplayToNomal();
            SyncAutonomy();
            saveTimer.Start();
        }
    }
}
