using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using VPet_Simulator.Core;
using static VPet_Simulator.Core.GraphInfo;
using Mode = VPet_Simulator.Core.IGameSave.ModeType;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private readonly List<(GraphType Type, string Name, Mode Mode)> remainingAutonomousChoices = new();
    private List<IGraph>? autonomousSequence;
    private IGraph? autonomousFrame;
    private int autonomousGeneration;

    private List<(GraphType Type, string Name, Mode Mode)> AutonomousChoices() => graph!.GraphsALL
        .Where(x => x.GraphInfo.Type is GraphType.Idel or GraphType.StateONE &&
            x.GraphInfo.Animat is AnimatType.A_Start or AnimatType.B_Loop or AnimatType.Single)
        .Select(x => (x.GraphInfo.Type, x.GraphInfo.Name, x.GraphInfo.ModeType)).Distinct().ToList();

    private (GraphType Type, string Name, Mode Mode)? NextAutonomousChoice()
    {
        // 每轮随机取完所有动作/状态组合再补池，避免小动作长期被大资源组或同一心情挤掉。
        if (remainingAutonomousChoices.Count == 0) remainingAutonomousChoices.AddRange(AutonomousChoices());
        if (remainingAutonomousChoices.Count == 0) return null;
        int index = Random.Shared.Next(remainingAutonomousChoices.Count);
        var choice = remainingAutonomousChoices[index];
        remainingAutonomousChoices.RemoveAt(index);
        return choice;
    }

    private IGraph? AutonomousPart(GraphType type, string name, AnimatType part, Mode mode)
    {
        var choices = graph!.GraphsALL.Where(x => x.GraphInfo.Type == type && x.GraphInfo.Name == name &&
            x.GraphInfo.Animat == part).ToArray();
        if (choices.Length == 0) return null;
        var matching = choices.Where(x => x.GraphInfo.ModeType == mode).ToArray();
        // 原素材的开心下蹲等动作缺少同状态循环段；只在缺段时复用同动作的常规版本。
        if (matching.Length == 0) matching = choices.Where(x => x.GraphInfo.ModeType == Mode.Nomal).ToArray();
        if (matching.Length == 0) matching = choices;
        return matching[Random.Shared.Next(matching.Length)];
    }

    private List<IGraph> BuildAutonomousSequence((GraphType Type, string Name, Mode Mode) choice)
    {
        var sequence = new List<IGraph>();
        void Add(GraphType type, AnimatType part)
        {
            if (AutonomousPart(type, choice.Name, part, choice.Mode) is { } frame) sequence.Add(frame);
        }
        if (choice.Type == GraphType.StateONE)
        {
            // 躺下必须先坐下，起身也先回到坐姿；不能把 StateTWO 当作独立站姿动作播放。
            Add(GraphType.StateONE, AnimatType.A_Start);
            Add(GraphType.StateONE, AnimatType.B_Loop);
            Add(GraphType.StateTWO, AnimatType.A_Start);
            Add(GraphType.StateTWO, AnimatType.B_Loop);
            Add(GraphType.StateTWO, AnimatType.C_End);
            Add(GraphType.StateONE, AnimatType.B_Loop);
            Add(GraphType.StateONE, AnimatType.C_End);
        }
        else if (AutonomousPart(choice.Type, choice.Name, AnimatType.Single, choice.Mode) is { } single)
            sequence.Add(single);
        else
        {
            Add(choice.Type, AnimatType.A_Start);
            // 自娱自乐只有 B 段，也能直接播放；循环次数有限，所有动作最终都会回到常规姿态。
            int loops = Random.Shared.Next(2, 5);
            for (int i = 0; i < loops; i++) Add(choice.Type, AnimatType.B_Loop);
            Add(choice.Type, AnimatType.C_End);
        }
        return sequence;
    }

    private void StartAutonomousSequence(List<IGraph> sequence)
    {
        if (sequence.Count == 0 || !CanMoveAutonomously) return;
        CancelAutonomousSequence(returnToNormal: false);
        autonomousSequence = sequence;
        pet!.CleanState();
        PlayAutonomousFrame(autonomousGeneration, 0);
    }

    private void PlayAutonomousFrame(int generation, int index)
    {
        if (generation != autonomousGeneration || autonomousSequence == null) return;
        if (!CanMoveAutonomously)
        {
            CancelAutonomousSequence();
            return;
        }
        if (index >= autonomousSequence.Count)
        {
            autonomousSequence = null;
            autonomousFrame = null;
            pet!.DisplayToNomal();
            return;
        }
        autonomousFrame = autonomousSequence[index];
        pet!.Display(autonomousFrame, () => {
            // 原版动画回调在后台执行；回到 UI 后验证批次，拖动/提醒/隐藏不能被旧动作续播覆盖。
            if (!Dispatcher.HasShutdownStarted)
                Dispatcher.BeginInvoke(() => PlayAutonomousFrame(generation, index + 1));
        });
    }

    private void CancelAutonomousSequence(bool returnToNormal = true)
    {
        ++autonomousGeneration;
        if (autonomousSequence == null) return;
        autonomousSequence = null;
        autonomousFrame?.Stop(true);
        autonomousFrame = null;
        if (returnToNormal && ready && visible && !closing && !notificationsSuspended) pet!.DisplayToNomal();
    }

    private void RunAutonomousBehavior(int? selectedChoice = null)
    {
        if (!CanMoveAutonomously || autonomousSequence != null || warningSpeechPending ||
            pet!.MsgBar.Visibility == Visibility.Visible || (!pet.IsIdel && !DockedEdge.HasValue)) return;
        int choice = selectedChoice ?? Random.Shared.Next(5);
        if (choice == 4) return;
        if (DockedEdge.HasValue)
        {
            // 开启自主活动时，贴边不是永久停留状态；先回到工作区再播放完整动作，避免被屏幕裁切。
            pet.CleanState();
            ClampPosition();
            pet.DisplayToNomal();
            UpdateQuotaCloud();
        }
        if (choice == 0)
        {
            // 靠墙时优先选择满足原版边界条件的爬墙动作，不能又被随机走远而错过狭窄触发区。
            var climbs = graph!.GraphConfig.Moves.Where(move => move.LocateType is
                GraphHelper.Move.DirectionType.Left or GraphHelper.Move.DirectionType.Right && move.Triggered(pet)).ToArray();
            if (climbs.Length > 0) climbs[Random.Shared.Next(climbs.Length)].Display(pet);
            else pet.DisplayToMove();
        }
        else if (NextAutonomousChoice() is { } idle)
            StartAutonomousSequence(BuildAutonomousSequence(idle));
    }
}
