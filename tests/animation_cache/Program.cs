using LinePutScript;
using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Threading;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using VPet_Simulator.Core;

internal static class Program
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

    [STAThread]
    private static int Main(string[] args)
    {
        return Run(args[0], args[1]);
    }

    private static int Run(string source, string cache)
    {
        GraphCore.CachePath = cache;
        using var core = new GraphCore(250, Dispatcher.CurrentDispatcher, new GraphCore.Config(new LpsDocument()));
        foreach (bool apng in new[] { false, true })
        {
            using IGraph animation = apng
                ? new APNGAnimation(core, Path.Combine(source, "animation.png"), new GraphInfo("cache-check"))
                : new PNGAnimation(core, source, Directory.GetFiles(source, "frame*.png").Order()
                    .Select(path => new FileInfo(path)).ToArray(), new GraphInfo("cache-check"));
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (!animation.IsReady && !animation.IsFail && DateTime.UtcNow < deadline) Thread.Sleep(10);
            Require(animation.IsReady, animation.FailMessage);
            var type = animation.GetType();
            var drawFrame = type.GetMethod("DrawFrame", Private)!;
            var image = new Image();
            BitmapSource Frame(int index)
            {
                drawFrame.Invoke(animation, new object[] { image, index });
                return (BitmapSource)image.Source;
            }
            var first = Frame(0);
            var pixels = Enumerable.Range(0, 3).Select(index => Pixels(Frame(index))).ToArray();
            for (int cycle = 0; cycle < 10; cycle++)
                for (int index = 0; index < pixels.Length; index++)
                {
                    var current = Frame(index);
                    Require(ReferenceEquals(current, first), "Loop reallocated the playback bitmap");
                    Require(Pixels(current).SequenceEqual(pixels[index]), "Loop changed frame pixels");
                }
            // 校验真实解码后的颜色，防止缓存复用掩盖错帧或 APNG 合成问题。
            for (int index = 0; index < 3; index++)
            {
                byte[] pixel = pixels[index];
                Require(pixel[2 - index] == 255 && pixel[3] == 255 &&
                    pixel.Take(3).Count(value => value == 0) == 2, "Unexpected RGBA frame pixels");
            }
            int[] durations = apng
                ? ((IEnumerable)type.GetField("FrameDurations", Private)!.GetValue(animation)!).Cast<int>().ToArray()
                : ((PNGAnimation)animation).Animations.Select(frame => frame.Time).ToArray();
            Require(durations.SequenceEqual(new[] { 40, 80, 120 }), "Frame timing changed");
            var atlas = type.GetField("SpriteSheetSource", Private)!;
            string atlasPath = apng ? (string)type.GetField("SpriteSheetPath", Private)!.GetValue(animation)! : animation.Path!;
            using (File.Open(atlasPath, FileMode.Open, FileAccess.Read, FileShare.None)) { }
            var sourceImage = atlas.GetValue(animation);
            animation.CleanupIdleCache(0);
            Require(ReferenceEquals(atlas.GetValue(animation), sourceImage), "Fresh image was discarded");
            // 清理不能打断正在播放的动画；停止后必须可释放，并能从磁盘恢复相同帧。
            var control = new IGraph.TaskControl();
            type.GetProperty("Control")!.SetValue(animation, control);
            animation.CleanupIdleCache(long.MaxValue);
            Require(ReferenceEquals(atlas.GetValue(animation), sourceImage), "Active image was discarded");
            control.Type = IGraph.TaskControl.ControlType.Status_Stoped;
            animation.CleanupIdleCache(long.MaxValue);
            Require(atlas.GetValue(animation) == null &&
                type.GetField("FramePixels", Private)!.GetValue(animation) == null,
                "Idle images were retained");
            Require(ReferenceEquals(Frame(0), first) && Pixels(Frame(0)).SequenceEqual(pixels[0]),
                "Cache reload changed pixels");
        }
        Console.WriteLine(JsonSerializer.Serialize(new { png = true, apng = true }));
        return 0;
    }

    private static byte[] Pixels(BitmapSource frame)
    {
        int stride = (frame.PixelWidth * frame.Format.BitsPerPixel + 7) / 8;
        byte[] pixels = new byte[stride * frame.PixelHeight];
        frame.CopyPixels(pixels, stride, 0);
        return pixels;
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
