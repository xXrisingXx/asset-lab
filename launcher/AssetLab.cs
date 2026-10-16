// Asset Lab launcher and bootstrapper: one exe that sets everything up, keeps it up to date, and runs the lab.
//
// From a source checkout (..\src\lab_server.py beside this exe) it runs that code directly. Anywhere else it installs
// into %LOCALAPPDATA%\AssetLab: the lab code it carries (or a newer GitHub release), a portable Python with numpy and
// Pillow, and on request the local AI runtime. Every download comes from its official source and is checked against
// the SHA-256 listed in bootstrap.json. Player data (workspaces, edits, marks) lives in the user's folder and is backed
// up, never replaced, by an update.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("Asset Lab")]
[assembly: AssemblyDescription("Asset Lab for Mitos.is")]
[assembly: AssemblyProduct("Asset Lab")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class Program {
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 64 * 1024 * 1024 };
    internal const string Name = "Asset Lab";
    internal static string ExeDir = AppDomain.CurrentDomain.BaseDirectory;
    internal static string Home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AssetLab");
    internal static string DevRoot;          // the source checkout, when this exe sits in its launcher folder
    internal static string Workspace, Game = "";
    internal static int Port = 8753;
    internal static bool NoBrowser, Offline;
    internal static string Smoke, Preview, DesktopSmoke;
    internal static string Url { get { return "http://127.0.0.1:" + Port; } }
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int command);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr FindWindow(string className, string title);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint message, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

    [STAThread] static int Main(string[] args) {
        try {
            SetProcessDPIAware();   // crisp text and shapes on scaled displays; sizes are scaled by hand
            bool stop = false, restart = false; int waitForExit = 0;
            for (int i = 0; i < args.Length; i++) {
                switch (args[i]) {
                    case "--port": Port = int.Parse(args[++i]); break;
                    case "--workspace": Workspace = Path.GetFullPath(args[++i]); break;
                    case "--game": Game = Path.GetFullPath(args[++i]); break;
                    case "--home": Home = Path.GetFullPath(args[++i]); break;
                    case "--wait-for-exit": waitForExit = int.Parse(args[++i]); break;
                    case "--no-browser": NoBrowser = true; break;
                    case "--offline": Offline = true; break;
                    case "--stop": stop = true; break;
                    case "--restart": restart = true; break;
                    case "--smoke-test": Smoke = Path.GetFullPath(args[++i]); NoBrowser = true; break;
                    case "--desktop-smoke": DesktopSmoke = Smoke = Path.GetFullPath(args[++i]); break;
                    case "--preview": Preview = Path.GetFullPath(args[++i]); break;   // render the screens to PNGs and exit
                    case "--window": case "--background": break;  // older shortcuts
                    default: throw new ArgumentException("Unknown option: " + args[i]);
                }
            }
            if (Port < 1024 || Port > 65535) throw new ArgumentException("Use a port from 1024 to 65535.");
            string dev = Path.GetFullPath(Path.Combine(ExeDir, ".."));
            if (File.Exists(Path.Combine(dev, "src", "lab_server.py"))) DevRoot = dev;
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            try { File.Delete(Assembly.GetExecutingAssembly().Location + ".old"); } catch (Exception) { }   // left by a self-update
            if (stop) { StopServer(); return 0; }
            if (restart) StopServer();
            bool first;
            if (waitForExit != 0) try { using (var previous = Process.GetProcessById(waitForExit)) if (!previous.WaitForExit(30000)) throw new TimeoutException("The previous Asset Lab is still closing."); } catch (ArgumentException) { }
            using (Mutex mutex = new Mutex(true, Smoke != null ? "Local\\AssetLab.Test." + Port : "Local\\AssetLab.Desktop", out first)) {
                if (!first) {
                    // Already open: bring the desktop window forward.
                    IntPtr h = FindWindow(null, Smoke != null ? Name + " - " + Port : Name);
                    if (h != IntPtr.Zero) { PostMessage(h, 0x8001, !NoBrowser && LabSettings.OpenBrowserOnStart ? new IntPtr(1) : IntPtr.Zero, IntPtr.Zero); }
                    return 0;
                }
                Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
                DesktopDependencies.Register();
                using (Loader window = new Loader()) Application.Run(window);
                mutex.ReleaseMutex();
            }
            return Loader.ExitCode;
        } catch (Exception e) {
            if (Array.IndexOf(args, "--stop") >= 0) Console.Error.WriteLine(e.Message);
            else MessageBox.Show(e.Message, Name, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    internal static string Quote(string value) {
        // CommandLineToArgvW-compatible quoting, including trailing backslashes.
        StringBuilder result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append(c); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2); return result.Append('"').ToString();
    }
    internal static Dictionary<string, object> Request(string endpoint, string token) {
        HttpWebRequest r = (HttpWebRequest)WebRequest.Create(Url + endpoint);
        r.Proxy = null; r.Timeout = 4000; r.ReadWriteTimeout = 4000;
        if (token != null) { r.Method = "POST"; r.Headers["X-Lab-Token"] = token; r.ContentLength = 0; }
        try {
            using (WebResponse response = r.GetResponse())
            using (StreamReader stream = new StreamReader(response.GetResponseStream()))
                return Json.Deserialize<Dictionary<string, object>>(stream.ReadToEnd());
        } catch (WebException e) {
            if (e.Response != null) {
                using (var response = e.Response)
                using (var reader = new StreamReader(response.GetResponseStream())) {
                    var error = Json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                    if (error.ContainsKey("error")) throw new InvalidOperationException(Convert.ToString(error["error"]), e);
                }
            }
            throw;
        }
    }
    internal static Dictionary<string, object> Health() {
        try {
            var h = Request("/api/health", null);
            string app = Convert.ToString(h["app"]);
            if (app != Name && app != "Mitosis Asset Lab") throw new InvalidOperationException("Port " + Port + " belongs to a different application.");
            return h;
        } catch (WebException e) {
            if (e.Status == WebExceptionStatus.ConnectFailure || e.Status == WebExceptionStatus.Timeout) return null;
            throw;
        }
    }
    internal static void StopServer(Process owned = null) {
        // Waiting for our process avoids slow TCP retries against a socket that has already closed.
        if (owned != null) {
            if (owned.HasExited) return;
            var status = Request("/api/status", null);
            Request("/api/shutdown", Convert.ToString(status["token"]));
            if (!owned.WaitForExit(30000)) throw new TimeoutException("Asset Lab is still finishing pending work. Please try closing again shortly.");
            return;
        }
        var h = Health(); if (h == null) return;
        var s = Request("/api/status", null); Request("/api/shutdown", Convert.ToString(s["token"]));
        for (int i = 0; i < 120; i++) { Thread.Sleep(250); if (Health() == null) return; }
        throw new InvalidOperationException("Still finishing a request. Asset Lab will stop after pending work completes.");
    }
    internal static void OpenBrowser() { Process.Start(new ProcessStartInfo(Url) { UseShellExecute = true }); }
    internal static Stream Resource(string name) { return Assembly.GetExecutingAssembly().GetManifestResourceStream(name); }
}

// ----- setup: versions, downloads, Python, updates --------------------------------------------------------------
internal static class Setup {
    internal delegate void Progress(string status, string detail, double fraction);
    static bool updating;

    static bool SafeVersion(string value) {
        return !String.IsNullOrEmpty(value) && value != "." && value != ".." && value.All(c => Char.IsLetterOrDigit(c) || c == '.' || c == '-' || c == '_');
    }
    // Flush the new file before atomically publishing it. A killed updater leaves either complete version.
    static void AtomicText(string path, string value) {
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        string temporary = path + ".new";
        using (var f = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None)) {
            byte[] bytes = Encoding.UTF8.GetBytes(value); f.Write(bytes, 0, bytes.Length); f.Flush(true);
        }
        RetryFile(() => { if (File.Exists(path)) File.Replace(temporary, path, null); else File.Move(temporary, path); });
    }
    // Deliberate process-kill checkpoints are enabled only for isolated smoke tests with a loopback release feed.
    static void UpdateCheckpoint(string point) {
        Uri feed;
        if (!updating || Program.Smoke == null || Environment.GetEnvironmentVariable("ASSET_LAB_TEST_UPDATE_POINT") != point ||
            !Uri.TryCreate(Environment.GetEnvironmentVariable("ASSET_LAB_RELEASES"), UriKind.Absolute, out feed) || !feed.IsLoopback) return;
        AtomicText(Path.Combine(Program.Home, "update-checkpoint.json"), Program.Json.Serialize(new { point = point, pid = Process.GetCurrentProcess().Id }));
        Thread.Sleep(Timeout.Infinite); // The fault harness kills this process, then starts a fresh one.
    }
    internal static void RecoverUpdate() {
        string journal = Path.Combine(Program.Home, "pending-update.json");
        if (!File.Exists(journal)) return;
        var pending = Program.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(journal));
        string next = Convert.ToString(pending["version"]), previous = Convert.ToString(pending["previous"]);
        if (!SafeVersion(next) || (previous.Length > 0 && !SafeVersion(previous))) throw new InvalidDataException("Invalid update recovery version.");
        string expected = Convert.ToString(pending["exeSha"]);
        bool completed = (expected.Length == 0 || Sha256(Assembly.GetExecutingAssembly().Location) == expected)
            && File.Exists(Path.Combine(Program.Home, "app", next, "src", "lab_server.py"));
        string chosen = completed ? next : previous;
        string pointer = Path.Combine(Program.Home, "app", "current.txt");
        if (chosen.Length > 0) AtomicText(pointer, chosen); else if (File.Exists(pointer)) File.Delete(pointer);
        AtomicText(Path.Combine(Program.Home, "last-update-recovery.json"), Program.Json.Serialize(new { version = chosen, completed = completed }));
        File.Delete(journal);
    }

    internal static string AppDir {
        get {
            if (Program.DevRoot != null) return Program.DevRoot;
            string v = InstalledVersion();
            return v == null ? null : Path.Combine(Program.Home, "app", v);
        }
    }
    internal static string RuntimeDir { get { return Program.DevRoot != null ? Path.Combine(Program.DevRoot, "runtime") : Path.Combine(Program.Home, "runtime"); } }
    internal static string DataHome {
        get {
            string env = Environment.GetEnvironmentVariable("ASSET_LAB_HOME");
            if (!String.IsNullOrEmpty(env)) return env;
            string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            string older = Path.Combine(profile, "MitosisAssetLab");
            return Directory.Exists(older) ? older : Path.Combine(profile, "AssetLab");
        }
    }
    internal static string Version {
        get {
            string dir = AppDir;
            string file = dir == null ? null : Path.Combine(dir, "VERSION");
            return file != null && File.Exists(file) ? File.ReadAllText(file).Trim() : EmbeddedVersion();
        }
    }

    static string Read(Stream s) { if (s == null) return null; using (var r = new StreamReader(s)) return r.ReadToEnd(); }
    internal static string EmbeddedVersion() { var v = Read(Program.Resource("VERSION")); return v == null ? "0" : v.Trim(); }
    internal static string InstalledVersion() {
        string file = Path.Combine(Program.Home, "app", "current.txt");
        if (!File.Exists(file)) return null;
        string v = File.ReadAllText(file).Trim();
        return SafeVersion(v) && File.Exists(Path.Combine(Program.Home, "app", v, "src", "lab_server.py")) ? v : null;
    }
    internal static int Compare(string a, string b) {
        int[] x = Parts(a), y = Parts(b);
        for (int i = 0; i < Math.Max(x.Length, y.Length); i++) {
            int p = i < x.Length ? x[i] : 0, q = i < y.Length ? y[i] : 0;
            if (p != q) return p.CompareTo(q);
        }
        return 0;
    }
    static int[] Parts(string v) {
        return (v ?? "0").TrimStart('v', 'V').Split('.').Select(s => { int n; return int.TryParse(new string(s.TakeWhile(Char.IsDigit).ToArray()), out n) ? n : 0; }).ToArray();
    }

    internal static Dictionary<string, object> Manifest() {
        string dir = AppDir;
        string text = dir != null && File.Exists(Path.Combine(dir, "bootstrap.json")) ? File.ReadAllText(Path.Combine(dir, "bootstrap.json")) : Read(Program.Resource("bootstrap.json"));
        return Program.Json.Deserialize<Dictionary<string, object>>(text);
    }
    internal static Dictionary<string, object> D(object o) { return (Dictionary<string, object>)o; }
    internal static IEnumerable<Dictionary<string, object>> List(object o) { return ((System.Collections.ArrayList)o).Cast<Dictionary<string, object>>(); }

    internal static string Sha256(string file) {
        using (var sha = SHA256.Create()) using (var s = File.OpenRead(file))
            return BitConverter.ToString(sha.ComputeHash(s)).Replace("-", "").ToLowerInvariant();
    }

    /// Download with resume into dest, verified against sha256. Already-present verified files are kept.
    internal static void Download(string url, string dest, string sha, long size, string label, Progress report, CancellationToken cancel) {
        Directory.CreateDirectory(Path.GetDirectoryName(dest));
        if (File.Exists(dest) && (size <= 0 || new FileInfo(dest).Length == size) && Sha256(dest) == sha) return;
        string part = dest + ".part";
        for (int attempt = 1; ; attempt++) {
            try {
                long have = File.Exists(part) ? new FileInfo(part).Length : 0;
                if (size > 0 && have > size) { File.Delete(part); have = 0; }
                if (size <= 0 || have < size) {
                    var request = (HttpWebRequest)WebRequest.Create(url);
                    request.UserAgent = "AssetLab"; request.Timeout = 30000; request.ReadWriteTimeout = 60000;
                    if (have > 0) request.AddRange(have);
                    using (var response = (HttpWebResponse)request.GetResponse()) {
                        if (have > 0 && response.StatusCode != HttpStatusCode.PartialContent) have = 0;  // server ignored the range
                        long total = size > 0 ? size : have + response.ContentLength;
                        using (var input = response.GetResponseStream())
                        using (var output = new FileStream(part, have > 0 ? FileMode.Append : FileMode.Create)) {
                            byte[] buffer = new byte[1 << 20]; int n; long done = have; var clock = Stopwatch.StartNew(); long mark = have;
                            while ((n = input.Read(buffer, 0, buffer.Length)) > 0) {
                                cancel.ThrowIfCancellationRequested();
                                output.Write(buffer, 0, n); done += n;
                                output.Flush(); UpdateCheckpoint("download");
                                if (clock.ElapsedMilliseconds > 250) {
                                    double speed = (done - mark) / Math.Max(0.001, clock.Elapsed.TotalSeconds);
                                    report(label, String.Format("{0} of {1} · {2}/s", Size(done), Size(total), Size((long)speed)), total > 0 ? (double)done / total : 0);
                                    clock.Restart(); mark = done;
                                }
                            }
                        }
                    }
                }
                report(label, L.T("Checking the file…"), 1);
                if (Sha256(part) != sha) { File.Delete(part); throw new InvalidDataException(label + ": the download did not match its checksum."); }
                if (File.Exists(dest)) File.Delete(dest);
                File.Move(part, dest);
                return;
            } catch (WebException) {
                if (attempt >= 4) throw;
                Thread.Sleep(2000 * attempt);
            }
        }
    }
    internal static string Size(long bytes) {
        if (bytes >= 1L << 30) return (bytes / (double)(1L << 30)).ToString("0.00") + " GB";
        if (bytes >= 1L << 20) return (bytes / (double)(1L << 20)).ToString("0.0") + " MB";
        return (bytes / 1024.0).ToString("0") + " KB";
    }

    static void Extract(string zip, string folder) {
        string full = Path.GetFullPath(folder) + Path.DirectorySeparatorChar;
        using (var archive = ZipFile.OpenRead(zip)) {
            foreach (var entry in archive.Entries) {
                string target = Path.GetFullPath(Path.Combine(folder, entry.FullName));
                if (!target.StartsWith(full, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Unsafe path in " + Path.GetFileName(zip));
                if (entry.FullName.EndsWith("/")) { Directory.CreateDirectory(target); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                entry.ExtractToFile(target, true);
                UpdateCheckpoint("extract");
            }
        }
    }
    static void Replace(string staging, string target) {
        string home = Path.GetFullPath(Program.Home).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!Path.GetFullPath(staging).StartsWith(home, StringComparison.OrdinalIgnoreCase) || !Path.GetFullPath(target).StartsWith(home, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("The setup folder must stay inside Asset Lab's installation folder.");
        string backup = target + ".previous-" + Guid.NewGuid().ToString("N");
        if (Directory.Exists(target)) RetryFile(() => Directory.Move(target, backup));
        try { RetryFile(() => Directory.Move(staging, target)); }
        catch { if (Directory.Exists(backup)) RetryFile(() => Directory.Move(backup, target)); throw; }
        if (Directory.Exists(backup)) try { Directory.Delete(backup, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }
    static void RetryFile(Action action) {
        for (int i = 0; ; i++) {
            try { action(); return; }
            catch (IOException) { if (i == 19) throw; }
            catch (UnauthorizedAccessException) { if (i == 19) throw; }
            Thread.Sleep(300);
        }
    }

    /// The lab code this exe carries, unless a same-or-newer version is already installed.
    internal static void EnsureApp(Progress report) {
        if (Program.DevRoot != null) return;
        string embedded = EmbeddedVersion(), installed = InstalledVersion();
        if (installed != null && Compare(installed, embedded) >= 0) return;
        report(L.T("Unpacking Asset Lab") + " " + embedded, "", 0.5);
        string zip = Path.Combine(Program.Home, "downloads", "app-" + embedded + ".zip");
        Directory.CreateDirectory(Path.GetDirectoryName(zip));
        using (var s = Program.Resource("app.zip")) using (var f = File.Create(zip)) s.CopyTo(f);
        InstallApp(zip, embedded);
    }
    static void InstallApp(string zip, string version) {
        if (!SafeVersion(version)) throw new InvalidDataException("Invalid release version.");
        string root = Path.Combine(Program.Home, "app"), staging = Path.Combine(root, version + ".new");
        if (Directory.Exists(staging)) Directory.Delete(staging, true);
        Extract(zip, staging);
        if (!File.Exists(Path.Combine(staging, "src", "lab_server.py"))) throw new InvalidDataException("The update package is incomplete.");
        File.WriteAllText(Path.Combine(staging, "VERSION"), version);
        Replace(staging, Path.Combine(root, version));
        string previous = InstalledVersion();
        UpdateCheckpoint("before-pointer");
        AtomicText(Path.Combine(root, "current.txt"), version);
        UpdateCheckpoint("after-pointer");
        // Keep the version just replaced for a manual rollback; older ones go.
        foreach (string dir in Directory.GetDirectories(root)) {
            string name = Path.GetFileName(dir);
            if (name != version && name != previous) try { Directory.Delete(dir, true); } catch (IOException) { }
        }
        File.Delete(zip);
    }

    internal static string PythonExe() {
        if (Program.DevRoot != null) {
            string dev = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), @".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe");
            if (File.Exists(dev)) return dev;
        }
        return Path.Combine(Program.Home, "python", "python.exe");
    }
    static string PythonStamp(Dictionary<string, object> m) {
        var parts = new List<string> { Convert.ToString(D(m["python"])["sha256"]) };
        parts.AddRange(List(m["wheels"]).Select(w => Convert.ToString(w["sha256"])));
        return String.Join(",", parts);
    }
    internal static bool PythonReady(Dictionary<string, object> m) {
        string exe = PythonExe();
        if (!File.Exists(exe)) return false;
        if (Program.DevRoot != null && !exe.StartsWith(Program.Home, StringComparison.OrdinalIgnoreCase)) return true;
        string stamp = Path.Combine(Program.Home, "python", ".ready");
        return File.Exists(stamp) && File.ReadAllText(stamp).Trim() == PythonStamp(m);
    }
    /// Official embeddable Python plus the numpy and Pillow wheels, unpacked into Lib\site-packages.
    internal static void EnsurePython(Dictionary<string, object> m, Progress report, CancellationToken cancel) {
        if (PythonReady(m)) return;
        string downloads = Path.Combine(Program.Home, "downloads"), staging = Path.Combine(Program.Home, "python.new");
        var py = D(m["python"]);
        string zip = Path.Combine(downloads, "python-" + py["version"] + ".zip");
        Download(Convert.ToString(py["url"]), zip, Convert.ToString(py["sha256"]), Convert.ToInt64(py["size"]), L.T("Downloading Python") + " " + py["version"], report, cancel);
        var wheels = new List<string>();
        foreach (var w in List(m["wheels"])) {
            string file = Path.Combine(downloads, Path.GetFileName(new Uri(Convert.ToString(w["url"])).LocalPath));
            Download(Convert.ToString(w["url"]), file, Convert.ToString(w["sha256"]), Convert.ToInt64(w["size"]), "Downloading " + w["name"], report, cancel);
            wheels.Add(file);
        }
        report(L.T("Setting up Python"), "", 0.95);
        if (Directory.Exists(staging)) Directory.Delete(staging, true);
        Extract(zip, staging);
        string site = Path.Combine(staging, "Lib", "site-packages");
        Directory.CreateDirectory(site);
        foreach (string w in wheels) Extract(w, site);
        string pth = Directory.GetFiles(staging, "python3*._pth").FirstOrDefault();
        if (pth == null) throw new InvalidDataException("The Python package has no ._pth file.");
        File.AppendAllText(pth, Environment.NewLine + @"Lib\site-packages" + Environment.NewLine);
        var check = Process.Start(new ProcessStartInfo(Path.Combine(staging, "python.exe"), "-c \"import numpy, PIL\"") { CreateNoWindow = true, UseShellExecute = false });
        check.WaitForExit(60000);
        if (check.ExitCode != 0) throw new InvalidOperationException("The portable Python could not load numpy and Pillow.");
        File.WriteAllText(Path.Combine(staging, ".ready"), PythonStamp(m));
        Replace(staging, Path.Combine(Program.Home, "python"));
        foreach (string f in new[] { zip }.Concat(wheels)) try { File.Delete(f); } catch (IOException) { }
    }

    internal static List<Dictionary<string, object>> MissingRuntime(Dictionary<string, object> m) {
        var missing = new List<Dictionary<string, object>>();
        foreach (var item in List(m["runtime"])) {
            string path = Path.Combine(RuntimeDir, Convert.ToString(item["file"]).Replace('/', '\\'));
            string unpacked = null;
            string file = Convert.ToString(item["file"]);
            if (file.Contains("ollama-windows")) unpacked = Path.Combine(RuntimeDir, "ollama", "ollama.exe");
            if (file.Contains("sd-master")) unpacked = Path.Combine(RuntimeDir, "sd", "sd-cli.exe");
            bool present = File.Exists(path) && new FileInfo(path).Length == Convert.ToInt64(item["size"]);
            if (!present && !(unpacked != null && File.Exists(unpacked))) missing.Add(item);
        }
        return missing;
    }
    internal static long Bytes(IEnumerable<Dictionary<string, object>> items) { return items.Sum(i => Convert.ToInt64(i["size"])); }
    internal static void DownloadRuntime(Dictionary<string, object> m, Progress report, CancellationToken cancel) {
        var items = MissingRuntime(m);
        long total = Bytes(items), before = 0;
        foreach (var item in items) {
            long size = Convert.ToInt64(item["size"]); long start = before;
            string name = Path.GetFileName(Convert.ToString(item["file"]));
            Download(Convert.ToString(item["url"]), Path.Combine(RuntimeDir, Convert.ToString(item["file"]).Replace('/', '\\')), Convert.ToString(item["sha256"]), size,
                     L.T("Downloading the local AI"), report == null ? null : (Progress)((s, d, f) => report(s, name + " · " + d, (start + f * size) / total)), cancel);
            before += size;
        }
    }

    // ----- updates: newest GitHub release of the repo in bootstrap.json -------------------------------------
    internal sealed class Release { public string Version, Notes, AppUrl, AppSha, ExeUrl, ExeSha; public long AppSize, ExeSize; }

    internal static Release Latest(Dictionary<string, object> m) {
        string repo = m.ContainsKey("repo") ? Convert.ToString(m["repo"]) : "";
        // ASSET_LAB_RELEASES points at another release feed (used to test updates locally).
        string feed = Environment.GetEnvironmentVariable("ASSET_LAB_RELEASES");
        if (feed == null && (Program.DevRoot != null || Program.Offline || repo.StartsWith("OWNER/") || !repo.Contains("/"))) return null;
        try {
            var request = (HttpWebRequest)WebRequest.Create(feed ?? "https://api.github.com/repos/" + repo + "/releases/latest");
            request.UserAgent = "AssetLab"; request.Accept = "application/vnd.github+json"; request.Timeout = 6000; request.ReadWriteTimeout = 6000;
            Dictionary<string, object> json;
            using (var response = request.GetResponse()) using (var r = new StreamReader(response.GetResponseStream())) json = Program.Json.Deserialize<Dictionary<string, object>>(r.ReadToEnd());
            var release = new Release { Version = Convert.ToString(json["tag_name"]).TrimStart('v', 'V'), Notes = json.ContainsKey("body") ? Convert.ToString(json["body"]) : "" };
            foreach (var a in List(json["assets"])) {
                string name = Convert.ToString(a["name"]), digest = a.ContainsKey("digest") && a["digest"] != null ? Convert.ToString(a["digest"]) : "";
                if (!digest.StartsWith("sha256:")) continue;  // only assets GitHub has fingerprinted
                if (name == "asset-lab-app.zip") { release.AppUrl = Convert.ToString(a["browser_download_url"]); release.AppSha = digest.Substring(7); release.AppSize = Convert.ToInt64(a["size"]); }
                if (name == "AssetLab.exe") { release.ExeUrl = Convert.ToString(a["browser_download_url"]); release.ExeSha = digest.Substring(7); release.ExeSize = Convert.ToInt64(a["size"]); }
            }
            return release.AppUrl != null && Compare(release.Version, Version) > 0 ? release : null;
        } catch (Exception) { return null; }  // offline, private repo, rate limit: run what is installed
    }

    /// Back up the player's settings, install the new code, and swap this exe if the release has a newer one.
    /// Returns true when the exe itself was replaced, so the caller restarts it.
    internal static bool ApplyUpdate(Release release, Progress report, CancellationToken cancel) {
        if (!SafeVersion(release.Version)) throw new InvalidDataException("Invalid release version.");
        updating = true;
        try {
        BackupData(Version);
        string zip = Path.Combine(Program.Home, "downloads", "asset-lab-" + release.Version + ".zip");
        Download(release.AppUrl, zip, release.AppSha, release.AppSize, L.T("Downloading Asset Lab") + " " + release.Version, report, cancel);
        string self = Assembly.GetExecutingAssembly().Location;
        bool replaceExe = release.ExeUrl != null && Sha256(self) != release.ExeSha;
        string fresh = self + ".new";
        // Validate BOTH packages before changing the active installation.
        if (replaceExe) Download(release.ExeUrl, fresh, release.ExeSha, release.ExeSize, L.T("Updating the launcher"), report, cancel);
        cancel.ThrowIfCancellationRequested();
        AtomicText(Path.Combine(Program.Home, "pending-update.json"), Program.Json.Serialize(new {
            version = release.Version, previous = InstalledVersion() ?? "", exeSha = replaceExe ? release.ExeSha : "" }));
        report(L.T("Installing Asset Lab") + " " + release.Version, L.T("Your workspace and edits are kept."), 1);
        InstallApp(zip, release.Version);
        if (replaceExe) {
            // ReplaceFile preserves a runnable EXE at the original path even if the updater is killed.
            RetryFile(() => File.Replace(fresh, self, self + ".old"));
            UpdateCheckpoint("after-exe");
        }
        File.Delete(Path.Combine(Program.Home, "pending-update.json"));
        return replaceExe;
        } catch { RecoverUpdate(); throw; }
        finally { updating = false; }
    }
    /// Small settings files only (the image revisions are never touched by updates): workspace state, theme history,
    /// favorites, names and removed assets, into %LOCALAPPDATA%\AssetLab\backups. The newest ten are kept.
    internal static void BackupData(string version) {
        string home = DataHome; if (!Directory.Exists(home)) return;
        string target = Path.Combine(Program.Home, "backups", DateTime.Now.ToString("yyyyMMdd-HHmmss") + "-before-update-from-" + version);
        foreach (string file in Directory.GetFiles(home, "*.json").Concat(Directory.Exists(Path.Combine(home, "workspace")) ? Directory.GetFiles(Path.Combine(home, "workspace"), "*.json") : new string[0])) {
            if (Path.GetFileName(file) == "audit-cache.json") continue;   // rebuilt automatically; not player data
            string rel = file.Substring(home.Length).TrimStart('\\');
            string dest = Path.Combine(target, rel);
            Directory.CreateDirectory(Path.GetDirectoryName(dest));
            File.Copy(file, dest, true);
        }
        string root = Path.Combine(Program.Home, "backups");
        if (Directory.Exists(root)) foreach (string old in Directory.GetDirectories(root).OrderByDescending(d => d).Skip(10)) try { Directory.Delete(old, true); } catch (IOException) { }
    }
}

// ----- what this PC has, for the local AI's requirements ---------------------------------------------------------
internal static class Specs {
    internal const long NeedRam = 16L << 30, MinRam = 12L << 30, GoodVram = 6L << 30;
    [StructLayout(LayoutKind.Sequential)] struct Memory { public uint Length, Load; public ulong Total, Free, TotalPage, FreePage, TotalVirtual, FreeVirtual, Extended; }
    [DllImport("kernel32.dll")] static extern bool GlobalMemoryStatusEx(ref Memory m);
    internal static long Ram() { var m = new Memory { Length = (uint)Marshal.SizeOf(typeof(Memory)) }; return GlobalMemoryStatusEx(ref m) ? (long)m.Total : 0; }
    /// The largest graphics card memory Windows reports (the registry holds the full 64-bit size; WMI caps at 4 GB).
    internal static long Vram(out string name) {
        long best = 0; name = "";
        try {
            using (var cls = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}")) {
                if (cls == null) return 0;
                foreach (string sub in cls.GetSubKeyNames()) {
                    using (var k = cls.OpenSubKey(sub)) {
                        if (k == null) continue;
                        object q = null; try { q = k.GetValue("HardwareInformation.qwMemorySize"); } catch (Exception) { }
                        long size = q is long ? (long)q : q is byte[] && ((byte[])q).Length >= 8 ? BitConverter.ToInt64((byte[])q, 0) : 0;
                        if (size > best) { best = size; name = Convert.ToString(k.GetValue("DriverDesc")); }
                    }
                }
            }
        } catch (Exception) { }
        return best;
    }
    internal static long FreeDisk(string folder) {
        try { return new DriveInfo(Path.GetPathRoot(Path.GetFullPath(folder))).AvailableFreeSpace; } catch (Exception) { return long.MaxValue; }
    }
    internal static string Gb(long b) { return (b / (double)(1L << 30)).ToString(b >= 10L << 30 ? "0" : "0.#") + " GB"; }
}
// ----- the window: logo intro, setup steps, then a small running panel ------------------------------------------
internal sealed partial class Loader : Form {
    internal static int ExitCode;
    static readonly Color Bg = Color.FromArgb(16, 21, 30), Panel2 = Color.FromArgb(30, 40, 53), Line = Color.FromArgb(48, 61, 78),
        Text1 = Color.FromArgb(232, 238, 246), Muted = Color.FromArgb(146, 164, 184), Cyan = Color.FromArgb(34, 211, 238), Pink = Color.FromArgb(244, 114, 182),
        Bad = Color.FromArgb(248, 113, 113);
    readonly float k;                 // display scale: 1 at 100 %, 1.75 at 175 %
    readonly int W, H, LogoBig, LogoSmall;
    int S(double v) { return (int)Math.Round(v * k); }
    readonly Image logo;
    readonly System.Windows.Forms.Timer frame = new System.Windows.Forms.Timer { Interval = 15 };
    readonly Stopwatch clock = new Stopwatch();
    readonly Label title = new Label(), status = new Label(), detail = new Label(), version = new Label();
    readonly Bar bar = new Bar();
    readonly FlatButton primary = new FlatButton(true), secondary = new FlatButton(false), close = new FlatButton(false), minimize = new FlatButton(false),
        ai = new FlatButton(false), lang = new FlatButton(false);
    readonly TextBox log = new TextBox();
    readonly CancellationTokenSource cancel = new CancellationTokenSource();
    Rectangle full;
    double intro;        // 0..1 logo pop and spin, 1..2 window grows
    bool introDone, closing, allowClose, running;
    Process child; ProcessGroup children;
    DesktopHost desktop;
    TaskCompletionSource<bool> choice;
    Dictionary<string, object> manifest;

    public Loader() {
        Text = Program.Smoke != null ? Program.Name + " - " + Program.Port : Program.Name;
        using (var probe = CreateGraphics()) k = probe.DpiX / 96f;
        W = S(560); H = S(356); LogoBig = S(132); LogoSmall = S(46);
        using (var s = Program.Resource("logo.png")) logo = s == null ? null : Image.FromStream(s);
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        FormBorderStyle = FormBorderStyle.None; StartPosition = FormStartPosition.Manual; BackColor = Bg; ForeColor = Text1;
        Font = new Font("Segoe UI", 10f); DoubleBuffered = true; ResizeRedraw = true; ShowInTaskbar = true;
        var screen = Screen.PrimaryScreen.WorkingArea;
        full = new Rectangle(screen.X + (screen.Width - W) / 2, screen.Y + (screen.Height - H) / 2, W, H);
        Bounds = Centered(LogoBig + S(24), LogoBig + S(24));
        TransparencyKey = Color.FromArgb(1, 2, 3); BackColor = TransparencyKey;   // only the logo shows during the intro

        title.Text = "Asset Lab"; title.Font = new Font("Segoe UI Semibold", 19f); title.AutoSize = true;
        status.Font = new Font("Segoe UI Semibold", 11f); status.AutoSize = false; status.Text = L.T("Starting…");
        detail.ForeColor = Muted; detail.Font = new Font("Segoe UI", 9f); detail.AutoSize = false;
        version.ForeColor = Muted; version.Font = new Font("Segoe UI", 8f); version.AutoSize = true; version.TextAlign = ContentAlignment.MiddleRight;
        close.Text = "✕"; minimize.Text = "–";
        log.Multiline = true; log.ReadOnly = true; log.ScrollBars = ScrollBars.None; log.BackColor = Color.FromArgb(12, 16, 23); log.ForeColor = Muted;
        log.BorderStyle = BorderStyle.None; log.Font = new Font("Consolas", 8.5f); log.Visible = false;
        foreach (Control c in new Control[] { title, status, detail, version, bar, primary, secondary, ai, close, minimize, lang, log }) { c.Visible = false; Controls.Add(c); }
        lang.Text = L.Code.ToUpperInvariant(); lang.Font = new Font("Segoe UI Semibold", 8.5f); lang.Click += LangClick; ai.Click += AiClick;
        Layout2();

        close.Click += delegate { Close(); };
        minimize.Click += delegate { WindowState = FormWindowState.Minimized; };
        MouseDown += Drag; title.MouseDown += Drag; status.MouseDown += Drag; detail.MouseDown += Drag;
        frame.Tick += delegate { Animate(); };
        Shown += delegate { if (Program.Preview != null) RenderPreviews(); else { clock.Start(); frame.Start(); } };
        if (Program.Preview != null) { Opacity = 0; ShowInTaskbar = false; }
        FormClosing += OnClosing;
        FormClosed += delegate { if (children != null) children.Dispose(); cancel.Dispose(); };
    }

    Rectangle Centered(int w, int h) { return new Rectangle(full.X + (full.Width - w) / 2, full.Y + (full.Height - h) / 2, w, h); }
    void Layout2() {
        title.Location = new Point(S(28) + LogoSmall + S(12), S(26));
        close.SetBounds(W - S(44), S(12), S(32), S(28)); minimize.SetBounds(W - S(78), S(12), S(32), S(28)); lang.SetBounds(W - S(124), S(12), S(42), S(28));
        status.SetBounds(S(28), S(110), W - S(56), S(26)); detail.SetBounds(S(28), S(138), W - S(56), S(84));
        bar.SetBounds(S(28), S(232), W - S(56), S(8));
        primary.SetBounds(S(28), S(254), S(200), S(40)); secondary.SetBounds(S(238), S(254), S(120), S(40)); ai.SetBounds(S(368), S(254), W - S(396), S(40));
        version.Location = new Point(W - S(24) - version.PreferredWidth, H - S(30));
        log.SetBounds(S(28), S(304), W - S(140), S(40));
    }

    // ----- intro: pop and spin, then grow into the loader --------------------------------------------------------
    static double EaseOutBack(double t) { const double c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.Pow(t - 1, 3) + c1 * Math.Pow(t - 1, 2); }
    static double EaseInOut(double t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.Pow(-2 * t + 2, 3) / 2; }
    void Animate() {
        double ms = clock.Elapsed.TotalMilliseconds;
        if (!introDone) {
            intro = Math.Min(2, ms < 650 ? ms / 650 : 1 + (ms - 850) / 420);
            if (ms >= 850 && ms < 1270) {
                double g = EaseInOut(Math.Max(0, intro - 1));
                if (BackColor != Bg) { BackColor = Bg; TransparencyKey = Color.Empty; }
                var start = Centered(LogoBig + S(24), LogoBig + S(24));
                Bounds = new Rectangle((int)(start.X + (full.X - start.X) * g), (int)(start.Y + (full.Y - start.Y) * g),
                                       (int)(start.Width + (full.Width - start.Width) * g), (int)(start.Height + (full.Height - start.Height) * g));
                Round();
            }
            if (ms >= 1270) {
                introDone = true; Bounds = full; Round();
                foreach (Control c in new Control[] { title, status, detail, bar, close, minimize, lang, version }) c.Visible = true;
                version.Text = "v" + Setup.Version; Layout2();
                Run();
            }
            Invalidate();
            return;
        }
        if (bar.Busy) bar.Invalidate();
        if (!bar.Busy && !running) frame.Stop();
    }
    void Round() {
        using (var path = Rounded(new Rectangle(0, 0, Width, Height), S(introDone || intro > 1 ? 18 : 30))) Region = new Region(path);
    }
    internal static GraphicsPath Rounded(Rectangle r, int radius) {
        var p = new GraphicsPath(); int d = radius * 2;
        p.AddArc(r.X, r.Y, d, d, 180, 90); p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90); p.AddArc(r.X, r.Bottom - d, d, d, 90, 90); p.CloseFigure(); return p;
    }
    protected override void OnPaint(PaintEventArgs e) {
        if (desktop != null) { PaintChrome(e.Graphics); return; }
        var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        if (logo == null) return;
        if (intro < 1) {
            // Pop: grow from small with a little overshoot while spinning one full turn.
            double t = intro, scale = Math.Max(0.05, EaseOutBack(t)), angle = 360 * (1 - Math.Pow(1 - t, 3));
            float size = (float)(LogoBig * scale);
            var state = g.Save();
            g.TranslateTransform(Width / 2f, Height / 2f); g.RotateTransform((float)angle);
            g.DrawImage(logo, -size / 2, -size / 2, size, size);
            g.Restore(state);
            return;
        }
        // Grow: the logo glides from the middle to its place in the header, shrinking as it goes.
        double p = introDone ? 1 : EaseInOut(Math.Min(1, intro - 1));
        float s2 = (float)(LogoBig + (LogoSmall - LogoBig) * p);
        float cx = (float)(Width / 2.0 + ((S(28) + LogoSmall / 2.0) - Width / 2.0) * p), cy = (float)(Height / 2.0 + ((S(22) + LogoSmall / 2.0) - Height / 2.0) * p);
        g.DrawImage(logo, cx - s2 / 2, cy - s2 / 2, s2, s2);
        if (introDone) using (var pen = new Pen(Line)) g.DrawLine(pen, S(28), S(94), W - S(28), S(94));
    }
    void Drag(object sender, MouseEventArgs e) {
        if (e.Button != MouseButtons.Left) return;
        ReleaseCapture(); SendMessage(Handle, 0xA1, (IntPtr)2, IntPtr.Zero);
    }
    [DllImport("user32.dll")] static extern bool ReleaseCapture();
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);

    // Draws each screen to a PNG (for documentation and checks) without showing a window.
    void RenderPreviews() {
        Directory.CreateDirectory(Program.Preview);
        introDone = true; intro = 2; Bounds = full; BackColor = Bg; TransparencyKey = Color.Empty;
        foreach (Control c in new Control[] { title, status, detail, bar, close, minimize, lang, version }) c.Visible = true;
        version.Text = "v" + Setup.Version; Layout2();
        Action<string> snap = name => {
            using (var bmp = new Bitmap(Width, Height)) { DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height)); bmp.Save(Path.Combine(Program.Preview, name + ".png")); }
        };
        var choosing = ChooseLanguage(); snap("0-language"); foreach (var b in languageButtons) { Controls.Remove(b); b.Dispose(); } languageButtons.Clear();
        Report("Downloading the local AI", "flux-2-klein-4b-Q4_0.gguf · 1.02 GB of 2.29 GB · 38.4 MB/s", 0.44); snap("1-downloading");
        string yes; var all = Setup.List(Setup.Manifest()["runtime"]).ToList();
        Ask(AiQuestion(all, out yes), AiDetails(all), yes, L.T("Not now")); snap("2-question");
        primary.Visible = secondary.Visible = false; Running(); ai.Text = L.T("Get the local AI"); ai.Visible = true; snap("3-running"); ai.Visible = false;
        Fail("Asset Lab stopped while starting. The details are below."); log.Text = "Traceback (most recent call last):\r\n  ..."; snap("4-error");
        allowClose = true; Close();
    }
    // ----- first start: pick a language (the one Windows uses is the bright button) ---------------------------------
    readonly List<FlatButton> languageButtons = new List<FlatButton>();
    Task ChooseLanguage() {
        var done = new TaskCompletionSource<bool>();
        status.Text = "Language · Idioma · Langue · Sprache"; status.ForeColor = Text1; detail.Text = L.T("You can change it any time in Asset Lab's Settings.");
        bar.Visible = primary.Visible = secondary.Visible = false;
        int gap = S(8), w = (W - S(56) - gap * (L.Codes.Length - 1)) / L.Codes.Length;
        for (int i = 0; i < L.Codes.Length; i++) {
            string code = L.Codes[i];
            var b = new FlatButton(code == L.Code) { Text = L.Names[i] };
            b.SetBounds(S(28) + i * (w + gap), S(250), w, S(44));
            b.Click += delegate {
                L.Code = code; L.MarkAsked(); lang.Text = code.ToUpperInvariant();
                foreach (var x in languageButtons) { Controls.Remove(x); x.Dispose(); }
                languageButtons.Clear(); done.TrySetResult(true);
            };
            languageButtons.Add(b); Controls.Add(b); b.BringToFront();
        }
        return done.Task;
    }
    // ----- steps ---------------------------------------------------------------------------------------------
    void Report(string s, string d, double f) {
        if (IsDisposed) return;
        if (InvokeRequired) { BeginInvoke((Action)(() => Report(s, d, f))); return; }
        status.Text = s; status.ForeColor = Text1; detail.Text = d; bar.Value = f; bar.Busy = f < 0; bar.Visible = true; bar.Invalidate();
        if (bar.Busy && !frame.Enabled) frame.Start();
    }
    void Write(string line) {
        if (IsDisposed) return;
        if (InvokeRequired) { BeginInvoke((Action)(() => Write(line))); return; }
        log.AppendText(line + Environment.NewLine);
    }
    Task<bool> Ask(string question, string info, string yes, string no) {
        status.Text = question; detail.Text = info; bar.Visible = false;
        primary.Text = yes; secondary.Text = no; primary.Visible = secondary.Visible = true;
        choice = new TaskCompletionSource<bool>();
        EventHandler onYes = null, onNo = null;
        onYes = delegate { primary.Click -= onYes; secondary.Click -= onNo; primary.Visible = secondary.Visible = false; choice.TrySetResult(true); };
        onNo = delegate { primary.Click -= onYes; secondary.Click -= onNo; primary.Visible = secondary.Visible = false; choice.TrySetResult(false); };
        primary.Click += onYes; secondary.Click += onNo;
        return choice.Task;
    }

    async void Run() {
        try {
            if (!L.Asked && Program.Smoke == null) await ChooseLanguage();
            bool updates = LabSettings.CheckUpdates;   // Settings > General > Check for updates when Asset Lab starts
            Report(L.T(updates ? "Checking for updates…" : "Starting…"), "", -1);
            await Task.Run(() => Setup.RecoverUpdate());
            manifest = await Task.Run(() => Setup.Manifest());
            await Task.Run(() => Setup.EnsureApp(Report));
            manifest = await Task.Run(() => Setup.Manifest());
            var release = updates ? await Task.Run(() => Setup.Latest(manifest)) : null;
            bool auto = Environment.GetEnvironmentVariable("ASSET_LAB_AUTO_UPDATE") == "1";   // tests only
            if (release != null && (Program.Smoke == null || auto)) {
                string notes = (release.Notes ?? "").Split('\n').Select(l => l.Trim()).FirstOrDefault(l => l.Length > 0) ?? "";
                if (auto || await Ask("Asset Lab " + release.Version + " " + L.T("is available"), L.T("You have") + " " + Setup.Version + ". " + (notes.Length > 90 ? notes.Substring(0, 90) + "…" : notes) +
                              "\n" + L.T("Your workspace and edits are kept, and a backup of your settings is made first."), L.T("Update now"), L.T("Not now"))) {
                    bool newExe = await Task.Run(() => Setup.ApplyUpdate(release, Report, cancel.Token));
                    if (newExe) {
                        // The new launcher takes over with the same options; this one steps aside.
                        Process.Start(Application.ExecutablePath, String.Join(" ", Environment.GetCommandLineArgs().Skip(1).Select(Program.Quote)) + " --wait-for-exit " + Process.GetCurrentProcess().Id);
                        allowClose = true; Close(); return;
                    }
                    manifest = await Task.Run(() => Setup.Manifest());
                    version.Text = "v" + Setup.Version;
                }
            }
            if (!Setup.PythonReady(manifest)) {
                Report(L.T("Preparing Python"), L.T("First start only: about 30 MB from python.org and PyPI."), 0);
                await Task.Run(() => Setup.EnsurePython(manifest, Report, cancel.Token));
            }
            var missing = Setup.MissingRuntime(manifest);
            string yes;
            string declined = Path.Combine(Program.Home, "ai-declined");
            if (missing.Count > 0 && !File.Exists(declined) && Program.Smoke == null) {
                if (await Ask(AiQuestion(missing, out yes), AiDetails(missing), yes, L.T("Not now"))) {
                    await Task.Run(() => Setup.DownloadRuntime(manifest, Report, cancel.Token));
                } else { Directory.CreateDirectory(Program.Home); File.WriteAllText(declined, DateTime.Now.ToString("s")); }
            }
            if (!Program.NoBrowser && !DesktopHost.RuntimeReady()) {
                if (Program.Offline || Program.Smoke != null) throw new InvalidOperationException("The desktop window needs Microsoft Edge WebView2 Runtime. Install it, then reopen Asset Lab.");
                if (!await Ask("Prepare the desktop window", "Asset Lab needs Microsoft's WebView2 Runtime. Download its small setup program from Microsoft and install the runtime?", "Install WebView2", "Exit")) { allowClose = true; Close(); return; }
                Report("Installing the desktop runtime…", "Downloading from Microsoft and checking its signature. This may take a few minutes.", -1);
                await Task.Run(() => DesktopHost.InstallRuntime());
            }
            await StartServer(Program.DesktopSmoke != null || LabSettings.OpenBrowserOnStart);
        } catch (OperationCanceledException) {
        } catch (Exception e) {
            Fail(e.Message);
        }
    }
    // The one-time AI question, with this PC's memory, graphics card and disk against what the models need.
    string AiQuestion(List<Dictionary<string, object>> missing, out string yes) {
        bool weak = Specs.Ram() < Specs.MinRam || Specs.FreeDisk(Setup.RuntimeDir) < Setup.Bytes(missing) + (1L << 30);
        yes = L.T(weak ? "Download anyway" : "Download");
        return L.T(weak ? "This PC is below the local AI's needs" : "Download the local AI?");
    }
    string AiDetails(List<Dictionary<string, object>> missing) {
        long ram = Specs.Ram(), size = Setup.Bytes(missing), disk = Specs.FreeDisk(Setup.RuntimeDir); string gpu; long vram = Specs.Vram(out gpu);
        string needs = L.T("Needs 16 GB RAM (this PC") + " " + Specs.Gb(ram) + ") " + L.T("and") + " " + Specs.Gb(size + (1L << 30)) + " " + L.T("free disk") + " (" + Specs.Gb(disk) + " " + L.T("free") + ").";
        string speed = vram >= Specs.GoodVram ? (gpu.Length > 0 ? gpu : L.T("Graphics card")) + ", " + Specs.Gb(vram) + ": " + L.T("about 35 s per image.")
                     : vram > 0 ? L.T("Graphics card") + " " + Specs.Gb(vram) + " " + L.T("(6 GB+ recommended): images may take minutes.")
                     : L.T("No graphics card memory found: images will take several minutes.");
        return Setup.Size(size) + " " + L.T("once: the smart Theme AI planner and image redesigns, all on this PC.") + "\n" + needs + "\n" + speed +
               "\n" + L.T("Without it, Theme AI still works from keywords.");
    }
    void Fail(string message) {
        if (IsDisposed) return;
        if (Program.Smoke != null) {
            ExitCode = 1;
            File.WriteAllText(Program.Smoke, Program.Json.Serialize(new { passed = false, error = message }));
            allowClose = true; Close(); return;
        }
        if (desktop != null) { MessageBox.Show(this, message, Program.Name, MessageBoxButtons.OK, MessageBoxIcon.Error); return; }
        status.Text = L.T("Something went wrong"); status.ForeColor = Bad; detail.Text = message; bar.Visible = false; bar.Busy = false;
        log.Visible = true; primary.Text = L.T("Try again"); primary.Visible = true; secondary.Visible = false; ai.Visible = false;
        EventHandler retry = null; retry = delegate { primary.Click -= retry; primary.Visible = false; log.Visible = false; Run(); };
        primary.Click += retry;
        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
    }

    async Task StartServer(bool openBrowser) {
        Report(L.T("Starting Asset Lab…"), "", -1);
        var h = await Task.Run(() => Program.Health());
        if (h == null) {
            if (child != null && !child.HasExited) await Task.Run(() => child.WaitForExit(30000));
            if (child != null) { child.Dispose(); child = null; } if (children != null) { children.Dispose(); children = null; }
            string app = Setup.AppDir, python = Setup.PythonExe();
            string args = "-B -X utf8 " + Program.Quote(Path.Combine(app, "src", "lab_server.py")) + " --no-browser --port " + Program.Port +
                          (Program.Workspace != null ? " --workspace " + Program.Quote(Program.Workspace) : "") + (String.IsNullOrEmpty(Program.Game) ? "" : " --game " + Program.Quote(Program.Game));
            var info = new ProcessStartInfo(python, args) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = Path.Combine(app, "src") };
            info.EnvironmentVariables["ASSET_LAB_LAUNCHER"] = "1";
            info.EnvironmentVariables["ASSET_LAB_RUNTIME"] = Setup.RuntimeDir;
            info.EnvironmentVariables["ASSET_LAB_VERSION"] = Setup.Version;
            child = new Process { StartInfo = info, EnableRaisingEvents = true };
            child.OutputDataReceived += (s, e) => { if (e.Data != null) Write(e.Data); };
            child.ErrorDataReceived += (s, e) => { if (e.Data != null) Write(e.Data); };
            child.Exited += OnServerExit;
            child.Start(); children = new ProcessGroup(child); child.BeginOutputReadLine(); child.BeginErrorReadLine();
            for (int i = 0; i < 240; i++) {
                await Task.Delay(250);
                if (child.HasExited) throw new InvalidOperationException(L.T("Asset Lab stopped while starting. The details are below."));
                h = await Task.Run(() => Program.Health()); if (h != null) break;
            }
            if (h == null) throw new InvalidOperationException(L.T("Starting is taking longer than expected. The details are below."));
        }
        Running();
        if (Program.Smoke != null && Program.DesktopSmoke == null) { await SmokeTest(); return; }
        if (desktop != null) { desktop.ReloadEditor(); return; }
        if (!openBrowser) return;
        await OpenDesktop();
    }
    void Running() {
        running = true; bar.Busy = false; bar.Value = 1; bar.Visible = false;
        status.Text = L.T("Asset Lab is running"); status.ForeColor = Text1;
        detail.Text = Program.Url + "\n" + L.T("Closing this window stops Asset Lab. Your work is saved in") + " " + Setup.DataHome + ".";
        primary.Text = L.T("Open Asset Lab"); secondary.Text = L.T("Stop"); primary.Visible = secondary.Visible = true;
        primary.Click -= OpenClick; secondary.Click -= StopClick; primary.Click += OpenClick; secondary.Click += StopClick;
        var m = manifest == null ? null : Setup.MissingRuntime(manifest);
        ai.Text = L.T("Get the local AI"); ai.Visible = m != null && m.Count > 0;
        if (ai.Visible) { detail.Text += "\n" + L.T("The local AI is not downloaded yet."); }
    }
    // Get the local AI later, from the running window (players who said "Not now" at the first start).
    async void AiClick(object s, EventArgs e) {
        var missing = manifest == null ? null : Setup.MissingRuntime(manifest); if (missing == null || missing.Count == 0) { Running(); return; }
        ai.Visible = false; string yes;
        if (await Ask(AiQuestion(missing, out yes), AiDetails(missing), yes, L.T("Not now"))) {
            try { if (!frame.Enabled) frame.Start(); await Task.Run(() => Setup.DownloadRuntime(manifest, Report, cancel.Token)); try { File.Delete(Path.Combine(Program.Home, "ai-declined")); } catch (Exception) { } }
            catch (OperationCanceledException) { return; } catch (Exception x) { Fail(x.Message); return; }
        }
        Running();
    }
    // The launcher's language (saved in settings.json for the lab too); the visible words change at once.
    void LangClick(object s, EventArgs e) {
        var menu = new ContextMenuStrip();
        for (int i = 0; i < L.Codes.Length; i++) {
            string code = L.Codes[i];
            var item = new ToolStripMenuItem(L.Names[i]) { Checked = code == L.Code };
            item.Click += delegate { L.Code = code; lang.Text = code.ToUpperInvariant(); if (running) Running(); };
            menu.Items.Add(item);
        }
        menu.Show(lang, new Point(0, lang.Height));
    }
    bool openingDesktop;
    async Task OpenDesktop() {
        if (closing || IsDisposed || openingDesktop) return;
        if (desktop != null) { WindowState = FormWindowState.Normal; Activate(); return; }
        openingDesktop = true;
        Report("Opening your workspace…", "", -1);
        DesktopHost next = new DesktopHost { Visible = false };
        Controls.Add(next);
        try { await next.Open(); }
        catch { Controls.Remove(next); next.Dispose(); throw; }
        finally { openingDesktop = false; }
        if (closing || IsDisposed) { next.Dispose(); return; }
        desktop = next;
        frame.Stop();
        foreach (Control control in Controls) control.Visible = false;
        TransparencyKey = Color.Empty; BackColor = Bg; Region = null;
        FormBorderStyle = FormBorderStyle.Sizable;
        Padding = new Padding(S(5));
        MinimumSize = new Size(S(500), S(400));
        int screenIndex;
        var screen = Program.DesktopSmoke != null && int.TryParse(Environment.GetEnvironmentVariable("ASSET_LAB_TEST_SCREEN"), out screenIndex)
            && screenIndex >= 0 && screenIndex < Screen.AllScreens.Length ? Screen.AllScreens[screenIndex] : Screen.FromControl(this);
        var area = screen.WorkingArea;
        MaximizedBounds = area;
        Bounds = new Rectangle(area.X + 30, area.Y + 30, Math.Min(1600, area.Width - 60), Math.Min(1000, area.Height - 60));
        desktop.Visible = true; desktop.BringToFront();
        WindowState = FormWindowState.Maximized;
        desktop.UpdateCaptionRegion();
        if (Program.DesktopSmoke != null) {
            TestChrome();
            await desktop.Smoke(Program.DesktopSmoke);
            using (var capture = new Bitmap(ClientSize.Width, ClientSize.Height)) {
                using (var graphics = Graphics.FromImage(capture)) {
                    PaintChrome(graphics);
                    using (var content = Image.FromFile(Program.DesktopSmoke + ".png")) graphics.DrawImage(content, desktop.Bounds);
                    graphics.SetClip(new Rectangle(ClientSize.Width - Padding.Right - S(144), Padding.Top, S(144), desktop.HeaderHeight));
                    PaintChrome(graphics);
                }
                capture.Save(Program.DesktopSmoke + ".window.png");
            }
            WindowState = FormWindowState.Normal;
            Bounds = new Rectangle(area.X, area.Y, area.Width / 2, area.Height);
            await Task.Delay(500);
            await desktop.Smoke(Program.DesktopSmoke + ".half");
            Close(); // Exercise the same confirmation, hide, and shutdown path as the close button.
        }
    }
    async void OpenClick(object s, EventArgs e) { try { await OpenDesktop(); } catch (Exception x) { Fail(x.Message); } }
    async void StopClick(object s, EventArgs e) { if (await Stop()) { allowClose = true; Close(); } }
    void OnServerExit(object sender, EventArgs e) {
        if (IsDisposed) return;
        int code; try { code = ((Process)sender).ExitCode; } catch (InvalidOperationException) { return; }
        BeginInvoke((Action)(async () => {
            if (closing || sender != child) return;
            if (code == 75) { running = false; await StartServer(false); return; }   // the lab asked for a restart with new code
            if (code == 0) { allowClose = true; Close(); return; }
            if (desktop != null) { MessageBox.Show(this, "The Asset Lab backend stopped. Please reopen Asset Lab.\n" + log.Text, Program.Name, MessageBoxButtons.OK, MessageBoxIcon.Error); allowClose = true; Close(); return; }
            running = false; status.Text = L.T("Asset Lab stopped"); detail.Text = code == 0 ? "" : L.T("It exited with code") + " " + code + ". " + L.T("The details are below.");
            log.Visible = code != 0; primary.Text = L.T("Start again"); primary.Visible = true; secondary.Visible = false; ai.Visible = false;
            primary.Click -= OpenClick; EventHandler again = null; again = async delegate { primary.Click -= again; log.Visible = false; await StartServer(LabSettings.OpenBrowserOnStart); }; primary.Click += again;
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
        }));
    }
    async Task<bool> Stop() {
        status.Text = L.T("Stopping after pending work…"); primary.Visible = secondary.Visible = ai.Visible = false;
        try {
            await Task.Run(() => Program.StopServer(child));
            running = false; return true;
        } catch (Exception e) { Show(); Activate(); Fail(e.Message); return false; }
    }
    async void OnClosing(object sender, FormClosingEventArgs e) {
        if (allowClose) return;
        e.Cancel = true; if (closing) return; closing = true;
        var clock = Stopwatch.StartNew();
        if (desktop != null && !await desktop.CanClose()) { closing = false; return; }
        long confirmed = clock.ElapsedMilliseconds;
        string monitor = Screen.FromControl(this).DeviceName;
        Hide(); // Keep the process alive for pending saves, but dismiss the window immediately.
        long hidden = clock.ElapsedMilliseconds;
        cancel.Cancel();
        if (running || (child != null && !child.HasExited)) { if (!await Stop()) { closing = false; return; } }
        var timing = new { confirmedMs = confirmed, hiddenMs = hidden, hideAfterConfirmationMs = hidden - confirmed,
            backendStoppedMs = clock.ElapsedMilliseconds, monitor = monitor, stopped = child == null || child.HasExited };
        try {
            Directory.CreateDirectory(Program.Home);
            File.WriteAllText(Path.Combine(Program.Home, "close-timing.json"), Program.Json.Serialize(timing));
            if (Program.DesktopSmoke != null) File.WriteAllText(Program.DesktopSmoke + ".shutdown.json", Program.Json.Serialize(timing));
        } catch (IOException) { } catch (UnauthorizedAccessException) { }
        allowClose = true; Close();
    }
    async Task SmokeTest() {
        closing = true; // Let this test finish recording shutdown before the exit event closes the form.
        var h = await Task.Run(() => Program.Health());
        bool ok = h != null; await Stop();
        var after = await Task.Run(() => Program.Health());
        File.WriteAllText(Program.Smoke, Program.Json.Serialize(new { passed = ok && after == null, launcherPid = Process.GetCurrentProcess().Id, version = Setup.Version, app = Setup.AppDir, python = Setup.PythonExe(), title = Text }));
        allowClose = true; Close();
    }
    protected override void WndProc(ref Message message) {
        if (desktop != null && ChromeMessage(ref message)) return;
        if (message.Msg == 0x8001) {
            if (!closing) {
                Show(); if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
                Activate();
                if (running && message.WParam != IntPtr.Zero) OpenClick(null, null);
            }
            return;
        }
        base.WndProc(ref message);
    }
}

