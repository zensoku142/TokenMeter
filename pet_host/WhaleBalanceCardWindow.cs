using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;

namespace TokenMeter.Pet;

internal sealed class WhaleBalanceCardWindow : Window
{
    private readonly TextBlock amount = new() { FontSize = 15, FontWeight = FontWeights.Bold,
        Foreground = new SolidColorBrush(Color.FromRgb(255, 105, 105)),
        VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock peak = new() { FontSize = 14, FontWeight = FontWeights.Bold,
        VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center,
        Margin = new Thickness(4, 0, 0, 0) };
    private readonly TextBlock label = new() { Text = "余额", FontSize = 11, FontWeight = FontWeights.SemiBold,
        Foreground = Brushes.White, VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock feedback = new() { FontSize = 16, FontWeight = FontWeights.Bold,
        Effect = new DropShadowEffect { Color = Colors.Black, BlurRadius = 3, ShadowDepth = 1, Opacity = 0.65 } };
    private readonly TranslateTransform feedbackOffset = new();
    private readonly Canvas deductionLayer = new() { Width = 146, Height = 56,
        HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top,
        IsHitTestVisible = false };
    private readonly List<TextBlock> deductionItems = new();
    private readonly Grid canvas;
    private readonly Border card;
    private readonly Grid columns;
    private readonly Viewbox amountView;
    private decimal? lastBalance, lastCost;

    internal string AmountText => amount.Text;
    internal string LabelText => label.Text;
    internal string PeakText => peak.Text;
    internal string FeedbackText => feedback.Text;
    internal bool FeedbackVisible => feedback.Visibility == Visibility.Visible || deductionItems.Count > 0;
    internal int DeductionCount => deductionItems.Count;

    internal WhaleBalanceCardWindow(Action openPanel)
    {
        Title = "TokenMeter · 鲸鱼娘余额卡片";
        Width = 146;
        // 额外透明高度只供扣除提示上飘；卡片本体仍保持 140 × 35。
        Height = 94;
        WindowStyle = WindowStyle.None;
        ResizeMode = ResizeMode.NoResize;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        ShowInTaskbar = false;
        ShowActivated = false;
        Focusable = false;
        Topmost = true;
        canvas = new Grid { Width = 146, Height = 94 };
        card = new Border { Width = 140, Height = 35, Margin = new Thickness(3, 56, 3, 3),
            VerticalAlignment = VerticalAlignment.Top, CornerRadius = new CornerRadius(13),
            BorderThickness = new Thickness(1), BorderBrush = new SolidColorBrush(Color.FromRgb(95, 104, 116)),
            Background = new LinearGradientBrush(Color.FromRgb(43, 48, 56), Color.FromRgb(27, 32, 39), 90),
            Effect = new DropShadowEffect { Color = Colors.Black, BlurRadius = 6, ShadowDepth = 2, Opacity = 0.25 } };
        columns = new Grid { Margin = new Thickness(7, 0, 6, 0) };
        columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(32) });
        columns.ColumnDefinitions.Add(new ColumnDefinition());
        columns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
        Grid.SetColumn(label, 0);
        columns.Children.Add(label);
        amountView = new Viewbox { Stretch = Stretch.Uniform,
            StretchDirection = StretchDirection.DownOnly, VerticalAlignment = VerticalAlignment.Center,
            HorizontalAlignment = HorizontalAlignment.Stretch, MaxHeight = 22, Child = amount };
        Grid.SetColumn(amountView, 1);
        columns.Children.Add(amountView);
        Grid.SetColumn(peak, 2);
        columns.Children.Add(peak);
        card.Child = columns;
        canvas.Children.Add(card);
        canvas.Children.Add(deductionLayer);
        feedback.RenderTransform = feedbackOffset;
        feedback.HorizontalAlignment = HorizontalAlignment.Left;
        feedback.VerticalAlignment = VerticalAlignment.Top;
        feedback.Margin = new Thickness(5, 40, 0, 0);
        feedback.Visibility = Visibility.Collapsed;
        canvas.Children.Add(feedback);
        Content = new Viewbox { Child = canvas };
        SourceInitialized += (_, _) => {
            var handle = new WindowInteropHelper(this).Handle;
            SetWindowLong(handle, -20, (GetWindowLong(handle, -20) & ~0x20) | 0x08000000 | 0x80);
        };
        MouseDoubleClick += (_, e) => {
            if (e.ChangedButton != MouseButton.Left) return;
            e.Handled = true;
            openPanel();
        };
        amount.Text = "--";
    }

