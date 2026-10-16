using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// The SDK is carried inside AssetLab.exe. Only the native loader needs a file;
// the Evergreen browser runtime is shared with other Windows applications.
internal static class DesktopDependencies {
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibrary(string path);
    internal static void Register() {
        AppDomain.CurrentDomain.AssemblyResolve += delegate(object sender, ResolveEventArgs e) {
            string name = new AssemblyName(e.Name).Name;
            if (name != "Microsoft.Web.WebView2.Core" && name != "Microsoft.Web.WebView2.WinForms") return null;
            using (var input = Program.Resource(name + ".dll"))
            using (var output = new MemoryStream()) { input.CopyTo(output); return Assembly.Load(output.ToArray()); }
        };
        string dir = Path.Combine(Program.Home, "desktop", "1.0.4258.31");
        Directory.CreateDirectory(dir);
        string file = Path.Combine(dir, "WebView2Loader.dll");
        using (var input = Program.Resource("WebView2Loader.dll"))
        using (var bytes = new MemoryStream()) {
            input.CopyTo(bytes);
            byte[] expected = bytes.ToArray();
            if (!File.Exists(file) || !System.Linq.Enumerable.SequenceEqual(File.ReadAllBytes(file), expected)) {
                string temp = file + "." + Guid.NewGuid().ToString("N") + ".tmp";
                File.WriteAllBytes(temp, expected);
                if (File.Exists(file)) File.Delete(file);
                File.Move(temp, file);
            }
        }
        if (LoadLibrary(file) == IntPtr.Zero) throw new InvalidOperationException("Could not load the desktop browser component.");
    }
}