// The player's settings: <data home>\settings.json, written by the lab's Settings window (src/settings.py) and by the
// language menu here. Only changed values are in it; a missing or damaged file, or a value of the wrong type, means the
// default.
internal static class LabSettings {
    internal static string FilePath { get { return Path.Combine(Setup.DataHome, "settings.json"); } }
    internal static Dictionary<string, object> Read() {
        try { var d = File.Exists(FilePath) ? Program.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(FilePath)) : null; return d ?? new Dictionary<string, object>(); }
        catch (Exception) { return new Dictionary<string, object>(); }
    }
    internal static bool Flag(string key, bool fallback) { object v; return Read().TryGetValue(key, out v) && v is bool ? (bool)v : fallback; }
    internal static string Text(string key) { object v; return Read().TryGetValue(key, out v) ? v as string : null; }
    // Historical settings key retained for compatibility; now opens the desktop editor.
    internal static bool OpenBrowserOnStart { get { return !Program.NoBrowser && Flag("open_browser", true); } }
    // General > Check for updates when Asset Lab starts (tests that force an update check anyway set ASSET_LAB_AUTO_UPDATE).
    internal static bool CheckUpdates { get { return Flag("check_updates", true) || Environment.GetEnvironmentVariable("ASSET_LAB_AUTO_UPDATE") == "1"; } }
}

