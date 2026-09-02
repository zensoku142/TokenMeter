using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using VPet_Simulator.Core;
using static VPet_Simulator.Core.GraphInfo;
using Mode = VPet_Simulator.Core.IGameSave.ModeType;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private readonly bool actionPanelEnabled;
    private Window? actionPanelWindow;
    private TextBlock? actionPanelStatus;
    private string? actionPanelNote;
    private DispatcherTimer? actionPanelStatusTimer;
    private List<IGraph>? actionPanelSequence;
    private IGraph? actionPanelFrame;
    private int actionPanelGeneration;

    private sealed record ActionPanelEntry(
        string Group, string Label, GraphType Type, string Name, bool StateSequence = false);

    private List<ActionPanelEntry> ActionPanelEntries()
    {
        var entries = new List<ActionPanelEntry>();

        void Add(string group, GraphType type, string? prefix = null)
        {
            foreach (string name in graph!.GraphsALL.Where(item => item.GraphInfo.Type == type)
                         .Select(item => item.GraphInfo.Name).Distinct(StringComparer.OrdinalIgnoreCase)
                         .OrderBy(name => name, StringComparer.OrdinalIgnoreCase))
                entries.Add(new ActionPanelEntry(group, $"{prefix ?? type.ToString()} · {name}", type, name));
        }

        Add("基础", GraphType.Default);
        Add("基础", GraphType.StartUP);
        Add("移动", GraphType.Move);
        Add("提起与触摸", GraphType.Raised_Static);
        Add("提起与触摸", GraphType.Raised_Dynamic);
        Add("提起与触摸", GraphType.Touch_Head);
        Add("提起与触摸", GraphType.Touch_Body);
        Add("说话", GraphType.Say);
        Add("贴边", GraphType.SideHide_Left_Main);
        Add("贴边", GraphType.SideHide_Left_Rise);
        Add("贴边", GraphType.SideHide_Right_Main);
        Add("贴边", GraphType.SideHide_Right_Rise);

        foreach (string name in graph!.GraphsALL.Where(item => item.GraphInfo.Type == GraphType.StateONE)
                     .Select(item => item.GraphInfo.Name).Distinct(StringComparer.OrdinalIgnoreCase)
                     .OrderBy(name => name, StringComparer.OrdinalIgnoreCase))
            entries.Add(new ActionPanelEntry("状态", $"坐下 · {name}", GraphType.StateONE, name));
        foreach (string name in graph.GraphsALL.Where(item => item.GraphInfo.Type == GraphType.StateTWO)
                     .Select(item => item.GraphInfo.Name).Distinct(StringComparer.OrdinalIgnoreCase)
                     .OrderBy(name => name, StringComparer.OrdinalIgnoreCase))
            entries.Add(new ActionPanelEntry("状态", $"躺下并起身 · {name}", GraphType.StateTWO, name, true));

        Add("待机", GraphType.Idel, "IDEL");
        return entries;
    }

    private void ShowActionPanel()
    {
        if (!actionPanelEnabled || graph == null || pet == null || closing) return;
        if (actionPanelWindow != null)
        {
            actionPanelWindow.Activate();
            return;
        }

        var entries = ActionPanelEntries();
        var root = new DockPanel { Margin = new Thickness(12) };
        var heading = new StackPanel { Margin = new Thickness(0, 0, 0, 10) };
        heading.Children.Add(new TextBlock {
            Text = $"GraphCore 已加载 {graph.GraphsALL.Count} 个动画段；面板提供 {entries.Count} 个高层动作入口。" +
                "按钮按当前模式选择可用片段，只播放有限序列且不触发窗口位移。",
            TextWrapping = TextWrapping.Wrap
        });
        actionPanelStatus = new TextBlock { Margin = new Thickness(0, 8, 0, 8), TextWrapping = TextWrapping.Wrap };
        actionPanelNote = null;
        heading.Children.Add(actionPanelStatus);
        var stop = new Button { Content = "停止并恢复默认", Padding = new Thickness(12, 6, 12, 6), HorizontalAlignment = HorizontalAlignment.Left };
        stop.Click += (_, _) => StopActionPanelPlayback();
        heading.Children.Add(stop);
        DockPanel.SetDock(heading, Dock.Top);
        root.Children.Add(heading);

        var groups = new StackPanel();
        foreach (var group in entries.GroupBy(entry => entry.Group))
        {
            var buttons = new WrapPanel();
            foreach (var entry in group)
            {
                var button = new Button {
                    Content = entry.Label,
                    Margin = new Thickness(3),
                    Padding = new Thickness(9, 5, 9, 5),
                    ToolTip = $"{entry.Type}/{entry.Name}"
                };
                button.Click += (_, _) => StartActionPanelEntry(entry);
                buttons.Children.Add(button);
            }
            groups.Children.Add(new GroupBox {
                Header = $"{group.Key}（{group.Count()}）",
                Content = buttons,
                Margin = new Thickness(0, 0, 0, 8),
                Padding = new Thickness(5)
            });
        }
        root.Children.Add(new ScrollViewer {
            Content = groups,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        });

        var window = new Window {
            Title = "TokenMeter.Pet · 动作验证面板",
            Width = 720,
            Height = 760,
            MinWidth = 480,
            MinHeight = 420,
            WindowStartupLocation = WindowStartupLocation.CenterScreen,
            Content = root,
            ShowInTaskbar = true
        };
        window.Closed += (_, _) => {
            actionPanelStatusTimer?.Stop();
            actionPanelStatusTimer = null;
            actionPanelStatus = null;
            actionPanelNote = null;
            if (ReferenceEquals(actionPanelWindow, window)) actionPanelWindow = null;
            StopActionPanelPlayback();
            // 独立验证模式没有父进程管道；关闭唯一的开发者入口时同步结束桌宠，避免留下后台窗口。
            if (!closing) Close();
        };
        actionPanelWindow = window;
        actionPanelStatusTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(200) };
        actionPanelStatusTimer.Tick += (_, _) => UpdateActionPanelStatus();
        actionPanelStatusTimer.Start();
        UpdateActionPanelStatus();
        window.Show();
        // 人工验证期间暂停随机待机和移动，避免未选择的动作污染观察结果。
        SyncAutonomy();
    }

    private void CloseActionPanel()
    {
        actionPanelStatusTimer?.Stop();
        actionPanelStatusTimer = null;
        actionPanelWindow?.Close();
    }

    private void UpdateActionPanelStatus(string? note = null)
    {
        if (actionPanelStatus == null || pet == null) return;
        if (note != null) actionPanelNote = note;
        string current = $"模式：{save.Mode}　状态：{pet.State}　当前：{pet.DisplayType.Type}/{pet.DisplayType.Name}/{pet.DisplayType.Animat}";
        actionPanelStatus.Text = string.IsNullOrWhiteSpace(actionPanelNote) ? current : $"{actionPanelNote}\n{current}";
    }

    private IGraph? ActionPanelPart(GraphType type, string name, AnimatType part, Mode mode)
    {
        var choices = graph!.GraphsALL.Where(item => item.GraphInfo.Type == type &&
            StringComparer.OrdinalIgnoreCase.Equals(item.GraphInfo.Name, name) &&
            item.GraphInfo.Animat == part && item.IsReady && !item.IsFail).ToArray();
        if (choices.Length == 0) return null;
        var matching = choices.Where(item => item.GraphInfo.ModeType == mode).ToArray();
        if (matching.Length == 0) matching = choices.Where(item => item.GraphInfo.ModeType == Mode.Nomal).ToArray();
        return (matching.Length == 0 ? choices : matching)[0];
    }

    private List<IGraph> BuildActionPanelSequence(ActionPanelEntry entry, Mode mode)
    {
        var sequence = new List<IGraph>();
        void Add(GraphType type, AnimatType part, int count = 1)
        {
            if (ActionPanelPart(type, entry.Name, part, mode) is not { } frame) return;
            for (int i = 0; i < count; i++) sequence.Add(frame);
        }

        if (entry.StateSequence)
        {
            // StateTWO 不是独立站姿：必须先坐下，再躺下，最后经坐姿回到站立。
            Add(GraphType.StateONE, AnimatType.A_Start);
            Add(GraphType.StateONE, AnimatType.B_Loop);
            Add(GraphType.StateTWO, AnimatType.A_Start);
            Add(GraphType.StateTWO, AnimatType.B_Loop);
            Add(GraphType.StateTWO, AnimatType.C_End);
            Add(GraphType.StateONE, AnimatType.B_Loop);
            Add(GraphType.StateONE, AnimatType.C_End);
            return sequence;
        }

        if (ActionPanelPart(entry.Type, entry.Name, AnimatType.Single, mode) is { } single)
        {
            sequence.Add(single);
            return sequence;
        }

        Add(entry.Type, AnimatType.A_Start);
        // 部分旧资源只有循环段；有限播放两次既能看清动作，也不会让验证面板卡在无限循环。
        Add(entry.Type, AnimatType.B_Loop, 2);
        Add(entry.Type, AnimatType.C_End);
        return sequence;
    }

    private void StartActionPanelEntry(ActionPanelEntry entry)
    {
        if (!actionPanelEnabled || !ready || closing || pet == null) return;
        var sequence = BuildActionPanelSequence(entry, save.Mode);
        if (sequence.Count == 0)
        {
            UpdateActionPanelStatus($"无法播放：{entry.Label} 没有可用片段。");
            return;
        }

        StopActionPanelPlayback(returnToDefault: false);
        CancelAutonomousSequence(returnToNormal: false);
        FinishNotification();
        pet.MsgBar.ForceClose();
        warningSpeechPending = false;
        ++notificationGeneration;
        actionPanelSequence = sequence;
        int generation = ++actionPanelGeneration;
        pet.CleanState();
        SyncAutonomy();
        UpdateActionPanelStatus($"播放：{entry.Label}（{sequence.Count} 段）");
        PlayActionPanelFrame(generation, 0);
    }

    private void PlayActionPanelFrame(int generation, int index)
    {
        if (generation != actionPanelGeneration || actionPanelSequence == null || pet == null) return;
        if (index >= actionPanelSequence.Count)
        {
            actionPanelSequence = null;
            actionPanelFrame = null;
            pet.DisplayToNomal();
            SyncAutonomy();
            UpdateActionPanelStatus("播放完成，已恢复默认。");
            return;
        }

        actionPanelFrame = actionPanelSequence[index];
        pet.Display(actionPanelFrame, () => {
            // VPet 动画回调来自后台线程；批次检查防止停止后旧回调续播后续片段。
            if (!Dispatcher.HasShutdownStarted)
                Dispatcher.BeginInvoke(() => PlayActionPanelFrame(generation, index + 1));
        });
    }

    private void StopActionPanelPlayback(bool returnToDefault = true)
    {
        if (!actionPanelEnabled && actionPanelSequence == null && actionPanelFrame == null) return;
        ++actionPanelGeneration;
        var frame = actionPanelFrame;
        actionPanelSequence = null;
        actionPanelFrame = null;
        frame?.Stop(true);
        pet?.CleanState();
        if (returnToDefault && ready && visible && !closing && !notificationsSuspended) pet?.DisplayToNomal();
        SyncAutonomy();
        UpdateActionPanelStatus(returnToDefault ? "已停止并恢复默认。" : "已停止。");
    }

    private void CancelActionPanelIfInterrupted()
    {
        if (actionPanelSequence != null && actionPanelFrame != null && pet?.DisplayType != actionPanelFrame.GraphInfo)
            StopActionPanelPlayback(returnToDefault: false);
    }

    private void RunActionPanelChecks(Dictionary<string, bool> checks)
    {
        var entries = ActionPanelEntries();
        // VPet also exposes two legacy mode-folder fallbacks as Move names; keeping them visible
        // lets the validation panel cover every loaded route, not only the 14 configured movers.
        string[] moveNames = { "climb.left", "climb.right", "climb.top.left", "climb.top.right", "crawl.left",
            "crawl.right", "fall.left", "fall.right", "happy", "poorcondition", "walk.left",
            "walk.left.faster", "walk.left.slow", "walk.right", "walk.right.faster", "walk.right.slow" };
        string[] idleNames = { "amusement", "aside", "boring", "bubbles", "like520", "meow", "meowlook",
            "squat", "tennis", "yawning" };
        string[] sayNames = { "self", "serious", "shining", "shy" };

        bool Has(GraphType type) => entries.Any(entry => !entry.StateSequence && entry.Type == type);
        string[] Names(GraphType type) => entries.Where(entry => !entry.StateSequence && entry.Type == type)
            .Select(entry => entry.Name).OrderBy(name => name, StringComparer.OrdinalIgnoreCase).ToArray();
        checks["actionPanelCoversRequiredTypes"] = new[] { GraphType.Default, GraphType.StartUP, GraphType.Move,
            GraphType.Raised_Static, GraphType.Raised_Dynamic, GraphType.Touch_Head, GraphType.Touch_Body,
            GraphType.Say, GraphType.SideHide_Left_Main, GraphType.SideHide_Left_Rise,
            GraphType.SideHide_Right_Main, GraphType.SideHide_Right_Rise, GraphType.StateONE, GraphType.Idel }.All(Has) &&
            entries.Any(entry => entry.StateSequence && entry.Type == GraphType.StateTWO);
        string[] actualMoveNames = Names(GraphType.Move);
        checks["actionPanelCoversMoveNames"] = actualMoveNames
            .ToHashSet(StringComparer.OrdinalIgnoreCase).SetEquals(moveNames);
        checks["actionPanelCoversIdleNames"] = Names(GraphType.Idel)
            .ToHashSet(StringComparer.OrdinalIgnoreCase).SetEquals(idleNames);
        checks["actionPanelCoversSayNames"] = Names(GraphType.Say)
            .ToHashSet(StringComparer.OrdinalIgnoreCase).SetEquals(sayNames);

        var sequences = entries.Select(entry => (Entry: entry, Frames: BuildActionPanelSequence(entry, save.Mode))).ToArray();
        checks["actionPanelSequencesAreReadyAndBounded"] = sequences.All(item => item.Frames.Count is > 0 and <= 7 &&
            item.Frames.All(frame => frame.IsReady && !frame.IsFail));
        var expectedState = new[] {
            (GraphType.StateONE, AnimatType.A_Start), (GraphType.StateONE, AnimatType.B_Loop),
            (GraphType.StateTWO, AnimatType.A_Start), (GraphType.StateTWO, AnimatType.B_Loop),
            (GraphType.StateTWO, AnimatType.C_End), (GraphType.StateONE, AnimatType.B_Loop),
            (GraphType.StateONE, AnimatType.C_End)
        };
        var states = sequences.Where(item => item.Entry.StateSequence).Select(item => item.Frames
            .Select(frame => (frame.GraphInfo.Type, frame.GraphInfo.Animat)).ToArray()).ToArray();
        checks["actionPanelStateSequenceKeepsPostureOrder"] = states.Length > 0 &&
            states.All(state => state.SequenceEqual(expectedState));
    }
}
