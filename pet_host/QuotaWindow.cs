using System;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace TokenMeter.Pet;

internal sealed class QuotaWindow : Window
{
    private bool shuttingDown;
    private readonly TextBlock provider = new();
    private readonly TextBlock primary = new() { FontSize = 25, FontWeight = FontWeights.SemiBold };
    private readonly TextBlock secondary = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock status = new() { Foreground = Brushes.SandyBrown, TextWrapping = TextWrapping.Wrap };
    private readonly Button open = new() { Content = "查看用量面板", Padding = new Thickness(5) };
    private readonly Button source = new() { Content = "角色 / 动画来源：VPet · 非商业试用", FontSize = 10,
        Margin = new Thickness(0, 8, 0, 0), Padding = new Thickness(0), Background = Brushes.Transparent,
        BorderThickness = new Thickness(0), Foreground = Brushes.LightGray };

    public QuotaWindow(bool demo, Action openPanel, Action unpin, Action credits)
    {
        Title = demo ? "TokenMeter 额度 · 演示数据" : "TokenMeter 额度";
        Width = 244;
        Height = 236;
        WindowStyle = WindowStyle.ToolWindow;
        ResizeMode = ResizeMode.NoResize;
        ShowInTaskbar = false;
        Topmost = true;
        Background = new SolidColorBrush(Color.FromRgb(29, 29, 29));
        Foreground = Brushes.WhiteSmoke;
        var stack = new StackPanel { Margin = new Thickness(14) };
        foreach (var block in new[] { provider, primary, secondary, status })
        {
            block.Margin = new Thickness(0, 0, 0, 7);
            stack.Children.Add(block);
        }
        open.Click += (_, _) => openPanel();
        stack.Children.Add(open);
        source.Click += (_, _) => credits();
        stack.Children.Add(source);
        Content = stack;
        // 用户关闭只取消常驻，不关闭桌宠；主应用退出时 Owner 会一并销毁窗口。
        Closing += (_, e) => { if (!shuttingDown) { e.Cancel = true; Hide(); unpin(); } };
        SetUsage(demo ? "Codex · 演示数据" : "等待用量数据", demo ? "剩余 65%" : "--", "", "");
    }

    internal void SetUsage(string provider, string primary, string secondary, string status)
    {
        this.provider.Text = provider;
        this.primary.Text = primary;
        this.secondary.Text = secondary;
        this.status.Text = status;
    }

    internal void SetTheme(JsonElement theme)
    {
        Brush Read(string key, Brush fallback)
        {
            // WPF 默认按钮画刷不保证为纯色；可选字段缺失或无效时保留原画刷，不强制转换。
            Color previous = (fallback as SolidColorBrush)?.Color ?? Colors.Transparent;
            Color color = QuotaCloudWindow.ReadThemeColor(theme, key, previous);
            return color == previous ? fallback : new SolidColorBrush(color);
        }
        // 复用主程序已有的可选主题消息；旧客户端不发消息时仍保留原深色额度窗。
        Background = Read("surface", Background);
        Foreground = Read("text", Foreground);
        primary.Foreground = Foreground;
        provider.Foreground = Read("subtext", provider.Foreground);
        secondary.Foreground = Read("subtext", secondary.Foreground);
        status.Foreground = Read("warning", status.Foreground);
        source.Foreground = Read("subtext", source.Foreground);
        open.Background = Read("accent", open.Background);
        open.Foreground = Read("on_accent", open.Foreground);
        open.BorderBrush = Read("border", open.BorderBrush);
    }

    internal void CloseForShutdown() { shuttingDown = true; Close(); }
}