// The launcher's words in the player's language. The choice lives in <data home>\settings.json ("language"), shared with
// the lab; without one, Windows' display language picks it. English text is the key, so a missing line stays English.
internal static class L {
    internal static readonly string[] Codes = { "en", "es", "pt", "fr", "de" };
    internal static readonly string[] Names = { "English", "Español", "Português", "Français", "Deutsch" };
    static readonly Dictionary<string, string[]> T9 = new Dictionary<string, string[]> {
        // English                                            es / pt / fr / de
        { "Starting…", new[] { "Iniciando…", "Iniciando…", "Démarrage…", "Wird gestartet…" } },
        { "Checking for updates…", new[] { "Buscando actualizaciones…", "Procurando atualizações…", "Recherche de mises à jour…", "Suche nach Updates…" } },
        { "Checking the file…", new[] { "Comprobando el archivo…", "Verificando o arquivo…", "Vérification du fichier…", "Datei wird geprüft…" } },
        { "Unpacking Asset Lab", new[] { "Descomprimiendo Asset Lab", "Descompactando o Asset Lab", "Décompression d'Asset Lab", "Asset Lab wird entpackt" } },
        { "Downloading Python", new[] { "Descargando Python", "Baixando o Python", "Téléchargement de Python", "Python wird heruntergeladen" } },
        { "Setting up Python", new[] { "Configurando Python", "Configurando o Python", "Configuration de Python", "Python wird eingerichtet" } },
        { "Preparing Python", new[] { "Preparando Python", "Preparando o Python", "Préparation de Python", "Python wird vorbereitet" } },
        { "First start only: about 30 MB from python.org and PyPI.", new[] { "Solo la primera vez: unos 30 MB de python.org y PyPI.", "Só na primeira vez: cerca de 30 MB do python.org e PyPI.", "Premier démarrage seulement : environ 30 Mo depuis python.org et PyPI.", "Nur beim ersten Start: etwa 30 MB von python.org und PyPI." } },
        { "Downloading the local AI", new[] { "Descargando la IA local", "Baixando a IA local", "Téléchargement de l'IA locale", "Lokale KI wird heruntergeladen" } },
        { "Downloading Asset Lab", new[] { "Descargando Asset Lab", "Baixando o Asset Lab", "Téléchargement d'Asset Lab", "Asset Lab wird heruntergeladen" } },
        { "Installing Asset Lab", new[] { "Instalando Asset Lab", "Instalando o Asset Lab", "Installation d'Asset Lab", "Asset Lab wird installiert" } },
        { "Your workspace and edits are kept.", new[] { "Tu espacio de trabajo y tus ediciones se conservan.", "Seu espaço de trabalho e suas edições são mantidos.", "Votre espace de travail et vos modifications sont conservés.", "Dein Arbeitsbereich und deine Änderungen bleiben erhalten." } },
        { "Updating the launcher", new[] { "Actualizando el lanzador", "Atualizando o inicializador", "Mise à jour du lanceur", "Starter wird aktualisiert" } },
        { "is available", new[] { "está disponible", "está disponível", "est disponible", "ist verfügbar" } },
        { "You have", new[] { "Tienes la", "Você tem a", "Vous avez la", "Du hast" } },
        { "Your workspace and edits are kept, and a backup of your settings is made first.", new[] { "Tu espacio de trabajo y tus ediciones se conservan, y antes se hace una copia de tus ajustes.", "Seu espaço de trabalho e suas edições são mantidos, e antes é feito um backup das suas configurações.", "Votre espace de travail et vos modifications sont conservés, et vos réglages sont sauvegardés avant.", "Arbeitsbereich und Änderungen bleiben erhalten; deine Einstellungen werden vorher gesichert." } },
        { "Update now", new[] { "Actualizar ahora", "Atualizar agora", "Mettre à jour", "Jetzt aktualisieren" } },
        { "Not now", new[] { "Ahora no", "Agora não", "Pas maintenant", "Nicht jetzt" } },
        { "Download", new[] { "Descargar", "Baixar", "Télécharger", "Herunterladen" } },
        { "Download anyway", new[] { "Descargar igualmente", "Baixar mesmo assim", "Télécharger quand même", "Trotzdem herunterladen" } },
        { "Download the local AI?", new[] { "¿Descargar la IA local?", "Baixar a IA local?", "Télécharger l'IA locale ?", "Lokale KI herunterladen?" } },
        { "This PC is below the local AI's needs", new[] { "Este PC no llega a lo que necesita la IA local", "Este PC está abaixo do que a IA local precisa", "Ce PC est en dessous des besoins de l'IA locale", "Dieser PC erfüllt die Anforderungen der lokalen KI nicht" } },
        { "once: the smart Theme AI planner and image redesigns, all on this PC.", new[] { "una sola vez: el planificador inteligente de Theme AI y los rediseños de imágenes, todo en este PC.", "uma vez só: o planejador inteligente do Theme AI e os redesenhos de imagens, tudo neste PC.", "une seule fois : le planificateur intelligent de Theme AI et les redessins d'images, tout sur ce PC.", "einmalig: der smarte Theme-AI-Planer und Bild-Neugestaltungen, alles auf diesem PC." } },
        { "Needs 16 GB RAM (this PC", new[] { "Necesita 16 GB de RAM (este PC tiene", "Precisa de 16 GB de RAM (este PC tem", "Nécessite 16 Go de RAM (ce PC a", "Benötigt 16 GB RAM (dieser PC hat" } },
        { "and", new[] { "y", "e", "et", "und" } },
        { "free disk", new[] { "libres en disco", "livres em disco", "libres sur le disque", "freien Speicher" } },
        { "free", new[] { "libres", "livres", "libres", "frei" } },
        { "Graphics card", new[] { "Tarjeta gráfica", "Placa de vídeo", "Carte graphique", "Grafikkarte" } },
        { "about 35 s per image.", new[] { "unos 35 s por imagen.", "cerca de 35 s por imagem.", "environ 35 s par image.", "etwa 35 s pro Bild." } },
        { "(6 GB+ recommended): images may take minutes.", new[] { "(se recomiendan 6 GB o más): las imágenes pueden tardar minutos.", "(recomendado 6 GB ou mais): as imagens podem levar minutos.", "(6 Go ou plus recommandés) : les images peuvent prendre des minutes.", "(6 GB+ empfohlen): Bilder können Minuten dauern." } },
        { "No graphics card memory found: images will take several minutes.", new[] { "No se encontró memoria de tarjeta gráfica: las imágenes tardarán varios minutos.", "Nenhuma memória de placa de vídeo encontrada: as imagens vão levar vários minutos.", "Aucune mémoire de carte graphique trouvée : les images prendront plusieurs minutes.", "Kein Grafikspeicher gefunden: Bilder dauern mehrere Minuten." } },
        { "Without it, Theme AI still works from keywords.", new[] { "Sin ella, Theme AI sigue funcionando con palabras clave.", "Sem ela, o Theme AI ainda funciona com palavras-chave.", "Sans elle, Theme AI fonctionne quand même avec des mots-clés.", "Ohne sie funktioniert Theme AI weiterhin mit Stichwörtern." } },
        { "Something went wrong", new[] { "Algo salió mal", "Algo deu errado", "Un problème est survenu", "Etwas ist schiefgelaufen" } },
        { "Try again", new[] { "Reintentar", "Tentar de novo", "Réessayer", "Erneut versuchen" } },
        { "Starting Asset Lab…", new[] { "Iniciando Asset Lab…", "Iniciando o Asset Lab…", "Démarrage d'Asset Lab…", "Asset Lab wird gestartet…" } },
        { "Asset Lab stopped while starting. The details are below.", new[] { "Asset Lab se detuvo al iniciar. Los detalles están abajo.", "O Asset Lab parou ao iniciar. Os detalhes estão abaixo.", "Asset Lab s'est arrêté au démarrage. Les détails sont ci-dessous.", "Asset Lab wurde beim Start beendet. Details unten." } },
        { "Starting is taking longer than expected. The details are below.", new[] { "El inicio está tardando más de lo esperado. Los detalles están abajo.", "A inicialização está demorando mais que o esperado. Os detalhes estão abaixo.", "Le démarrage prend plus de temps que prévu. Les détails sont ci-dessous.", "Der Start dauert länger als erwartet. Details unten." } },
        { "Asset Lab is running", new[] { "Asset Lab está funcionando", "O Asset Lab está rodando", "Asset Lab est lancé", "Asset Lab läuft" } },
        { "Closing this window stops Asset Lab. Your work is saved in", new[] { "Cerrar esta ventana detiene Asset Lab. Tu trabajo se guarda en", "Fechar esta janela encerra o Asset Lab. Seu trabalho é salvo em", "Fermer cette fenêtre arrête Asset Lab. Votre travail est enregistré dans", "Schließen dieses Fensters beendet Asset Lab. Deine Arbeit ist gespeichert in" } },
        { "The local AI is not downloaded yet.", new[] { "La IA local aún no está descargada.", "A IA local ainda não foi baixada.", "L'IA locale n'est pas encore téléchargée.", "Die lokale KI ist noch nicht heruntergeladen." } },
        { "Open Asset Lab", new[] { "Abrir Asset Lab", "Abrir o Asset Lab", "Ouvrir Asset Lab", "Asset Lab öffnen" } },
        { "Stop", new[] { "Detener", "Parar", "Arrêter", "Beenden" } },
        { "Get the local AI", new[] { "Obtener la IA local", "Obter a IA local", "Obtenir l'IA locale", "Lokale KI holen" } },
        { "Asset Lab stopped", new[] { "Asset Lab se detuvo", "O Asset Lab parou", "Asset Lab s'est arrêté", "Asset Lab wurde beendet" } },
        { "It exited with code", new[] { "Terminó con el código", "Saiu com o código", "Il s'est terminé avec le code", "Es wurde mit Code beendet:" } },
        { "The details are below.", new[] { "Los detalles están abajo.", "Os detalhes estão abaixo.", "Les détails sont ci-dessous.", "Details unten." } },
        { "Start again", new[] { "Iniciar de nuevo", "Iniciar de novo", "Relancer", "Erneut starten" } },
        { "Stopping after pending work…", new[] { "Deteniendo tras terminar el trabajo pendiente…", "Parando após o trabalho pendente…", "Arrêt après le travail en cours…", "Wird nach laufender Arbeit beendet…" } },
        { "Language", new[] { "Idioma", "Idioma", "Langue", "Sprache" } },
        { "You can change it any time in Asset Lab's Settings.", new[] { "Puedes cambiarlo cuando quieras en los Ajustes de Asset Lab.", "Você pode mudar quando quiser nas Configurações do Asset Lab.", "Vous pouvez le changer à tout moment dans les Réglages d'Asset Lab.", "Du kannst sie jederzeit in den Einstellungen von Asset Lab ändern." } },
    };
    static int index = -1;
    internal static string Code {
        get {
            if (index < 0) {
                // "" (Automatic in the lab's Settings) or no choice: Windows' display language.
                string saved = LabSettings.Text("language");
                index = Array.IndexOf(Codes, String.IsNullOrEmpty(saved) ? System.Globalization.CultureInfo.CurrentUICulture.TwoLetterISOLanguageName : saved);
                if (index < 0) index = 0;
            }
            return Codes[index];
        }
        set {
            index = Math.Max(0, Array.IndexOf(Codes, value));
            try { var d = LabSettings.Read(); d["language"] = Codes[index]; Directory.CreateDirectory(Setup.DataHome); File.WriteAllText(LabSettings.FilePath, Program.Json.Serialize(d)); }
            catch (Exception) { }   // the choice still holds for this session
        }
    }
    // The first start asks once; "Automatic" in the lab's Settings stores no value, so a small marker remembers the asking.
    static string AskedFile { get { return Path.Combine(Program.Home, "language-asked"); } }
    internal static bool Asked { get { return LabSettings.Text("language") != null || File.Exists(AskedFile); } }
    internal static void MarkAsked() { try { Directory.CreateDirectory(Program.Home); File.WriteAllText(AskedFile, DateTime.Now.ToString("s")); } catch (Exception) { } }
    internal static string T(string english) {
        string[] t; var i = Array.IndexOf(Codes, Code) - 1;
        return i >= 0 && T9.TryGetValue(english, out t) ? t[i] : english;
    }
}

