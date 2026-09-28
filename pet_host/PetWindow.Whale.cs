using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using System.Windows.Interop;
using VPet_Simulator.Core;
using static VPet_Simulator.Core.GraphInfo;

namespace TokenMeter.Pet;

internal sealed partial class PetWindow
{
    private JsonDocument? whaleCatalog;
    private MenuItem? whaleActionMenu;
    private MenuItem? whaleCustomMenu;
    private readonly Dictionary<string, APNGAnimation> whaleActionCache = new(StringComparer.Ordinal);
    private readonly Dictionary<string, Task<APNGAnimation>> whaleActionLoads = new(StringComparer.Ordinal);
    private CancellationTokenSource? whaleChainCancellation;
    private CancellationTokenSource? whaleStatusCancellation;
    private IGraph? whaleChainFrame;
    private string? lastWhaleAction;
    private string? activeWhaleWorkStatus;
    private int? lastWhaleBalanceTier;
    private string? lastWhaleQuotaSource;
    private double? lastWhaleRemaining;
    private int whaleActionGeneration;
    private bool whaleFacingRight;
    private bool whaleCardEligible;
    private Image? whaleTransition;
    private readonly DispatcherTimer whaleWhisperTimer = new();
    private readonly DispatcherTimer whaleMoveTimer = new() { Interval = TimeSpan.FromMilliseconds(16) };
    private readonly Stopwatch whaleMoveClock = new();
    private int whaleMoveStartX, whaleMoveStartY, whaleMoveDirection;
    private double whaleMoveDistance, whaleMoveLead, whaleMoveDuration;

    private JsonElement WhaleCatalog => (whaleCatalog ??= JsonDocument.Parse(File.ReadAllText(
        Path.Combine(resources, "pet", "whale", "actions.json")))).RootElement;

    private static IEnumerable<string> WhaleNames(JsonElement slot)
    {
        if (slot.ValueKind == JsonValueKind.String)
            yield return slot.GetString()!;
        else if (slot.ValueKind == JsonValueKind.Array)
            foreach (var item in slot.EnumerateArray())
                foreach (string name in WhaleNames(item)) yield return name;
    }

    private void AddWhaleActionMenus()
    {
        whaleActionMenu = new MenuItem { Header = "鲸鱼娘动作" };
        petMenu!.Items.Add(whaleActionMenu);
        void Group(string title, IEnumerable<string> names)
        {
            var group = new MenuItem { Header = title };
            foreach (string name in names.Distinct())
            {
                var item = new MenuItem { Header = name };
                item.Click += (_, _) => _ = PlayWhaleActionAsync(name);
                group.Items.Add(item);
            }
            whaleActionMenu.Items.Add(group);
        }
        var catalog = WhaleCatalog;
        Group("待机与转向", WhaleNames(catalog.GetProperty("idle")).Concat(WhaleNames(catalog.GetProperty("turn"))));
        Group("点击与拖拽", WhaleNames(catalog.GetProperty("clicks")).Concat(WhaleNames(catalog.GetProperty("drag"))));
        Group("移动", catalog.GetProperty("moves").GetProperty("actions").EnumerateArray()
            .Select(item => item.GetProperty("name").GetString()!));
        foreach (var category in catalog.GetProperty("categories").EnumerateArray())
            Group(category.GetProperty("id").GetString()!, WhaleNames(category.GetProperty("actions")));
        var events = catalog.GetProperty("events");
        Group("余额事件", WhaleNames(events.GetProperty("balance")));
        Group("碎碎念", WhaleNames(events.GetProperty("whisper")));
        Group("工作状态", WhaleNames(events.GetProperty("workStatus")));
        whaleCustomMenu = new MenuItem { Header = "自定义 WebM" };
        whaleActionMenu.Items.Add(whaleCustomMenu);
        whaleActionMenu.SubmenuOpened += (_, _) => {
            whaleActionMenu.IsEnabled = character == "whale";
            whaleCustomMenu.Items.Clear();
            string directory = Path.Combine(dataDirectory, "main-animation", "webm");
            if (!Directory.Exists(directory)) return;
            foreach (string file in Directory.EnumerateFiles(directory, "*.webm").OrderBy(Path.GetFileName))
            {
                string name = Path.GetFileNameWithoutExtension(file);
                var item = new MenuItem { Header = name };
                item.Click += (_, _) => _ = PlayWhaleActionAsync(name);
                whaleCustomMenu.Items.Add(item);
            }
        };
    }