internal sealed class DesktopHost : UserControl {
    internal static bool RuntimeReady() {
        try { return !String.IsNullOrEmpty(CoreWebView2Environment.GetAvailableBrowserVersionString()); }
        catch (WebView2RuntimeNotFoundException) { return false; }
    }
    internal static void InstallRuntime() {
        string file = Path.Combine(Program.Home, "downloads", "MicrosoftEdgeWebview2Setup.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(file));
        using (var client = new System.Net.WebClient()) client.DownloadFile("https://go.microsoft.com/fwlink/p/?LinkId=2124703", file);
        // The evergreen bootstrapper changes over time: verify Microsoft's Authenticode signature.
        var verify = new System.Diagnostics.ProcessStartInfo("powershell.exe") {
            UseShellExecute = false, CreateNoWindow = true,
            Arguments = "-NoProfile -NonInteractive -Command " + Program.Quote("$s=Get-AuthenticodeSignature -LiteralPath $env:ASSET_LAB_WEBVIEW_INSTALLER; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch '(^|, )O=Microsoft Corporation(,|$)') { exit 1 }")
        };
        verify.EnvironmentVariables["ASSET_LAB_WEBVIEW_INSTALLER"] = file;
        using (var check = System.Diagnostics.Process.Start(verify)) {
            if (!check.WaitForExit(30000) || check.ExitCode != 0) throw new InvalidOperationException("The WebView2 installer could not be verified as signed by Microsoft.");
        }
        using (var install = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(file, "/silent /install") { UseShellExecute = false, CreateNoWindow = true })) {
            if (!install.WaitForExit(300000)) throw new TimeoutException("WebView2 setup is still finishing. Reopen Asset Lab when it is done.");
        }
        if (!RuntimeReady()) throw new InvalidOperationException("WebView2 setup did not finish. Install Microsoft Edge WebView2 Runtime and reopen Asset Lab.");
    }
    readonly WebView2 view = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.FromArgb(16, 21, 30) };
    double headerCssHeight = 76, cssWidth = 0;
    internal int HeaderHeight { get { return (int)Math.Round(headerCssHeight * (cssWidth > 0 ? view.Width / cssWidth : DeviceDpi / 96.0)); } }
    internal DesktopHost() { Dock = DockStyle.Fill; BackColor = view.DefaultBackgroundColor; Controls.Add(view); Resize += (s,e) => UpdateCaptionRegion(); }
    internal void UpdateCaptionRegion() {
        var region = new Region(ClientRectangle);
        var owner = FindForm() as Loader;
        int width = owner != null ? owner.CaptionWidth : (int)Math.Round(144 * DeviceDpi / 96.0);
        region.Exclude(new Rectangle(Math.Max(0, Width - width), 0, width, HeaderHeight));
        var old = Region; Region = region; if (old != null) old.Dispose();
        if (FindForm() != null) FindForm().Invalidate();
    }
    internal async Task Open() {
        var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Program.Home, "desktop-profile"));
        await view.EnsureCoreWebView2Async(env);
        view.CoreWebView2.Settings.AreHostObjectsAllowed = false;
        view.CoreWebView2.Settings.IsWebMessageEnabled = true;
        view.CoreWebView2.Settings.IsNonClientRegionSupportEnabled = true;
        view.CoreWebView2.WebMessageReceived += (s,e) => {
            Uri source;
            if (!Uri.TryCreate(e.Source, UriKind.Absolute, out source) || source.GetLeftPart(UriPartial.Authority) != Program.Url) return;
            try {
                var data = Program.Json.Deserialize<System.Collections.Generic.Dictionary<string, object>>(e.WebMessageAsJson);
                if (Convert.ToString(data["kind"]) != "header") return;
                headerCssHeight = Math.Max(40, Math.Min(160, Convert.ToDouble(data["height"])));
                cssWidth = Math.Max(1, Convert.ToDouble(data["width"])); UpdateCaptionRegion();
            } catch (Exception) { }
        };
        await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(@"
document.addEventListener('DOMContentLoaded',()=>{
 const style=document.createElement('link');style.rel='stylesheet';style.href='/desktop.css';document.head.append(style);
 const bar=document.querySelector('.topbar');
 const report=()=>{if(bar)chrome.webview.postMessage({kind:'header',height:bar.getBoundingClientRect().height,width:innerWidth})};
 if(bar)new ResizeObserver(report).observe(bar);addEventListener('resize',report);report();
});");
        view.CoreWebView2.Settings.IsStatusBarEnabled = false;
        view.CoreWebView2.Settings.AreDevToolsEnabled = Program.DevRoot != null;
        view.CoreWebView2.PermissionRequested += (s, e) => {
            if (e.PermissionKind != CoreWebView2PermissionKind.ClipboardRead)
                e.State = CoreWebView2PermissionState.Deny;
        };
        view.CoreWebView2.NavigationStarting += (s, e) => {
            Uri dest;
            if (Uri.TryCreate(e.Uri, UriKind.Absolute, out dest) && dest.GetLeftPart(UriPartial.Authority) == Program.Url) return;
            e.Cancel = true;
            if (e.IsUserInitiated) External(e.Uri);
        };
        view.CoreWebView2.NewWindowRequested += (s, e) => { e.Handled = true; if (e.IsUserInitiated) External(e.Uri); };
        view.CoreWebView2.DownloadStarting += (s, e) => {
            using (var dialog = new SaveFileDialog { FileName = Path.GetFileName(e.ResultFilePath), OverwritePrompt = true }) {
                if (dialog.ShowDialog(FindForm()) != DialogResult.OK) e.Cancel = true;
                else { e.ResultFilePath = dialog.FileName; e.Handled = true; }
            }
        };
        var ready = new TaskCompletionSource<bool>();
        view.CoreWebView2.NavigationCompleted += (s, e) => {
            if (e.IsSuccess) ready.TrySetResult(true);
            else ready.TrySetException(new InvalidOperationException("The editor could not load: " + e.WebErrorStatus));
        };
        view.CoreWebView2.Navigate(Program.Url);
        if (await Task.WhenAny(ready.Task, Task.Delay(60000)) != ready.Task) throw new TimeoutException("The desktop editor took too long to open.");
        await ready.Task;
    }
    static void External(string address) {
        Uri uri;
        if (Uri.TryCreate(address, UriKind.Absolute, out uri) && (uri.Scheme == "https" || uri.Scheme == "http"))
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(address) { UseShellExecute = true });
    }
    internal async Task<bool> CanClose() {
        try {
            string dirty = await view.ExecuteScriptAsync("Boolean((typeof S !== 'undefined' && S.dirty) || (window.LabAudio && LabAudio.state().source && !LabAudio.state().saved))");
            if (dirty == "true") return MessageBox.Show(FindForm(), "This edit has not been saved. Close Asset Lab and discard the draft?", "Unsaved edit", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes;
        } catch (Exception) { }
        return true;
    }
    internal void ReloadEditor() { if (view.CoreWebView2 != null) view.Reload(); }
    internal async Task Smoke(string file) {
        string state = null;
        for (int i = 0; i < 200; i++) {
            state = await view.ExecuteScriptAsync("JSON.stringify({title:document.title,ready:!!document.querySelector('#assetList .asset-card'),editor:!!window.Ed,desktop:!!window.chrome.webview,oneBrand:document.querySelectorAll('.brand').length===1,headerFits:[...document.querySelectorAll('.topbar button')].every(b=>{const r=b.getBoundingClientRect();return !r.width||r.right<=innerWidth-144})})");
            string decoded = Program.Json.Deserialize<string>(state);
            var values = Program.Json.Deserialize<System.Collections.Generic.Dictionary<string, object>>(decoded);
            if (Convert.ToBoolean(values["ready"]) && Convert.ToBoolean(values["editor"])) {
                File.WriteAllText(file, decoded);
                using (var stream = File.Create(file + ".png")) await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream);
                if (!Convert.ToBoolean(values["headerFits"])) {
                    string layout = await view.ExecuteScriptAsync("JSON.stringify({width:innerWidth,buttons:[...document.querySelectorAll('.topbar button')].map(b=>({id:b.id,text:b.textContent,right:b.getBoundingClientRect().right,visible:b.checkVisibility()}))})");
                    File.WriteAllText(file + ".layout.json", Program.Json.Deserialize<string>(layout));
                    throw new InvalidOperationException("Header actions overlap window controls.");
                }
                return;
            }
            await Task.Delay(100);
        }
        throw new InvalidOperationException("Desktop smoke test did not reach the editor: " + state);
    }
}