// A thin progress bar in the lab's colors; Busy shows a sliding highlight while the length of a step is unknown.
internal sealed class Bar : Control {
    internal double Value; internal bool Busy;
    readonly Stopwatch clock = Stopwatch.StartNew();
    public Bar() { DoubleBuffered = true; SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint, true); }
    protected override void OnPaint(PaintEventArgs e) {
        var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias;
        var r = new Rectangle(0, 0, Width - 1, Height - 1);
        using (var track = Loader.Rounded(r, Height / 2)) using (var b = new SolidBrush(Color.FromArgb(30, 40, 53))) g.FillPath(b, track);
        Rectangle fill;
        if (Busy) { int w = Width / 4, x = (int)((clock.ElapsedMilliseconds / 3) % (Width + w)) - w; fill = new Rectangle(Math.Max(0, x), 0, Math.Min(w, Width - Math.Max(0, x)) - 1, Height - 1); }
        else fill = new Rectangle(0, 0, (int)Math.Max(Height, (Width - 1) * Math.Max(0, Math.Min(1, Value))), Height - 1);
        if (fill.Width < 2) return;
        using (var p = Loader.Rounded(fill, Height / 2))
        using (var brush = new LinearGradientBrush(new Rectangle(0, 0, Width, Height), Color.FromArgb(34, 211, 238), Color.FromArgb(244, 114, 182), 0f)) g.FillPath(brush, p);
    }
}