    private string WhaleSource(string name)
    {
        string custom = Path.Combine(dataDirectory, "main-animation", "webm", name + ".webm");
        if (File.Exists(custom)) return custom;
        string bundled = Path.Combine(resources, "pet", "whale", "apng", name + ".png");
        if (File.Exists(bundled)) return bundled;
        throw new FileNotFoundException("鲸鱼娘动作素材不存在", name);
    }

    private async Task<APNGAnimation> WhaleActionGraphAsync(string name)
    {
        string source = WhaleSource(name);
        // 文件内容参与缓存身份；用户同名覆盖或更新自定义 WebM 时不会继续播放旧帧。
        // 取景比例修正后不能复用旧 WebM 转码，否则同一素材仍会在动作开始时缩小。
        string key = "whale-v2-" + Convert.ToHexString(SHA256.HashData(await File.ReadAllBytesAsync(source)));
        if (whaleActionCache.TryGetValue(key, out var cached)) return cached;
        if (whaleActionLoads.TryGetValue(key, out var loading)) return await loading;
        var task = LoadWhaleActionGraphAsync(name, source, key);
        whaleActionLoads[key] = task;
        try
        {
            var result = await task;
            whaleActionCache[key] = result;
            return result;
        }
        finally { whaleActionLoads.Remove(key); }
    }