    internal void SetBalance(string primary, decimal? balance, decimal? totalCost, bool? pricingPeak, bool reset)
    {
        if (label.Text != "余额")
        {
            feedback.Visibility = Visibility.Collapsed;
            ClearDeductions();
        }
        label.Text = "余额";
        SetCardWidth(146);
        if (reset) { lastBalance = null; lastCost = null; }
        amount.Text = balance is { } value ? "CNY " + value.ToString("0.00", CultureInfo.InvariantCulture) :
            primary.StartsWith("余额 ", StringComparison.Ordinal) ? primary[3..] : "--";
        peak.Text = pricingPeak is null ? "" : pricingPeak.Value ? "峰" : "谷";
        peak.Foreground = new SolidColorBrush(pricingPeak == true
            ? Color.FromRgb(255, 90, 90) : Color.FromRgb(62, 208, 137));
        decimal change = 0;
        if (totalCost is { } cost)
        {
            if (lastCost is { } previous && cost > previous) change = -(cost - previous);
            lastCost = cost;
        }
        else if (balance is { } current && lastBalance is { } old)
            change = current - old;
        if (balance is { } currentBalance) lastBalance = currentBalance;
        // 同一次刷新可能同时更新余额和累计费用；优先用实际费用差额，避免重复播放扣款。
        if (Math.Abs(change) >= 0.000001m) ShowFeedback(change);
    }

    internal void SetQuota(double remaining)
    {
        if (label.Text != "额度") ClearDeductions();
        label.Text = "额度";
        SetCardWidth(106);
        amount.Text = remaining.ToString("0", CultureInfo.InvariantCulture) + "%";
        peak.Text = "";
        // 切换配置后不再沿用 DeepSeek 的峰谷和扣款动画。
        feedback.Visibility = Visibility.Collapsed;
        lastBalance = lastCost = null;
    }

    internal void ShowQuotaDecrease(double decrease)
    {
        string value = "-" + decrease.ToString("0.##", CultureInfo.InvariantCulture) + "%";
        feedback.Text = value;
        ShowDeduction(value);
    }