// Flat buttons matching the lab: a gradient primary and a quiet outline secondary.
internal sealed class FlatButton : Control {
    readonly bool main; bool hover, down;
    public FlatButton(bool primary) {
        main = primary; DoubleBuffered = true; Cursor = Cursors.Hand; Font = new Font("Segoe UI Semibold", 10f);
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.SupportsTransparentBackColor, true);
        MouseEnter += delegate { hover = true; Invalidate(); }; MouseLeave += delegate { hover = down = false; Invalidate(); };
        MouseDown += delegate { down = true; Invalidate(); }; MouseUp += delegate { down = false; Invalidate(); };
    }
    protected override void OnTextChanged(EventArgs e) { base.OnTextChanged(e); Invalidate(); }
    protected override void OnPaint(PaintEventArgs e) {
        var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        g.Clear(Parent != null ? Parent.BackColor : Color.FromArgb(16, 21, 30));
        var r = new Rectangle(0, 0, Width - 1, Height - 1);
        bool icon = Text.Length <= 1;
        using (var p = Loader.Rounded(r, icon ? 8 : 11)) {
            if (main) {
                using (var b = new LinearGradientBrush(r, Color.FromArgb(34, 211, 238), Color.FromArgb(244, 114, 182), 45f)) g.FillPath(b, p);
                if (hover) using (var w = new SolidBrush(Color.FromArgb(down ? 50 : 30, 255, 255, 255))) g.FillPath(w, p);
            } else {
                using (var b = new SolidBrush(hover ? Color.FromArgb(37, 49, 64) : (icon ? Color.FromArgb(16, 21, 30) : Color.FromArgb(30, 40, 53)))) g.FillPath(b, p);
                if (!icon) using (var pen = new Pen(Color.FromArgb(48, 61, 78))) g.DrawPath(pen, p);
            }
        }
        TextRenderer.DrawText(g, Text, Font, r, main ? Color.FromArgb(7, 8, 12) : Color.FromArgb(232, 238, 246), TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
    }
}

// Windows closes this handle if Task Manager ends the launcher; its server and AI workers then end too.
internal sealed class ProcessGroup : IDisposable {
    [StructLayout(LayoutKind.Sequential)] struct Limits {
        public long UserTime, JobTime; public uint Flags; public UIntPtr MinWorking, MaxWorking;
        public uint ActiveProcesses; public UIntPtr Affinity; public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct IO { public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)] struct Extended { public Limits Basic; public IO Counters; public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr attrs, string name);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref Extended info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    IntPtr handle;
    internal ProcessGroup(Process process) {
        handle = CreateJobObject(IntPtr.Zero, null); Extended limits = new Extended(); limits.Basic.Flags = 0x2000;
        if (handle == IntPtr.Zero || !SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(typeof(Extended))) || !AssignProcessToJobObject(handle, process.Handle)) {
            int error = Marshal.GetLastWin32Error(); Dispose(); process.Kill(); throw new System.ComponentModel.Win32Exception(error, "Cannot attach the server to the Asset Lab process group.");
        }
    }
    public void Dispose() { if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; } }
}
