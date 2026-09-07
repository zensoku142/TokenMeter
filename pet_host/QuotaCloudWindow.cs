using System;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace TokenMeter.Pet;

internal sealed class QuotaCloudWindow : Window
{
    internal const double BaseWidth = 76;
    internal const double BaseHeight = 60;
    private readonly Ellipse statusDot = new() { Width = 6, Height = 6, HorizontalAlignment = HorizontalAlignment.Right,
        VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 35, 30, 0) };
    private readonly TextBlock primary = new() { FontSize = 23, FontWeight = FontWeights.Medium,
        TextAlignment = TextAlignment.Center };
    private readonly Path silhouette;
    private readonly Geometry cloudBody;
    private readonly Grid artwork = new();
    private readonly Path backWater = new() { Fill = new SolidColorBrush(Color.FromRgb(183, 208, 248)) };
    private readonly Path frontWater = new();
    private readonly Rectangle waterText;
    private readonly DispatcherTimer waveTimer = new() { Interval = TimeSpan.FromMilliseconds(40) };
    private readonly DispatcherTimer idleTimer = new() { Interval = TimeSpan.FromSeconds(3) };
    private readonly Stopwatch waveClock = new();
    private readonly StackPanel text = new() { Width = 132, VerticalAlignment = VerticalAlignment.Top };
    private readonly Viewbox surface;
    private Color accentColor = Color.FromRgb(47, 114, 232);
    private Color peakColor = Color.FromRgb(255, 176, 0);
    private Color textColor = Color.FromRgb(68, 81, 92);
    private Color borderColor = Color.FromRgb(216, 222, 229);
    private Color mutedColor = Colors.SlateGray;
    private Color warningColor = Color.FromRgb(177, 86, 53);
    private Color warningDotColor = Colors.DarkOrange;
    private Rect? lastBounds;
    private Point? lastPointerPosition;
    internal string PrimaryText => primary.Text;
    internal double DisplayOpacity => surface.Opacity;
    internal double? RemainingPercent { get; private set; }
    internal bool IsWaveRunning => waveTimer.IsEnabled;
    internal bool IsIdleTimerRunning => idleTimer.IsEnabled;
    internal Geometry LiquidGeometry => frontWater.Data;
    internal Geometry BubbleGeometry => silhouette.Data;
    internal Color OutlineColor => ((SolidColorBrush)silhouette.Stroke).Color;
    internal Color LiquidColor => ((LinearGradientBrush)frontWater.Fill).GradientStops[2].Color;
    internal Color SurfaceColor => ((SolidColorBrush)silhouette.Fill).Color;
    internal Color TextColor => ((SolidColorBrush)primary.Foreground).Color;

    public QuotaCloudWindow(bool demo, Action openPanel)
    {
        Title = "TokenMeter · 贴边额度云朵";
        Width = BaseWidth;
        Height = BaseHeight;
        WindowStyle = WindowStyle.None;
        ResizeMode = ResizeMode.NoResize;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        ShowInTaskbar = false;
        ShowActivated = false;
        Focusable = false;
        Topmost = true;
        cloudBody = Geometry.Parse("M 32,22 C 39,1 75,0 102,13 C 118,1 140,9 144,21 " +
            "C 177,20 184,49 158,57 C 161,75 134,83 111,70 " +
            "C 95,88 57,87 46,66 C 13,76 -9,48 11,30 C 17,25 24,22 32,22 Z").Clone();
        // Parse 返回只读几何；复制后只拉高主体，避免拉伸整个 Viewbox 导致数字和圆泡也变形。
        cloudBody.Transform = new ScaleTransform(1, 1.2);
        // 大圆瓣主体和两颗独立圆泡一起镜像、描边；液体只裁进主体，圆泡不会被当成额度水位。
        var drawing = new GeometryGroup();
        drawing.Children.Add(cloudBody);
        drawing.Children.Add(new EllipseGeometry(new Point(124, 117), 9, 9));
        drawing.Children.Add(new EllipseGeometry(new Point(150, 134), 5, 5));
        drawing.Freeze();
        silhouette = new Path { Data = drawing, Fill = Brushes.White,
            Stroke = new SolidColorBrush(Color.FromRgb(216, 222, 229)), StrokeThickness = 1,
            Effect = new DropShadowEffect { Color = Colors.Black, BlurRadius = 4, ShadowDepth = 1, Opacity = 0.06 } };
        var canvas = new Grid { Width = 176, Height = 140 };
        artwork.Children.Add(silhouette);
        var liquid = new Grid { Clip = cloudBody };
        liquid.Children.Add(backWater);
        liquid.Children.Add(frontWater);
        artwork.Children.Add(liquid);
        artwork.Children.Add(new Path { Data = cloudBody, Stroke = Brushes.White, StrokeThickness = 0.8, Opacity = 0.45 });
        canvas.Children.Add(artwork);
        // 金额长度不固定；只缩小数值这一行，不能把币种或尾数裁掉。
        text.Children.Add(new Viewbox { Stretch = Stretch.Uniform, StretchDirection = StretchDirection.DownOnly,
            Height = 38, Margin = new Thickness(0, 1, 0, 0), Child = primary });
        var textLayer = new Grid();
        textLayer.Children.Add(text);
        canvas.Children.Add(textLayer);
        // 与悬浮球一样，水下文字变白；直接复用文字的透明度蒙版，避免两份文字布局出现错位。
        waterText = new Rectangle { Fill = Brushes.White, OpacityMask = new VisualBrush(textLayer) {
                ViewboxUnits = BrushMappingMode.Absolute, Viewbox = new Rect(0, 0, 176, 140)
            },
            Effect = new DropShadowEffect { Color = Color.FromRgb(24, 52, 94), BlurRadius = 2, ShadowDepth = 0, Opacity = 0.5 } };
        canvas.Children.Add(waterText);
        canvas.Children.Add(statusDot);
        surface = new Viewbox { Child = canvas, Opacity = 1 };
        Content = surface;
        waveTimer.Tick += (_, _) => DrawWater();
        idleTimer.Tick += (_, _) => {
            idleTimer.Stop();
            if (IsVisible)
                surface.BeginAnimation(OpacityProperty, new DoubleAnimation(0.65, TimeSpan.FromMilliseconds(250)));
        };
        IsVisibleChanged += (_, _) => {
            UpdateWaveTimer();
            if (IsVisible) NotifyActivity();
            else
            {
                // 隐藏或关闭后取消淡化，不能让已排队的动画影响下次显示。
                idleTimer.Stop();
                surface.BeginAnimation(OpacityProperty, null);
                surface.Opacity = 1;
            }
        };
        Closed += (_, _) => { idleTimer.Stop(); waveTimer.Stop(); waveClock.Stop(); };
        MouseEnter += (_, _) => NotifyActivity();
        MouseMove += (_, e) => NotifyPointerMovement(PointToScreen(e.GetPosition(this)));
        MouseLeave += (_, _) => NotifyActivity();
        PreviewMouseDown += (_, _) => NotifyActivity();
        SourceInitialized += (_, _) => {
            var handle = new WindowInteropHelper(this).Handle;
            // 气泡本体需要接收双击，不能再全窗穿透；保留 NOACTIVATE，单击不会抢走当前应用焦点。
            SetWindowLong(handle, -20, (GetWindowLong(handle, -20) & ~0x20) | 0x08000000 | 0x80);
        };
        MouseDoubleClick += (_, e) => {
            if (e.ChangedButton != MouseButton.Left) return;
            e.Handled = true;
            openPanel();
        };
        SetTheme(default);
        SetUsage(demo ? "Codex · 演示数据" : "等待用量数据", demo ? "剩余 65%" : "--", "", "", false);
    }

    internal void SetTheme(JsonElement theme)
    {
        Color Read(string key, Color fallback) => ReadThemeColor(theme, key, fallback);
        accentColor = Read("accent", accentColor);
        peakColor = Read("peak", peakColor);
        // 旧主程序缺少这些可选字段时保留旧外观；新主程序让云朵底色、文字和提示一起跟随主题。
        silhouette.Fill = new SolidColorBrush(Read("surface", SurfaceColor));
        textColor = Read("text", textColor);
        borderColor = Read("border", borderColor);
        mutedColor = Read("subtext", mutedColor);
        warningColor = Read("warning", warningColor);
        warningDotColor = Read("warning", warningDotColor);
        var previous = frontWater.Fill as LinearGradientBrush;
        var gradient = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(0, 1) };
        gradient.GradientStops.Add(new GradientStop(Read("water_top", previous?.GradientStops[0].Color ?? Color.FromRgb(132, 174, 243)), 0));
        gradient.GradientStops.Add(new GradientStop(Read("accent_hover", previous?.GradientStops[1].Color ?? Color.FromRgb(78, 136, 237)), 0.2));
        gradient.GradientStops.Add(new GradientStop(accentColor, 0.64));
        gradient.GradientStops.Add(new GradientStop(Read("water_deep", previous?.GradientStops[3].Color ?? Color.FromRgb(34, 83, 168)), 1));
        gradient.Freeze();
        frontWater.Fill = gradient;
        backWater.Fill = new SolidColorBrush(Read("water_back", ((SolidColorBrush)backWater.Fill).Color));
        waterText.Fill = new SolidColorBrush(Read("on_accent", ((SolidColorBrush)waterText.Fill).Color));
    }

    internal static Color ReadThemeColor(JsonElement theme, string key, Color fallback)
    {
        // 两种额度窗口只接受 #RRGGBB；拒绝资源表达式或路径，非法颜色保留已有配色。
        if (theme.ValueKind == JsonValueKind.Object && theme.TryGetProperty(key, out var value) &&
            value.ValueKind == JsonValueKind.String && value.GetString() is { Length: 7 } hex && hex[0] == '#' &&
            int.TryParse(hex.AsSpan(1), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out int rgb))
            return Color.FromRgb((byte)(rgb >> 16), (byte)(rgb >> 8), (byte)rgb);
        return fallback;
    }

    internal void SetUsage(string provider, string value, string secondary, string status, bool warning, bool? pricingPeak = null)
    {
        bool hasStatus = !string.IsNullOrWhiteSpace(status);
        // 小云朵仅保留数值；刷新/异常用小圆点提示，完整说明仍在原额度窗口和辅助功能文本中。
        statusDot.Visibility = hasStatus ? Visibility.Visible : Visibility.Collapsed;
        statusDot.Fill = new SolidColorBrush(warning ? warningDotColor : mutedColor);
        text.Margin = new Thickness(0, 40, 0, 0);
        primary.Text = string.IsNullOrWhiteSpace(value) ? "--" : value;
        // 保留现有管道和独立额度窗口的文案，只在云朵里把旧格式“剩余 65%”简化成“65%”。
        string percent = primary.Text.StartsWith("剩余 ", StringComparison.Ordinal) ? primary.Text[3..] : primary.Text;
        RemainingPercent = null;
        if (percent.EndsWith('%'))
        {
            if (double.TryParse(percent.AsSpan(0, percent.Length - 1), NumberStyles.Float, CultureInfo.InvariantCulture,
                out double remaining) && double.IsFinite(remaining))
            {
                RemainingPercent = Math.Clamp(remaining, 0, 100);
                primary.Text = RemainingPercent.Value.ToString("0", CultureInfo.InvariantCulture) + "%";
            }
            else primary.Text = "--";
        }
        primary.Foreground = new SolidColorBrush(warning ? warningColor : textColor);
        // 外轮廓进一步缩小时保住百分比字号，避免小气泡虽然不遮挡却读不清核心数值。
        primary.FontSize = RemainingPercent.HasValue ? 32 : 23;
        bool showPricing = RemainingPercent == null && pricingPeak.HasValue &&
            provider.Split('·')[0].Trim().Equals("deepseek", StringComparison.OrdinalIgnoreCase);
        // 峰时使用当前主题的提醒色、平时使用强调色；关闭分时或切换账户后恢复中性描边。
        silhouette.Stroke = new SolidColorBrush(showPricing
            ? pricingPeak == true ? peakColor : accentColor
            : borderColor);
        silhouette.StrokeThickness = showPricing ? 2 : 1;
        string pricingLabel = showPricing ? pricingPeak == true ? "峰时" : "平时" : "";
        System.Windows.Automation.AutomationProperties.SetName(this, $"{provider} {primary.Text} {secondary} {status} {pricingLabel}");
        DrawWater();
        UpdateWaveTimer();
    }

    internal void NotifyActivity()
    {
        if (!IsVisible) return;
        idleTimer.Stop();
        surface.BeginAnimation(OpacityProperty, null);
        surface.Opacity = 1;
        idleTimer.Start();
    }

    internal void NotifyPointerMovement(Point position)
    {
        // 切帧可能让 WPF 重新派发同坐标的 MouseMove；只有实际移动才重置空闲时间。
        if (lastPointerPosition == position) return;
        lastPointerPosition = position;
        NotifyActivity();
    }

    internal void ShowNextTo(Rect pet, Rect work, bool? left, double scale, DpiScale dpi)
    {
        if ((artwork.RenderTransform as ScaleTransform)?.ScaleX != (left == true ? -1 : 1))
        {
            artwork.RenderTransform = new ScaleTransform(left == true ? -1 : 1, 1, 88, 70);
            DrawWater();
        }
        // 云朵只轻微跟随角色大小，放大桌宠不能把信息提示也变成一张大卡片。
        scale = Math.Clamp(scale, 0.95, 1.0);
        var bounds = BoundsFor(pet, work, new Size(BaseWidth * scale * dpi.DpiScaleX, BaseHeight * scale * dpi.DpiScaleY), left);
        bool moved = lastBounds != bounds;
        lastBounds = bounds;
        var handle = new WindowInteropHelper(this).EnsureHandle();
        // 与宿主拖拽一样使用物理像素定位，避免副屏原点或不同 DPI 让云朵偏到屏幕外。
        void Place() => SetWindowPos(handle, IntPtr.Zero, (int)Math.Round(bounds.X), (int)Math.Round(bounds.Y),
            (int)Math.Round(bounds.Width), (int)Math.Round(bounds.Height), 0x0004 | 0x0010);
        Place();
        if (!IsVisible) { Show(); Place(); }
        // 同一位置的动画重播不能刷新空闲时间，否则贴边循环会让气泡永远不淡化。
        if (moved) NotifyActivity();
    }

    private void UpdateWaveTimer()
    {
        // 云朵隐藏、显示金额或额度未知时不跑动画；0% 和 100% 也保持真实的全空/全满状态。
        if (IsVisible && RemainingPercent is > 0 and < 100)
        {
            waveClock.Start();
            waveTimer.Start();
        }
        else { waveTimer.Stop(); waveClock.Stop(); }
    }

    private void DrawWater()
    {
        double ratio = (RemainingPercent ?? 0) / 100;
        frontWater.Data = WaveGeometry(ratio, waveClock.Elapsed.TotalSeconds * 0.55, false);
        backWater.Data = WaveGeometry(ratio, waveClock.Elapsed.TotalSeconds * 0.55, true);
        var clip = new CombinedGeometry(GeometryCombineMode.Intersect, cloudBody, frontWater.Data) {
            Transform = artwork.RenderTransform
        };
        clip.Freeze();
        waterText.Clip = clip;
    }

    private Geometry WaveGeometry(double ratio, double phase, bool back)
    {
        if (ratio <= 0) return Geometry.Empty;
        var bounds = cloudBody.Bounds;
        if (ratio >= 1) return new RectangleGeometry(bounds);
        const int count = 14;
        var points = new Point[count];
        var offsets = new double[count];
        double mean = 0, shift = back ? 0.72 : 0;
        // 沿用 qt_ball 的三组缓慢行波和零均值处理；云朵不可拖动，因此不复制球体的拖拽物理模拟。
        for (int i = 0; i < count; i++)
        {
            double x = 120.0 * i / (count - 1);
            offsets[i] = Math.Sin(x * 0.047 - phase * 0.84 + shift) * 1.85 +
                Math.Sin(x * 0.030 + phase * 0.34 + 1.6 + shift * 0.63) * 0.55 +
                Math.Sin(x * 0.071 - phase * 0.22 + 3.1 - shift * 0.4) * 0.22;
            mean += offsets[i] / count;
        }
        // 两端收敛波幅，不能让极低额度凭空涨水或让满额度出现明显空洞。
        double amplitude = Math.Min(1, Math.Min(ratio, 1 - ratio) / 0.06) * (back ? 0.76 : 1);
        double y = bounds.Bottom - bounds.Height * ratio;
        for (int i = 0; i < count; i++)
            points[i] = new Point(176.0 * i / (count - 1),
                Math.Clamp(y + (offsets[i] - mean) * amplitude - (back ? amplitude : 0), bounds.Top, bounds.Bottom));
        var wave = new StreamGeometry();
        using (var path = wave.Open())
        {
            path.BeginFigure(points[0], true, true);
            for (int i = 0; i < count - 1; i++)
            {
                var previous = points[Math.Max(0, i - 1)];
                var after = points[Math.Min(count - 1, i + 2)];
                path.BezierTo(points[i] + (points[i + 1] - previous) / 6,
                    points[i + 1] - (after - points[i]) / 6, points[i + 1], true, true);
            }
            path.LineTo(new Point(176, bounds.Bottom), true, false);
            path.LineTo(new Point(0, bounds.Bottom), true, false);
        }
        wave.Freeze();
        return wave;
    }

    internal static Rect BoundsFor(Rect pet, Rect work, Size cloud, bool? left)
    {
        double width = Math.Min(cloud.Width, Math.Max(1, work.Width - 8));
        double height = Math.Min(cloud.Height, Math.Max(1, work.Height - 8));
        double y = pet.Top + pet.Height * 0.08 - height;
        double x;
        if (left is bool dockLeft)
        {
            // 顶部放不下时向屏幕内侧让位，不能把云朵直接压在角色脸上。
            double inset = y < work.Top + 4 ? pet.Width * 0.4 : 0;
            x = dockLeft ? work.Left + inset : work.Right - inset - width;
        }
        else
        {
            x = pet.Left + (pet.Width - width) / 2;
            if (y < work.Top + 4)
            {
                // 非贴边时通常居中跟随头顶；顶部空间不足则放到角色旁边。
                x = pet.Right + 4;
                if (x + width > work.Right) x = pet.Left - width - 4;
                y = pet.Top + pet.Height * 0.08;
            }
        }
        return new Rect(Math.Clamp(x, work.Left, Math.Max(work.Left, work.Right - width)),
            Math.Clamp(y, work.Top + 4, Math.Max(work.Top + 4, work.Bottom - height - 4)), width, height);
    }

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
    private static extern int GetWindowLong(IntPtr window, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongW")]
    private static extern int SetWindowLong(IntPtr window, int index, int value);
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
}