    private void ShowDeduction(string value)
    {
        // 连续扣除按参考效果向上堆叠，旧提示淡出前仍保留各自的数值。
        foreach (var item in deductionItems)
            Canvas.SetTop(item, Math.Max(0, Canvas.GetTop(item) - 17));
        if (deductionItems.Count == 3)
        {
            deductionLayer.Children.Remove(deductionItems[0]);
            deductionItems.RemoveAt(0);
        }
        var notice = new TextBlock { FontWeight = FontWeights.Bold, IsHitTestVisible = false,
            Effect = new DropShadowEffect { Color = Color.FromRgb(255, 36, 42), BlurRadius = 9,
                ShadowDepth = 0, Opacity = 1 } };
        notice.Inlines.Add(new Run("扣除 ") { FontSize = 12,
            Foreground = new SolidColorBrush(Color.FromRgb(255, 105, 89)) });
        notice.Inlines.Add(new Run(value) { FontSize = 17,
            Foreground = new SolidColorBrush(Color.FromRgb(255, 221, 112)) });
        Canvas.SetLeft(notice, 4);
        Canvas.SetTop(notice, 34);
        deductionLayer.Children.Add(notice);
        deductionItems.Add(notice);
        var offset = new TranslateTransform();
        notice.RenderTransform = offset;
        offset.BeginAnimation(TranslateTransform.YProperty,
            new DoubleAnimation(0, -16, TimeSpan.FromSeconds(1.7)) {
                EasingFunction = new QuadraticEase { EasingMode = EasingMode.EaseOut }
            });
        var fade = new DoubleAnimationUsingKeyFrames();
        fade.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.Zero)));
        fade.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(0.12))));
        fade.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(0.85))));
        fade.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(1.7))));
        fade.Completed += (_, _) => {
            deductionLayer.Children.Remove(notice);
            deductionItems.Remove(notice);
        };
        notice.BeginAnimation(OpacityProperty, fade);
    }

    private void ClearDeductions()
    {
        deductionLayer.Children.Clear();
        deductionItems.Clear();
    }

    private void SetCardWidth(double width)
    {
        if (Width == width) return;
        bool quota = width < 146;
        Width = canvas.Width = deductionLayer.Width = width;
        card.Width = width - 6;
        columns.Margin = quota ? new Thickness(4, 0, 4, 0) : new Thickness(7, 0, 6, 0);
        columns.HorizontalAlignment = quota ? HorizontalAlignment.Center : HorizontalAlignment.Stretch;
        columns.ColumnDefinitions[0].Width = quota ? GridLength.Auto : new GridLength(32);
        columns.ColumnDefinitions[1].Width = quota ? GridLength.Auto : new GridLength(1, GridUnitType.Star);
        columns.ColumnDefinitions[2].Width = quota ? new GridLength(0) : new GridLength(18);
        label.Margin = quota ? new Thickness(0, 0, 6, 0) : new Thickness(0);
        amountView.HorizontalAlignment = quota ? HorizontalAlignment.Left : HorizontalAlignment.Stretch;
    }

    private void ShowFeedback(decimal change)
    {
        string format = Math.Abs(change) < 0.0001m ? "0.000000" : Math.Abs(change) < 0.01m ? "0.0000" : "0.00";
        string value = Math.Abs(change).ToString(format, CultureInfo.InvariantCulture);
        feedback.Text = (change > 0 ? "+" : "-") + "¥" + value;
        if (change < 0)
        {
            feedback.Visibility = Visibility.Collapsed;
            ShowDeduction("-" + value + "¥");
            return;
        }
        ClearDeductions();
        feedback.Foreground = new SolidColorBrush(Color.FromRgb(56, 220, 147));
        AnimateFeedback();
    }

    private void AnimateFeedback()
    {
        feedback.Visibility = Visibility.Visible;
        feedback.BeginAnimation(OpacityProperty, new DoubleAnimation(1, 0, TimeSpan.FromSeconds(2)));
        feedbackOffset.BeginAnimation(TranslateTransform.YProperty,
            new DoubleAnimation(12, -20, TimeSpan.FromSeconds(2)));
    }

    internal void ShowNextTo(Rect character, Rect work, DpiScale dpi, bool leftCorner)
    {
        // 窗口物理尺寸只随 DPI 变化，角色缩放不能放大余额或额度卡片。
        double width = Width * dpi.DpiScaleX;
        double height = Height * dpi.DpiScaleY;
        double x = leftCorner ? character.Right - character.Width * 0.25
            : character.Left - width + character.Width * 0.25;
        double y = character.Bottom - height + 8 * dpi.DpiScaleY;
        x = Math.Clamp(x, work.Left + 4, Math.Max(work.Left + 4, work.Right - width - 4));
        y = Math.Clamp(y, work.Top + 4, Math.Max(work.Top + 4, work.Bottom - height - 4));
        var handle = new WindowInteropHelper(this).EnsureHandle();
        void Place() => SetWindowPos(handle, IntPtr.Zero, (int)Math.Round(x), (int)Math.Round(y),
            (int)Math.Round(width), (int)Math.Round(height), 0x0004 | 0x0010);
        Place();
        if (!IsVisible) { Show(); Place(); }
    }

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
    private static extern int GetWindowLong(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongW")]
    private static extern int SetWindowLong(IntPtr window, int index, int value);
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
}