    private async Task<APNGAnimation> LoadWhaleActionGraphAsync(string name, string source, string key)
    {
        string destination = source;
        if (Path.GetExtension(source).Equals(".webm", StringComparison.OrdinalIgnoreCase))
        {
            string cache = Path.Combine(dataDirectory, "cache", "whale");
            Directory.CreateDirectory(cache);
            destination = Path.Combine(cache, key + ".png");
        }
        if (destination != source && !File.Exists(destination))
        {
            string temporary = destination + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                // 自定义 WebM 与 build_vpet.py 的预制动作使用相同固定取景，保持人物比例和脚底位置。
                var start = new ProcessStartInfo("ffmpeg") {
                    UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true
                };
                foreach (string argument in new[] { "-y", "-loglevel", "error", "-c:v", "libvpx-vp9",
                    "-i", source, "-vf", "fps=15,crop=iw*3/4:ih,scale=250:188:flags=lanczos,format=rgba,pad=250:250:0:62:color=black@0",
                    "-plays", "0", "-f", "apng", temporary })
                    start.ArgumentList.Add(argument);
                using var process = Process.Start(start) ?? throw new IOException("无法启动鲸鱼娘动画解码器");
                var errors = process.StandardError.ReadToEndAsync();
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(60));
                try { await process.WaitForExitAsync(timeout.Token); }
                catch (OperationCanceledException) { process.Kill(true); throw new TimeoutException("鲸鱼娘动画转码超时"); }
                if (process.ExitCode != 0) throw new InvalidDataException("鲸鱼娘动画转码失败：" + await errors);
                File.Move(temporary, destination, true);
            }
            finally { File.Delete(temporary); }
        }
        var owner = whaleGraph ?? throw new InvalidOperationException("鲸鱼娘图池尚未就绪");
        var animation = new APNGAnimation(owner, destination,
            new GraphInfo(name, GraphType.Common, AnimatType.Single, save.Mode));
        while (!closing && !animation.IsReady && !animation.IsFail) await Task.Delay(30);
        if (closing || animation.IsFail)
        {
            string message = animation.FailMessage;
            animation.Dispose();
            throw new InvalidDataException("鲸鱼娘动画加载失败：" + message);
        }
        owner.AddGraph(animation);
        return animation;
    }

    private async Task PlayWhaleActionAsync(string name)
    {
        if (character != "whale" || closing || !visible) return;
        CancelWhaleChain();
        CancelWhaleStatus();
        int generation = ++whaleActionGeneration;
        try
        {
            var animation = await WhaleActionGraphAsync(name);
            if (generation != whaleActionGeneration || character != "whale" || closing || !visible) return;
            CancelAutonomousSequence(returnToNormal: false);
            pet!.CleanState();
            if (IsWhaleMove(name)) StartWhaleMove(name);
            pet.Display(animation, () => {
                if (!Dispatcher.HasShutdownStarted) Dispatcher.BeginInvoke(() => {
                    if (generation != whaleActionGeneration || character != "whale" || closing) return;
                    StopWhaleMove();
                    pet.DisplayToNomal();
                    TryStartWhaleChain();
                });
            });
        }
        catch (Exception ex)
        {
            File.AppendAllText(Path.Combine(dataDirectory, "host-error.log"), ex + "\n");
            if (generation == whaleActionGeneration && !closing)
                MessageBox.Show(this, ex.Message, "鲸鱼娘动作失败", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private void CancelWhaleChain()
    {
        StopWhaleMove();
        whaleChainCancellation?.Cancel();
        whaleChainCancellation?.Dispose();
        whaleChainCancellation = null;
        whaleChainFrame = null;
    }

    private void CancelWhaleStatus()
    {
        whaleStatusCancellation?.Cancel();
        whaleStatusCancellation?.Dispose();
        whaleStatusCancellation = null;
        if (activeWhaleWorkStatus != null) pet?.MsgBar?.ForceClose();
        activeWhaleWorkStatus = null;
    }

    private void InitializeWhaleEvents()
    {
        int seconds = WhaleCatalog.GetProperty("eventsRefreshSec").GetProperty("whisper").GetInt32();
        whaleWhisperTimer.Interval = TimeSpan.FromSeconds(seconds);
        whaleWhisperTimer.Tick += (_, _) => {
            if (character == "whale" && CanMoveAutonomously && activeWhaleWorkStatus == null)
                _ = PlayWhaleActionAsync(PickWhaleName(WhaleNames(
                    WhaleCatalog.GetProperty("events").GetProperty("whisper"))));
        };
        whaleMoveTimer.Tick += (_, _) => AdvanceWhaleMove();
    }

    private void SyncWhaleEvents()
    {
        whaleWhisperTimer.IsEnabled = character == "whale" && ready && visible && !closing &&
            !notificationsSuspended && allowMove && !LogicalDockedEdge.HasValue;
    }

    private void OnWhaleUsage(string provider)
    {
        if (quotaCloud?.RemainingPercent is not double remaining)
        {
            lastWhaleQuotaSource = null;
            lastWhaleRemaining = null;
            lastWhaleBalanceTier = null;
            return;
        }
        bool sameSource = string.Equals(lastWhaleQuotaSource, provider, StringComparison.Ordinal);
        double decrease = sameSource && lastWhaleRemaining is double previous && remaining < previous
            ? previous - remaining : 0;
        if (!sameSource) lastWhaleBalanceTier = null;
        lastWhaleQuotaSource = provider;
        lastWhaleRemaining = remaining;
        if (character != "whale") return;
        if (decrease > 0 && whaleCard?.IsVisible == true) whaleCard.ShowQuotaDecrease(decrease);
        double used = 100 - remaining;
        int tier = used >= 100 ? 5 : Math.Clamp((int)(used / 20), 0, 4);
        // 同一档位内额度也会下降；只凭档位变化会漏掉这类实际消耗。
        if (tier == lastWhaleBalanceTier && decrease == 0) return;
        lastWhaleBalanceTier = tier;
        if (activeWhaleWorkStatus != null) return;
        string name = PickWhaleName(WhaleNames(
            WhaleCatalog.GetProperty("events").GetProperty("balance")[tier]));
        _ = PlayWhaleActionAsync(name);
    }

    private void UpdateWhaleCardData(JsonElement usage, bool? pricingPeak)
    {
        if (whaleCard == null) return;
        string provider = usage.TryGetProperty("provider", out var source) && source.ValueKind == JsonValueKind.String
            ? source.GetString() ?? "" : "";
        if (quotaCloud?.RemainingPercent is double remaining)
        {
            whaleCardEligible = true;
            whaleCard.SetQuota(remaining);
            UpdateQuotaCloud();
            return;
        }
        whaleCardEligible = provider.StartsWith("DeepSeek", StringComparison.OrdinalIgnoreCase);
        if (!whaleCardEligible) { whaleCard.Hide(); UpdateQuotaCloud(); return; }
        static decimal? Amount(JsonElement data, string key)
        {
            if (!data.TryGetProperty(key, out var value)) return null;
            if (value.ValueKind == JsonValueKind.Number && value.TryGetDecimal(out decimal number)) return number;
            if (value.ValueKind == JsonValueKind.String && decimal.TryParse(value.GetString(),
                NumberStyles.Number, CultureInfo.InvariantCulture, out number)) return number;
            return null;
        }
        string primaryText = usage.TryGetProperty("primary", out var primary) && primary.ValueKind == JsonValueKind.String
            ? primary.GetString() ?? "" : "";
        bool reset = usage.TryGetProperty("balance_reset", out var changed) && changed.ValueKind == JsonValueKind.True;
        whaleCard.SetBalance(primaryText, Amount(usage, "balance_amount"),
            Amount(usage, "total_cost_amount"), pricingPeak, reset);
        UpdateQuotaCloud();
    }

    private void UpdateWhaleBalanceCard()
    {
        if (whaleCard == null) return;
        // 角落卡片也是额度展示；持久关闭必须同时隐藏卡片和普通气泡。
        if (character != "whale" || !ready || !visible || !IsVisible || closing || cloudMode == "off" ||
            !whaleCardEligible || !WhaleAtBottomCorner())
        {
            whaleCard.Hide();
            return;
        }
        var handle = new WindowInteropHelper(this).Handle;
        if (!GetWindowRect(handle, out var petRect)) return;
        var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
        var pixels = WhaleVisiblePixels();
        var characterBounds = new Rect(petRect.Left + pixels.Left * (petRect.Right - petRect.Left),
            petRect.Top + pixels.Top * (petRect.Bottom - petRect.Top),
            pixels.Width * (petRect.Right - petRect.Left), pixels.Height * (petRect.Bottom - petRect.Top));
        whaleCard.ShowNextTo(characterBounds, new Rect(work.X, work.Y, work.Width, work.Height),
            VisualTreeHelper.GetDpi(this), LogicalDockedEdge == true);
    }

    private bool WhaleAtBottomCorner()
    {
        if (character != "whale" || !LogicalDockedEdge.HasValue) return false;
        var handle = new WindowInteropHelper(this).Handle;
        if (!GetWindowRect(handle, out var petRect)) return false;
        var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
        // 以可拖动窗口的底边判断角落，透明动画画布的留白不能改变触发位置。
        return petRect.Bottom >= work.Bottom - 24 * VisualTreeHelper.GetDpi(this).DpiScaleY;
    }

    private async Task ShowWhaleWorkStatusAsync(string? status, string? message)
    {
        if (character != "whale") return;
        CancelWhaleChain();
        CancelWhaleStatus();
        int generation = ++whaleActionGeneration;
        string[] stages = { "thinking", "working", "result", "waiting", "success", "error" };
        int tier = Array.IndexOf(stages, status);
        if (tier < 0)
        {
            pet!.DisplayToNomal();
            TryStartWhaleChain();
            return;
        }
        activeWhaleWorkStatus = status;
        var cancellation = new CancellationTokenSource();
        whaleStatusCancellation = cancellation;
        if (!string.IsNullOrWhiteSpace(message)) pet!.MsgBar.Show(save.Name, message);
        try
        {
            do
            {
                string name = PickWhaleName(WhaleNames(
                    WhaleCatalog.GetProperty("events").GetProperty("workStatus")[tier]));
                var animation = await WhaleActionGraphAsync(name);
                if (cancellation.IsCancellationRequested || generation != whaleActionGeneration || closing) return;
                var finished = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                pet!.Display(animation, () => finished.TrySetResult());
                await finished.Task.WaitAsync(cancellation.Token);
            }
            while (tier < 4 && visible && !closing);
            if (generation == whaleActionGeneration && character == "whale")
            {
                pet!.DisplayToNomal();
                TryStartWhaleChain();
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { File.AppendAllText(Path.Combine(dataDirectory, "host-error.log"), ex + "\n"); }
        finally
        {
            if (whaleStatusCancellation == cancellation)
            {
                whaleStatusCancellation = null;
                activeWhaleWorkStatus = null;
                pet?.MsgBar?.ForceClose();
                cancellation.Dispose();
            }
        }
    }

    // 冒烟检查逐项点播真实动作，后台随机链不能抢走截图和交互断言的画布。
    private bool CanRunWhaleChain => !smoke && character == "whale" && CanMoveAutonomously &&
        pet?.DisplayType.Type == GraphType.Default;

    private void TryStartWhaleChain()
    {
        if (!CanRunWhaleChain || whaleChainCancellation != null) return;
        var cancellation = new CancellationTokenSource();
        whaleChainCancellation = cancellation;
        _ = RunWhaleChainAsync(cancellation);
    }

    private async Task RunWhaleChainAsync(CancellationTokenSource cancellation)
    {
        try
        {
            var queued = PickWhaleChainAction(whaleFacingRight);
            Task<APNGAnimation>? prepared = null;
            while (!cancellation.IsCancellationRequested && character == "whale")
            {
                var (name, kind) = queued;
                var animation = prepared == null ? await WhaleActionGraphAsync(name) : await prepared;
                if (cancellation.IsCancellationRequested || !CanMoveAutonomously || character != "whale") return;
                // 当前片段播放期间先准备下一段；播放回调到来时可直接切帧，避免首次转码造成空白。
                queued = PickWhaleChainAction(kind == "turn" ? !whaleFacingRight : whaleFacingRight);
                prepared = WhaleActionGraphAsync(queued.Name);
                whaleChainFrame = animation;
                var finished = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                if (kind == "move") StartWhaleMove(name);
                pet!.Display(animation, () => finished.TrySetResult());
                await finished.Task.WaitAsync(cancellation.Token);
                StopWhaleMove();
                if (kind == "turn") SetWhaleFacing(!whaleFacingRight);
                whaleChainFrame = null;
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            File.AppendAllText(Path.Combine(dataDirectory, "host-error.log"), ex + "\n");
        }
        finally
        {
            if (whaleChainCancellation == cancellation)
            {
                whaleChainCancellation = null;
                whaleChainFrame = null;
                cancellation.Dispose();
            }
        }
    }

    private (string Name, string Kind) PickWhaleChainAction(bool facingRight)
    {
        var catalog = WhaleCatalog;
        var weights = catalog.GetProperty("animationWeights");
        int idle = weights.GetProperty("idle").GetInt32();
        int turn = weights.GetProperty("turn").GetInt32();
        int move = weights.GetProperty("move").GetInt32();
        int roll = Random.Shared.Next(100);
        if (roll < idle) return (PickWhaleName(WhaleNames(catalog.GetProperty("idle"))), "idle");
        if (roll < idle + turn) return (PickWhaleName(WhaleNames(catalog.GetProperty("turn"))), "turn");
        if (roll < idle + turn + move)
            return (PickWhaleName(catalog.GetProperty("moves").GetProperty("actions").EnumerateArray()
                .Select(item => item.GetProperty("name").GetString()!)), "move");
        var categories = catalog.GetProperty("categories").EnumerateArray()
            .Where(item => !facingRight || !item.TryGetProperty("noMirror", out var noMirror) ||
                noMirror.ValueKind != JsonValueKind.True).ToArray();
        int total = categories.Sum(item => item.GetProperty("weight").GetInt32());
        int selected = Random.Shared.Next(total);
        foreach (var category in categories)
        {
            selected -= category.GetProperty("weight").GetInt32();
            if (selected < 0) return (PickWhaleName(WhaleNames(category.GetProperty("actions"))), "action");
        }
        return (PickWhaleName(WhaleNames(catalog.GetProperty("idle"))), "idle");
    }

    private string PickWhaleName(IEnumerable<string> names)
    {
        string[] choices = names.ToArray();
        string[] eligible = choices.Where(name => name != lastWhaleAction).ToArray();
        string selected = (eligible.Length == 0 ? choices : eligible)[Random.Shared.Next(eligible.Length == 0 ? choices.Length : eligible.Length)];
        lastWhaleAction = selected;
        return selected;
    }

    private void SetWhaleFacing(bool right)
    {
        whaleFacingRight = right;
        var mirror = new ScaleTransform(right ? -1 : 1, 1, 250, 0);
        pet!.PetGrid.RenderTransform = mirror;
        pet.PetGrid2.RenderTransform = mirror;
    }

    private void BeginWhaleTransition()
    {
        if (character != "whale" || !ready || pet == null) return;
        var current = pet.PetGrid.Visibility == Visibility.Visible ? pet.PetGrid : pet.PetGrid2;
        if (current.ActualWidth <= 0 || current.ActualHeight <= 0) return;
        int width = (int)Math.Ceiling(current.ActualWidth), height = (int)Math.Ceiling(current.ActualHeight);
        var frame = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        frame.Render(current);
        ClearWhaleTransition();
        var overlay = new Image { Source = frame, Width = width, Height = height, IsHitTestVisible = false };
        whaleTransition = overlay;
        pet.MainGrid.Children.Insert(pet.MainGrid.Children.IndexOf(pet.UIGrid), overlay);
        // 原内核下一步会停止旧画布；保留最后一帧短暂淡出，与新画布交叠，避免透明视频切换闪空。
        Dispatcher.BeginInvoke(DispatcherPriority.Render, () => {
            var fade = new DoubleAnimation(1, 0, TimeSpan.FromMilliseconds(180));
            fade.Completed += (_, _) => { if (whaleTransition == overlay) ClearWhaleTransition(); };
            overlay.BeginAnimation(OpacityProperty, fade);
        });
    }

    private void ClearWhaleTransition()
    {
        if (whaleTransition == null || pet == null) return;
        pet.MainGrid.Children.Remove(whaleTransition);
        whaleTransition = null;
    }

    private bool IsWhaleMove(string name) => WhaleCatalog.GetProperty("moves").GetProperty("actions")
        .EnumerateArray().Any(item => item.GetProperty("name").GetString() == name);

    private void StartWhaleMove(string name)
    {
        StopWhaleMove();
        var handle = new WindowInteropHelper(this).Handle;
        if (!GetWindowRect(handle, out var rect)) return;
        var defaults = WhaleCatalog.GetProperty("moves").GetProperty("default");
        int min = defaults.GetProperty("minDist").GetInt32();
        int max = defaults.GetProperty("maxDist").GetInt32();
        whaleMoveLead = defaults.GetProperty("leadSec").GetDouble();
        double tail = defaults.GetProperty("tailSec").GetDouble();
        foreach (var action in WhaleCatalog.GetProperty("moves").GetProperty("actions").EnumerateArray())
        {
            if (action.GetProperty("name").GetString() != name || !action.TryGetProperty("params", out var parameters)) continue;
            if (parameters.TryGetProperty("minDist", out var value)) min = value.GetInt32();
            if (parameters.TryGetProperty("maxDist", out value)) max = value.GetInt32();
            if (parameters.TryGetProperty("leadSec", out value)) whaleMoveLead = value.GetDouble();
            if (parameters.TryGetProperty("tailSec", out value)) tail = value.GetDouble();
        }
        whaleMoveDuration = Math.Max(0.1, 10 - whaleMoveLead - tail);
        whaleMoveDistance = Random.Shared.Next(min, max) * size / 462.0;
        whaleMoveStartX = rect.Left;
        whaleMoveStartY = rect.Top;
        whaleMoveDirection = whaleFacingRight ? 1 : -1;
        var work = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
        var pixels = WhaleVisiblePixels();
        double minX = work.Left - pixels.Left * (rect.Right - rect.Left);
        double maxX = work.Right - pixels.Right * (rect.Right - rect.Left);
        if (whaleMoveStartX + whaleMoveDirection * whaleMoveDistance < minX ||
            whaleMoveStartX + whaleMoveDirection * whaleMoveDistance > maxX)
        {
            whaleMoveDirection = -whaleMoveDirection;
            SetWhaleFacing(whaleMoveDirection > 0);
        }
        whaleMoveDistance = Math.Min(whaleMoveDistance, whaleMoveDirection > 0
            ? maxX - whaleMoveStartX : whaleMoveStartX - minX);
        if (whaleMoveDistance <= 0) return;
        whaleMoveClock.Restart();
        whaleMoveTimer.Start();
    }

    private void AdvanceWhaleMove()
    {
        double progress = Math.Clamp((whaleMoveClock.Elapsed.TotalSeconds - whaleMoveLead) / whaleMoveDuration, 0, 1);
        int x = whaleMoveStartX + (int)Math.Round(whaleMoveDirection * whaleMoveDistance * progress);
        SetWindowPos(new WindowInteropHelper(this).Handle, IntPtr.Zero, x, whaleMoveStartY,
            0, 0, DragPositionFlags);
        if (progress >= 1) StopWhaleMove();
    }

    private void StopWhaleMove()
    {
        whaleMoveTimer.Stop();
        whaleMoveClock.Stop();
    }
}
