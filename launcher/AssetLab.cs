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
    internal static string Smoke, Preview;
    internal static string Url { get { return "http://127.0.0.1:" + Port; } }
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int command);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr FindWindow(string className, string title);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint message, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

    [STAThread] static int Main(string[] args) {
        try {
            SetProcessDPIAware();   // crisp text and shapes on scaled displays; sizes are scaled by hand
            bool stop = false, restart = false;
            for (int i = 0; i < args.Length; i++) {
                switch (args[i]) {
                    case "--port": Port = int.Parse(args[++i]); break;
                    case "--workspace": Workspace = Path.GetFullPath(args[++i]); break;
                    case "--game": Game = Path.GetFullPath(args[++i]); break;
                    case "--home": Home = Path.GetFullPath(args[++i]); break;
                    case "--no-browser": NoBrowser = true; break;
                    case "--offline": Offline = true; break;
                    case "--stop": stop = true; break;
                    case "--restart": restart = true; break;
                    case "--smoke-test": Smoke = Path.GetFullPath(args[++i]); NoBrowser = true; break;
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
            using (Mutex mutex = new Mutex(true, "Local\\AssetLab.Controller." + Port, out first)) {
                if (!first) {
                    // Already open: bring that window forward and let it open the browser.
                    IntPtr h = FindWindow(null, Name + " - " + Port);
                    if (h != IntPtr.Zero) { ShowWindow(h, 9); SetForegroundWindow(h); PostMessage(h, 0x8001, NoBrowser ? IntPtr.Zero : new IntPtr(1), IntPtr.Zero); }
                    return 0;
                }
                Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
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
        using (WebResponse response = r.GetResponse())
        using (StreamReader stream = new StreamReader(response.GetResponseStream()))
            return Json.Deserialize<Dictionary<string, object>>(stream.ReadToEnd());
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
    internal static void StopServer() {
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
        return File.Exists(Path.Combine(Program.Home, "app", v, "src", "lab_server.py")) ? v : null;
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
                                if (clock.ElapsedMilliseconds > 250) {
                                    double speed = (done - mark) / Math.Max(0.001, clock.Elapsed.TotalSeconds);
                                    report(label, String.Format("{0} of {1} · {2}/s", Size(done), Size(total), Size((long)speed)), total > 0 ? (double)done / total : 0);
                                    clock.Restart(); mark = done;
                                }
                            }
                        }
                    }
                }
                report(label, "Checking the file…", 1);
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
            }
        }
    }
    static void Replace(string staging, string target) {
        if (Directory.Exists(target)) Directory.Delete(target, true);
        Directory.Move(staging, target);
    }

    /// The lab code this exe carries, unless a same-or-newer version is already installed.
    internal static void EnsureApp(Progress report) {
        if (Program.DevRoot != null) return;
        string embedded = EmbeddedVersion(), installed = InstalledVersion();
        if (installed != null && Compare(installed, embedded) >= 0) return;
        report("Unpacking Asset Lab " + embedded, "", 0.5);
        string zip = Path.Combine(Program.Home, "downloads", "app-" + embedded + ".zip");
        Directory.CreateDirectory(Path.GetDirectoryName(zip));
        using (var s = Program.Resource("app.zip")) using (var f = File.Create(zip)) s.CopyTo(f);
        InstallApp(zip, embedded);
    }
    static void InstallApp(string zip, string version) {
        string root = Path.Combine(Program.Home, "app"), staging = Path.Combine(root, version + ".new");
        if (Directory.Exists(staging)) Directory.Delete(staging, true);
        Extract(zip, staging);
        if (!File.Exists(Path.Combine(staging, "src", "lab_server.py"))) throw new InvalidDataException("The update package is incomplete.");
        File.WriteAllText(Path.Combine(staging, "VERSION"), version);
        Replace(staging, Path.Combine(root, version));
        string previous = InstalledVersion();
        File.WriteAllText(Path.Combine(root, "current.txt"), version);
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
        Download(Convert.ToString(py["url"]), zip, Convert.ToString(py["sha256"]), Convert.ToInt64(py["size"]), "Downloading Python " + py["version"], report, cancel);
        var wheels = new List<string>();
        foreach (var w in List(m["wheels"])) {
            string file = Path.Combine(downloads, Path.GetFileName(new Uri(Convert.ToString(w["url"])).LocalPath));
            Download(Convert.ToString(w["url"]), file, Convert.ToString(w["sha256"]), Convert.ToInt64(w["size"]), "Downloading " + w["name"], report, cancel);
            wheels.Add(file);
        }
        report("Setting up Python", "", 0.95);
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
                     "Downloading the local AI", report == null ? null : (Progress)((s, d, f) => report(s, name + " · " + d, (start + f * size) / total)), cancel);
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
        BackupData(Version);
        string zip = Path.Combine(Program.Home, "downloads", "asset-lab-" + release.Version + ".zip");
        Download(release.AppUrl, zip, release.AppSha, release.AppSize, "Downloading Asset Lab " + release.Version, report, cancel);
        report("Installing Asset Lab " + release.Version, "Your workspace and edits are kept.", 1);
        InstallApp(zip, release.Version);
        string self = Assembly.GetExecutingAssembly().Location;
        if (release.ExeUrl == null || Sha256(self) == release.ExeSha) return false;
        string fresh = self + ".new";
        Download(release.ExeUrl, fresh, release.ExeSha, release.ExeSize, "Updating the launcher", report, cancel);
        string old = self + ".old";
        if (File.Exists(old)) File.Delete(old);
        File.Move(self, old);   // a running exe can be renamed, not overwritten
        File.Move(fresh, self);
        return true;
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
internal sealed class Loader : Form {
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
    readonly FlatButton primary = new FlatButton(true), secondary = new FlatButton(false), close = new FlatButton(false), minimize = new FlatButton(false);
    readonly TextBox log = new TextBox();
    readonly CancellationTokenSource cancel = new CancellationTokenSource();
    Rectangle full;
    double intro;        // 0..1 logo pop and spin, 1..2 window grows
    bool introDone, closing, allowClose, running;
    Process child; ProcessGroup children;
    TaskCompletionSource<bool> choice;
    Dictionary<string, object> manifest;

