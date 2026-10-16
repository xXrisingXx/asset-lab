using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

// Keep the native sizable window styles and nonclient hit targets. Windows, rather than
// a simulated drag loop, owns moving/resizing, Win+Arrow, and Windows 11 Snap Layouts.
internal sealed partial class Loader {
    int chromeHover;
    internal int CaptionWidth { get { return S(144); } }
    [StructLayout(LayoutKind.Sequential)] struct MouseTracking { public int size, flags; public IntPtr window; public uint hoverTime; }
    [DllImport("user32.dll")] static extern bool TrackMouseEvent(ref MouseTracking tracking);
    [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr window);
    [StructLayout(LayoutKind.Sequential)] struct NativeRect { public int left, top, right, bottom; }
    Rectangle CaptionButton(int hit) {
        int slot = hit == 20 ? 0 : hit == 9 ? 1 : 2;
        int y = Padding.Top + Math.Max(0, (desktop.HeaderHeight - S(42)) / 2);
        return new Rectangle(ClientSize.Width - Padding.Right - S(48) * (slot + 1), y, S(48), S(42));
    }
    int ChromeHit(Point p) {
        if (WindowState != FormWindowState.Maximized) {
            bool left = p.X < S(5), right = p.X >= ClientSize.Width - S(5), top = p.Y < S(5), bottom = p.Y >= ClientSize.Height - S(5);
            if (top) return left ? 13 : right ? 14 : 12;
            if (bottom) return left ? 16 : right ? 17 : 15;
            if (left) return 10; if (right) return 11;
        }
        foreach (int hit in new[] { 20, 9, 8 }) if (CaptionButton(hit).Contains(p)) return hit;
        return p.Y < S(42) ? 2 : 1;
    }
    void CaptionAction(int hit) {
        if (hit == 20) Close();
        if (hit == 8) WindowState = FormWindowState.Minimized;
        if (hit == 9) WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized;
        Invalidate();
    }
    void PaintChrome(Graphics g) {
        g.Clear(Color.FromArgb(20, 27, 37));
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        using (var line = new Pen(Line)) g.DrawLine(line, 0, Padding.Top + desktop.HeaderHeight - 1, ClientSize.Width, Padding.Top + desktop.HeaderHeight - 1);
        foreach (int hit in new[] { 8, 9, 20 }) {
            Rectangle r = CaptionButton(hit);
            if (chromeHover == hit) using (var brush = new SolidBrush(hit == 20 ? Color.FromArgb(186, 48, 63) : Panel2)) g.FillRectangle(brush, r);
            int x = r.X + r.Width / 2, y = r.Y + r.Height / 2, d = S(5);
            using (var pen = new Pen(Text1, Math.Max(1, S(1)))) {
                if (hit == 8) g.DrawLine(pen, x - d, y, x + d, y);
                else if (hit == 20) { g.DrawLine(pen, x - d, y - d, x + d, y + d); g.DrawLine(pen, x + d, y - d, x - d, y + d); }
                else {
                    if (WindowState == FormWindowState.Maximized) g.DrawRectangle(pen, x - d + S(3), y - d - S(3), d * 2, d * 2);
                    using (var fill = new SolidBrush(chromeHover == hit ? Panel2 : Color.FromArgb(20, 27, 37))) g.FillRectangle(fill, x - d, y - d, d * 2, d * 2);
                    g.DrawRectangle(pen, x - d, y - d, d * 2, d * 2);
                }
            }
        }
    }
    bool ChromeMessage(ref Message m) {
        if (m.Msg == 0xA0) {
            var tracking = new MouseTracking { size = Marshal.SizeOf(typeof(MouseTracking)), flags = 0x12, window = Handle };
            TrackMouseEvent(ref tracking);
        }
        if (m.Msg == 0x83 && m.WParam != IntPtr.Zero) { // WM_NCCALCSIZE
            // A maximized sizable HWND extends its invisible resize frame beyond the monitor.
            // Keep the custom client chrome inside the working area, just as a native caption is.
            if (IsZoomed(Handle)) {
                var area = Screen.FromHandle(Handle).WorkingArea;
                var rect = (NativeRect)Marshal.PtrToStructure(m.LParam, typeof(NativeRect));
                rect.left = Math.Max(rect.left, area.Left); rect.top = Math.Max(rect.top, area.Top);
                rect.right = Math.Min(rect.right, area.Right); rect.bottom = Math.Min(rect.bottom, area.Bottom);
                Marshal.StructureToPtr(rect, m.LParam, false);
            }
            m.Result = IntPtr.Zero; return true;
        }
        if (m.Msg == 0x84) { // WM_NCHITTEST: signed coordinates also work on monitors left of the primary.
            long packed = m.LParam.ToInt64();
            var p = PointToClient(new Point((short)(packed & 0xffff), (short)((packed >> 16) & 0xffff)));
            int hit = ChromeHit(p);
            if (chromeHover != hit) { chromeHover = hit; Invalidate(new Rectangle(0, 0, ClientSize.Width, Padding.Top + desktop.HeaderHeight)); }
            m.Result = new IntPtr(hit); return true;
        }
        if (m.Msg == 0xA1 && (m.WParam.ToInt32() == 8 || m.WParam.ToInt32() == 9 || m.WParam.ToInt32() == 20)) return true;
        if (m.Msg == 0xA2 && (m.WParam.ToInt32() == 8 || m.WParam.ToInt32() == 9 || m.WParam.ToInt32() == 20)) { CaptionAction(m.WParam.ToInt32()); return true; }
        if (m.Msg == 0x2A2) { chromeHover = 0; Invalidate(); }
        if (m.Msg == 0x24) { // WM_GETMINMAXINFO: custom chrome must respect the taskbar on each monitor.
            var area = Screen.FromHandle(Handle).WorkingArea; var screen = Screen.FromHandle(Handle).Bounds;
            var info = (MinMaxInfo)Marshal.PtrToStructure(m.LParam, typeof(MinMaxInfo));
            info.maxPosition = new Point(area.Left - screen.Left, area.Top - screen.Top);
            info.maxSize = area.Size; info.minTrack = new Size(S(500), S(400));
            Marshal.StructureToPtr(info, m.LParam, false); m.Result = IntPtr.Zero; return true;
        }
        return false;
    }
    [StructLayout(LayoutKind.Sequential)] struct MinMaxInfo { public Point reserved; public Size maxSize; public Point maxPosition; public Size minTrack, maxTrack; }
    void TestChrome() {
        var max = CaptionButton(9);
        if (ChromeHit(new Point(max.X + max.Width / 2, max.Y + max.Height / 2)) != 9) throw new Exception("Maximize has no native snap hit target");
        foreach (int hit in new[] {8,9,20}) {
            var button = CaptionButton(hit);
            var point = new Point(button.X + button.Width / 2 - desktop.Left, button.Y + button.Height / 2 - desktop.Top);
            if (desktop.Region == null || desktop.Region.IsVisible(point)) throw new Exception("WebView clips a window control at this display scale");
        }
        CaptionAction(9); if (WindowState != FormWindowState.Normal) throw new Exception("Restore failed");
        if (ChromeHit(new Point(1, S(100))) != 10 || ChromeHit(new Point(S(300), S(20))) != 2) throw new Exception("Resize or drag target failed");
        CaptionAction(8); if (WindowState != FormWindowState.Minimized) throw new Exception("Minimize failed");
        WindowState = FormWindowState.Normal; CaptionAction(9);
        if (WindowState != FormWindowState.Maximized) throw new Exception("Maximize failed");
        var client = new Rectangle(PointToScreen(Point.Empty), ClientSize);
        if (client != Screen.FromControl(this).WorkingArea) throw new Exception("Maximized client area does not fit the monitor: " + client);
        System.IO.File.WriteAllText(Program.DesktopSmoke + ".chrome.json", Program.Json.Serialize(new {
            minimize = true, restore = true, maximize = true, nativeSnapHitTarget = true,
            drag = true, resize = true, bounds = Bounds.ToString(), client = client.ToString(), workArea = Screen.FromControl(this).WorkingArea.ToString()
        }));
    }
}
