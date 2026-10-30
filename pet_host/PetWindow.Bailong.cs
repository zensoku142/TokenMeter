using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using VPet_Simulator.Core;
using static VPet_Simulator.Core.GraphInfo;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private MenuItem? bailongActionMenu;
    private int bailongActionGeneration;
    private IGraph? bailongManualFrame;

    private void AddBailongActionMenu()
    {
        bailongActionMenu = new MenuItem { Header = "小龙娘动作" };
        petMenu!.Items.Add(bailongActionMenu);
        using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(resources, "pet", "bailong", "actions.json")));
        var catalog = document.RootElement;
        foreach (var movement in catalog.GetProperty("movementAnimations").EnumerateArray())
            bailongMovements[movement.GetProperty("id").GetString()!] = movement.Clone();
        bailongMoveTimer.Tick += (_, _) => AdvanceBailongMovement();
        void Group(string title, IEnumerable<JsonElement> items)
        {
            var group = new MenuItem { Header = title };
            foreach (var item in items.Where(item => item.ValueKind == JsonValueKind.Object))
            {
                string id = item.GetProperty("id").GetString()!;
                string name = item.TryGetProperty("name", out var label) ? label.GetString()! : "默认待机";
                var action = new MenuItem { Header = name };
                action.Click += (_, _) => PlayBailongAction(id);
                group.Items.Add(action);
            }
            bailongActionMenu.Items.Add(group);
        }
        Group("待机", new[] { catalog.GetProperty("idle") }.Concat(catalog.GetProperty("additionalIdleAnimations").EnumerateArray()));
        Group("互动", catalog.GetProperty("variants").EnumerateArray());
        Group("工作动作", catalog.GetProperty("workAnimations").EnumerateArray());
        Group("移动动作", catalog.GetProperty("movementAnimations").EnumerateArray());
        Group("拖拽与额度动作", new[] { catalog.GetProperty("dragAnimation"), catalog.GetProperty("landingAnimation"),
            catalog.GetProperty("quotaAnimation") });
    }

    private void PlayBailongAction(string id)
    {
        if (character != "bailong" || pet == null || closing || !visible) return;
        var animation = graph!.FindGraph("bailong.action." + id, AnimatType.Single, save.Mode);
        if (animation == null) return;
        CancelAutonomousSequence();
        int generation = ++bailongActionGeneration;
        bailongManualFrame = animation;
        pet.CleanState();
        pet.Display(animation, () => {
            // 动画回调来自后台；只允许当前动作恢复待机，不能覆盖后来的拖动、切换或隐藏。
            if (!Dispatcher.HasShutdownStarted)
                Dispatcher.BeginInvoke(() => {
                    if (generation == bailongActionGeneration && character == "bailong" && visible && !closing)
                    {
                        bailongManualFrame = null;
                        StopBailongMovement();
                        pet.DisplayToNomal();
                    }
                });
        });
        StartBailongMovement(animation.GraphInfo.Name);
    }

    private void CancelBailongAction(bool returnToNormal)
    {
        ++bailongActionGeneration;
        var active = bailongManualFrame;
        bailongManualFrame = null;
        StopBailongMovement();
        active?.Stop(true);
        if (active != null && returnToNormal && ready && visible && !closing && !notificationsSuspended)
            pet!.DisplayToNomal();
    }

    private async Task RunBailongVisualCheck(string output)
    {
        var checks = new Dictionary<string, bool>();
        checks["sameVPetCore"] = graph == bailongGraph && pet!.Core.Graph == graph;
        checks["all35Actions"] = graph!.GraphsALL.Count(animation => animation.GraphInfo.Name.StartsWith("bailong.action.")) == 35;
        checks["frameworkGestures"] = new[] { GraphType.Touch_Head, GraphType.Touch_Body, GraphType.Raised_Static,
            GraphType.Move, GraphType.SideHide_Left_Main, GraphType.SideHide_Right_Main }.All(type => graph.FindName(type) != null);
        checks["modelSelector"] = bailongCharacterMenuItem != null;
        checks["noChatMenu"] = !petMenu!.Items.OfType<MenuItem>().Any(item => item.Header.ToString()!.Contains("对话"));
        Capture(this, Path.Combine(output, "bailong-a.png"));
        await Task.Delay(400);
        Capture(this, Path.Combine(output, "bailong-b.png"));
        checks["animated"] = !File.ReadAllBytes(Path.Combine(output, "bailong-a.png"))
            .SequenceEqual(File.ReadAllBytes(Path.Combine(output, "bailong-b.png")));
        PlayBailongAction("20");
        await Task.Delay(180);
        checks["manualAction"] = pet!.DisplayType.Name == "bailong.action.20";
        Capture(this, Path.Combine(output, "bailong-action.png"));
        CancelAutonomousSequence();
        pet.DisplayToNomal();
        using (var usage = JsonDocument.Parse("{\"type\":\"usage\",\"provider\":\"Codex · 演示数据\",\"primary\":\"剩余 65%\",\"secondary\":\"\",\"status\":\"\",\"warning\":false}"))
            Receive(usage.RootElement);
        cloudManualChoice = true;
        UpdateQuotaCloud();
        await Task.Delay(120);
        checks["quotaBubble"] = quotaCloud?.IsVisible == true;
        if (quotaCloud!.ActualWidth > 0) Capture(quotaCloud, Path.Combine(output, "bailong-quota.png"));
        int oldSize = size;
        ResizePet(20);
        checks["frameworkResize"] = size == Math.Min(320, oldSize + 20);
        ResizePet(-20);
        ResizePet(-1000);
        checks["smallerMinimum"] = size == 80 && Width == 80 && Height == 80;
        ResizePet(-20);
        checks["minimumClamped"] = size == 80;
        checks["frameworkDock"] = TrySnapPetToEdge(false);
        manualDockedEdge = null;
        pet.DisplayToNomal();
        await ChangeCharacterAsync("whale");
        checks["switchToWhale"] = character == "whale" && graph == whaleGraph;
        checks["oldModelMinimumPreserved"] = size >= 160;
        await ChangeCharacterAsync("bailong");
        checks["switchBack"] = character == "bailong" && graph == bailongGraph;
        await RunBailongAutonomyChecks(checks);
        SaveState();
        File.WriteAllText(Path.Combine(output, "report.json"), JsonSerializer.Serialize(new {
            checks, animations = graph!.GraphsALL.Count, errors = pet.ErrorMessage,
        }, new JsonSerializerOptions { WriteIndented = true }));
        Application.Current.Shutdown(checks.Values.All(value => value) ? 0 : 1);
    }
}