    public Loader() {
        Text = Program.Name + " - " + Program.Port;
        using (var probe = CreateGraphics()) k = probe.DpiX / 96f;
        W = S(560); H = S(356); LogoBig = S(132); LogoSmall = S(46);
        using (var s = Program.Resource("logo.png")) logo = s == null ? null : Image.FromStream(s);
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        FormBorderStyle = FormBorderStyle.None; StartPosition = FormStartPosition.Manual; BackColor = Bg; ForeColor = Text1;
        Font = new Font("Segoe UI", 10f); DoubleBuffered = true; ShowInTaskbar = true;
        var screen = Screen.PrimaryScreen.WorkingArea;
        full = new Rectangle(screen.X + (screen.Width - W) / 2, screen.Y + (screen.Height - H) / 2, W, H);
        Bounds = Centered(LogoBig + S(24), LogoBig + S(24));
        TransparencyKey = Color.FromArgb(1, 2, 3); BackColor = TransparencyKey;   // only the logo shows during the intro

        title.Text = "Asset Lab"; title.Font = new Font("Segoe UI Semibold", 19f); title.AutoSize = true;
        status.Font = new Font("Segoe UI Semibold", 11f); status.AutoSize = false; status.Text = "Starting…";
        detail.ForeColor = Muted; detail.Font = new Font("Segoe UI", 9f); detail.AutoSize = false;
        version.ForeColor = Muted; version.Font = new Font("Segoe UI", 8f); version.AutoSize = true; version.TextAlign = ContentAlignment.MiddleRight;
        close.Text = "✕"; minimize.Text = "–";
        log.Multiline = true; log.ReadOnly = true; log.ScrollBars = ScrollBars.None; log.BackColor = Color.FromArgb(12, 16, 23); log.ForeColor = Muted;
        log.BorderStyle = BorderStyle.None; log.Font = new Font("Consolas", 8.5f); log.Visible = false;
        foreach (Control c in new Control[] { title, status, detail, version, bar, primary, secondary, close, minimize, log }) { c.Visible = false; Controls.Add(c); }
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
        close.SetBounds(W - S(44), S(12), S(32), S(28)); minimize.SetBounds(W - S(78), S(12), S(32), S(28));
        status.SetBounds(S(28), S(110), W - S(56), S(26)); detail.SetBounds(S(28), S(138), W - S(56), S(84));
        bar.SetBounds(S(28), S(232), W - S(56), S(8));
        primary.SetBounds(S(28), S(254), S(200), S(40)); secondary.SetBounds(S(238), S(254), S(150), S(40));
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
                foreach (Control c in new Control[] { title, status, detail, bar, close, minimize, version }) c.Visible = true;
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
        foreach (Control c in new Control[] { title, status, detail, bar, close, minimize, version }) c.Visible = true;
        version.Text = "v" + Setup.Version; Layout2();
        Action<string> snap = name => {
            using (var bmp = new Bitmap(Width, Height)) { DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height)); bmp.Save(Path.Combine(Program.Preview, name + ".png")); }
        };
        Report("Downloading the local AI", "flux-2-klein-4b-Q4_0.gguf · 1.02 GB of 2.29 GB · 38.4 MB/s", 0.44); snap("1-downloading");
        string yes; var all = Setup.List(Setup.Manifest()["runtime"]).ToList();
        Ask(AiQuestion(all, out yes), AiDetails(all), yes, "Not now"); snap("2-question");
        primary.Visible = secondary.Visible = false; Running(); snap("3-running");
        Fail("Asset Lab stopped while starting. The details are below."); log.Text = "Traceback (most recent call last):\r\n  ..."; snap("4-error");
        allowClose = true; Close();
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
            Report("Checking for updates…", "", -1);
            manifest = await Task.Run(() => Setup.Manifest());
            await Task.Run(() => Setup.EnsureApp(Report));
            manifest = await Task.Run(() => Setup.Manifest());
            var release = await Task.Run(() => Setup.Latest(manifest));
            bool auto = Environment.GetEnvironmentVariable("ASSET_LAB_AUTO_UPDATE") == "1";   // tests only
            if (release != null && (Program.Smoke == null || auto)) {
                string notes = (release.Notes ?? "").Split('\n').Select(l => l.Trim()).FirstOrDefault(l => l.Length > 0) ?? "";
                if (auto || await Ask("Asset Lab " + release.Version + " is available", "You have " + Setup.Version + ". " + (notes.Length > 90 ? notes.Substring(0, 90) + "…" : notes) +
                              "\nYour workspace and edits are kept, and a backup of your settings is made first.", "Update now", "Not now")) {
                    bool newExe = await Task.Run(() => Setup.ApplyUpdate(release, Report, cancel.Token));
                    if (newExe) {
                        // The new launcher takes over with the same options; this one steps aside.
                        Process.Start(Application.ExecutablePath, String.Join(" ", Environment.GetCommandLineArgs().Skip(1).Select(Program.Quote)));
                        allowClose = true; Close(); return;
                    }
                    manifest = await Task.Run(() => Setup.Manifest());
                    version.Text = "v" + Setup.Version;
                }
            }
            if (!Setup.PythonReady(manifest)) {
                Report("Preparing Python", "First start only: about 30 MB from python.org and PyPI.", 0);
                await Task.Run(() => Setup.EnsurePython(manifest, Report, cancel.Token));
            }
            var missing = Setup.MissingRuntime(manifest);
            string yes;
            string declined = Path.Combine(Program.Home, "ai-declined");
            if (missing.Count > 0 && !File.Exists(declined) && Program.Smoke == null) {
                if (await Ask(AiQuestion(missing, out yes), AiDetails(missing), yes, "Not now")) {
                    await Task.Run(() => Setup.DownloadRuntime(manifest, Report, cancel.Token));
                } else { Directory.CreateDirectory(Program.Home); File.WriteAllText(declined, DateTime.Now.ToString("s")); }
            }
            await StartServer(!Program.NoBrowser);
        } catch (OperationCanceledException) {
        } catch (Exception e) {
            Fail(e.Message);
        }
    }
    // The one-time AI question, with this PC's memory, graphics card and disk against what the models need.
    string AiQuestion(List<Dictionary<string, object>> missing, out string yes) {
        bool weak = Specs.Ram() < Specs.MinRam || Specs.FreeDisk(Setup.RuntimeDir) < Setup.Bytes(missing) + (1L << 30);
        yes = weak ? "Download anyway" : "Download";
        return weak ? "This PC is below the local AI's needs" : "Download the local AI?";
    }
    string AiDetails(List<Dictionary<string, object>> missing) {
        long ram = Specs.Ram(), size = Setup.Bytes(missing), disk = Specs.FreeDisk(Setup.RuntimeDir); string gpu; long vram = Specs.Vram(out gpu);
        string needs = "Needs 16 GB RAM (this PC " + Specs.Gb(ram) + ") and " + Specs.Gb(size + (1L << 30)) + " free disk (" + Specs.Gb(disk) + " free).";
        string speed = vram >= Specs.GoodVram ? (gpu.Length > 0 ? gpu : "Graphics card") + ", " + Specs.Gb(vram) + ": about 35 s per image."
                     : vram > 0 ? "Graphics card " + Specs.Gb(vram) + " (6 GB+ recommended): images may take minutes."
                     : "No graphics card memory found: images will take several minutes.";
        return Setup.Size(size) + " once: the smart Theme AI planner and image redesigns, all on this PC.\n" + needs + "\n" + speed +
               "\nWithout it, Theme AI still works from keywords.";
    }
    void Fail(string message) {
        if (IsDisposed) return;
        status.Text = "Something went wrong"; status.ForeColor = Bad; detail.Text = message; bar.Visible = false; bar.Busy = false;
        log.Visible = true; primary.Text = "Try again"; primary.Visible = true; secondary.Visible = false;
        EventHandler retry = null; retry = delegate { primary.Click -= retry; primary.Visible = false; log.Visible = false; Run(); };
        primary.Click += retry;
        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
    }

    async Task StartServer(bool openBrowser) {
        Report("Starting Asset Lab…", "", -1);
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
                if (child.HasExited) throw new InvalidOperationException("Asset Lab stopped while starting. The details are below.");
                h = await Task.Run(() => Program.Health()); if (h != null) break;
            }
            if (h == null) throw new InvalidOperationException("Starting is taking longer than expected. The details are below.");
        }
        Running();
        if (Program.Smoke != null) { await SmokeTest(); return; }
        if (openBrowser) Program.OpenBrowser();
        // Out of the way, but still in the taskbar (never the tray): click it to get Open / Stop back.
        await Task.Delay(1200);
        if (running && !IsDisposed) WindowState = FormWindowState.Minimized;
    }
    void Running() {
        running = true; bar.Busy = false; bar.Value = 1; bar.Visible = false;
        status.Text = "Asset Lab is running"; status.ForeColor = Text1;
        detail.Text = Program.Url + "\nClosing this window stops Asset Lab. Your work is saved in " + Setup.DataHome + ".";
        primary.Text = "Open Asset Lab"; secondary.Text = "Stop"; primary.Visible = secondary.Visible = true;
        primary.Click -= OpenClick; secondary.Click -= StopClick; primary.Click += OpenClick; secondary.Click += StopClick;
        var m = manifest == null ? null : Setup.MissingRuntime(manifest);
        if (m != null && m.Count > 0) { detail.Text += "\nThe local AI is not downloaded yet."; }
    }
    void OpenClick(object s, EventArgs e) { try { Program.OpenBrowser(); } catch (Exception x) { Write(x.Message); } }
    async void StopClick(object s, EventArgs e) { if (await Stop()) { allowClose = true; Close(); } }
    void OnServerExit(object sender, EventArgs e) {
        if (IsDisposed) return;
        int code; try { code = ((Process)sender).ExitCode; } catch (InvalidOperationException) { return; }
        BeginInvoke((Action)(async () => {
            if (closing || sender != child) return;
            if (code == 75) { running = false; await StartServer(false); return; }   // the lab asked for a restart with new code
            running = false; status.Text = "Asset Lab stopped"; detail.Text = code == 0 ? "" : "It exited with code " + code + ". The details are below.";
            log.Visible = code != 0; primary.Text = "Start again"; primary.Visible = true; secondary.Visible = false;
            primary.Click -= OpenClick; EventHandler again = null; again = async delegate { primary.Click -= again; log.Visible = false; await StartServer(true); }; primary.Click += again;
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
        }));
    }
    async Task<bool> Stop() {
        status.Text = "Stopping after pending work…"; primary.Visible = secondary.Visible = false;
        try {
            await Task.Run(() => Program.StopServer());
            if (child != null && !child.HasExited) await Task.Run(() => child.WaitForExit(30000));
            running = false; return true;
        } catch (Exception e) { Fail(e.Message); return false; }
    }
    async void OnClosing(object sender, FormClosingEventArgs e) {
        if (allowClose) return;
        e.Cancel = true; if (closing) return; closing = true;
        cancel.Cancel();
        if (running || (child != null && !child.HasExited)) { if (!await Stop()) { closing = false; return; } }
        allowClose = true; Close();
    }
    async Task SmokeTest() {
        var h = await Task.Run(() => Program.Health());
        bool ok = h != null; await Stop();
        var after = await Task.Run(() => Program.Health());
        File.WriteAllText(Program.Smoke, Program.Json.Serialize(new { passed = ok && after == null, version = Setup.Version, app = Setup.AppDir, python = Setup.PythonExe(), title = Text }));
        allowClose = true; Close();
    }
    protected override void WndProc(ref Message message) {
        if (message.Msg == 0x8001) { bool open = message.WParam != IntPtr.Zero; if (running && open) OpenClick(null, null); return; }
        base.WndProc(ref message);
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
