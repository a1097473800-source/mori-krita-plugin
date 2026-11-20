using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Mori for Krita 安装程序")]
[assembly: AssemblyCompany("Mori")]
[assembly: AssemblyVersion("__MORI_VERSION__.0")]

internal static class Setup
{
    const string Magic = "MORI-KRITA-SETUP1";
    const string PayloadHash = "__PAYLOAD_SHA256__";
    static string ResultPath;

    [STAThread]
    static int Main(string[] args)
    {
        using (var mutex = new Mutex(false, "Local\\MoriKritaBundleSetup"))
        {
            if (!mutex.WaitOne(0)) return 2;
            try
            {
                if (args.Length == 2 && args[0] == "--self-test")
                {
                    string root = Path.GetFullPath(args[1]);
                    Directory.CreateDirectory(root);
                    try
                    {
                        Run(Path.Combine(root, "payload"), Path.Combine(root, "profile"), delegate(int p, string s) { });
                        File.WriteAllText(Path.Combine(root, "setup-test-passed.txt"), ResultPath, Encoding.UTF8);
                        return 0;
                    }
                    catch (Exception e)
                    {
                        File.WriteAllText(Path.Combine(root, "setup-test-error.txt"), e.ToString(), Encoding.UTF8);
                        return 1;
                    }
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new SetupForm());
                return 0;
            }
            finally { mutex.ReleaseMutex(); }
        }
    }

    internal static string Install(Action<int, string> report)
    {
        string work = Path.Combine(Path.GetTempPath(), "MoriKritaSetup-" + Guid.NewGuid().ToString("N"));
        try { Run(work, null, report); return ResultPath; }
        finally
        {
            try
            {
                string prefix = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "MoriKritaSetup-"));
                if (Path.GetFullPath(work).StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
                    && Directory.Exists(work)) Directory.Delete(work, true);
            }
            catch { /* A locked temporary log can be removed by Windows later. */ }
        }
    }

    static void Run(string work, string testRoot, Action<int, string> report)
    {
        if (testRoot == null && Process.GetProcessesByName("krita").Length != 0)
            throw new InvalidOperationException("请先退出 Krita，然后重新点击安装。");
        if (Directory.Exists(work)) throw new IOException("临时安装目录已存在，请重试。");
        Directory.CreateDirectory(work);
        string archive = Path.Combine(work, "payload.zip");
        report(1, "正在读取安装包");
        using (var source = File.OpenRead(Assembly.GetExecutingAssembly().Location))
        {
            int footerSize = 8 + Encoding.ASCII.GetByteCount(Magic);
            if (source.Length < footerSize) throw new IOException("安装包不完整。");
            source.Seek(-footerSize, SeekOrigin.End);
            var reader = new BinaryReader(source);
            long length = reader.ReadInt64();
            if (Encoding.ASCII.GetString(reader.ReadBytes(footerSize - 8)) != Magic
                || length <= 0 || length > source.Length - footerSize)
                throw new IOException("安装包不完整，请重新下载。");
            source.Position = source.Length - footerSize - length;
            using (var target = File.Create(archive))
            using (var hash = SHA256.Create())
            {
                byte[] buffer = new byte[1024 * 1024];
                long copied = 0;
                while (copied < length)
                {
                    int n = source.Read(buffer, 0, (int)Math.Min(buffer.Length, length - copied));
                    if (n == 0) throw new EndOfStreamException();
                    target.Write(buffer, 0, n);
                    hash.TransformBlock(buffer, 0, n, buffer, 0);
                    copied += n;
                    report(1 + (int)(8 * copied / length), "正在校验安装包");
                }
                hash.TransformFinalBlock(new byte[0], 0, 0);
                string digest = BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant();
                if (digest != PayloadHash) throw new IOException("安装包校验失败，请重新下载。");
            }
        }
        report(10, "正在解压 Krita 与插件");
        using (var zip = ZipFile.OpenRead(archive))
        {
            int done = 0;
            string prefix = Path.GetFullPath(work).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            foreach (var entry in zip.Entries)
            {
                string path = Path.GetFullPath(Path.Combine(work, entry.FullName.Replace('/', Path.DirectorySeparatorChar)));
                if (!path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                    throw new IOException("安装包包含无效文件路径。");
                if (entry.Name.Length != 0)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(path));
                    using (var input = entry.Open())
                    using (var output = File.Create(path)) input.CopyTo(output);
                }
                done++;
                if (done % 80 == 0) report(10 + 20 * done / zip.Entries.Count, "正在解压 Krita 与插件");
            }
        }
        File.Delete(archive);
        string system = Environment.GetFolderPath(Environment.SpecialFolder.System);
        string powershellRoot = Path.Combine(system, "WindowsPowerShell", "v1.0");
        var start = new ProcessStartInfo(Path.Combine(powershellRoot, "powershell.exe"),
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + Path.Combine(work, "install-bundle.ps1") + "\""
            + (testRoot == null ? "" : " -TestRoot \"" + testRoot + "\""));
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        start.StandardOutputEncoding = Encoding.UTF8;
        start.StandardErrorEncoding = Encoding.UTF8;
        // Use Windows PowerShell's own modules when launched from PowerShell 7.
        start.EnvironmentVariables["PSModulePath"] = Path.Combine(powershellRoot, "Modules");
        var errors = new StringBuilder();
        using (var process = new Process())
        {
            process.StartInfo = start;
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (e.Data == null) return;
                if (e.Data.StartsWith("MORI_RESULT|")) ResultPath = e.Data.Substring(12);
                if (e.Data.StartsWith("MORI_PROGRESS|"))
                {
                    string[] parts = e.Data.Split(new char[] { '|' }, 3);
                    int p;
                    if (parts.Length == 3 && int.TryParse(parts[1], out p)) report(p, parts[2]);
                }
            };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
            { if (e.Data != null) lock (errors) errors.AppendLine(e.Data); };
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            process.WaitForExit();
            if (process.ExitCode != 0 || String.IsNullOrEmpty(ResultPath) || !File.Exists(ResultPath))
            {
                string log = errors.ToString();
                File.WriteAllText(Path.Combine(work, "install-error.txt"), log, Encoding.UTF8);
                string logRoot = testRoot ?? Path.GetTempPath();
                Directory.CreateDirectory(logRoot);
                string persistentLog = Path.Combine(logRoot, "MoriKrita-install-error.txt");
                File.WriteAllText(persistentLog, log, Encoding.UTF8);
                throw new InvalidOperationException("安装未完成。请查看日志：\n" + persistentLog);
            }
        }
    }
}

internal sealed class SetupForm : Form
{
    readonly Label status = new Label();
    readonly ProgressBar progress = new ProgressBar();
    readonly Button action = new Button();
    bool busy;
    string installed;

    internal SetupForm()
    {
        Text = "Mori / Krita 安装程序";
        ClientSize = new Size(520, 316);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(32, 32, 35);
        ForeColor = Color.FromArgb(239, 239, 242);
        Font = new Font("Microsoft YaHei UI", 10);
        var title = new Label { Text = "MORI / Krita", Font = new Font("Segoe UI", 23, FontStyle.Bold), AutoSize = true, Location = new Point(30, 25) };
        var detail = new Label { Text = "Krita 5.3.4.1\nMori __MORI_VERSION__  ·  AI Diffusion __AI_VERSION__  ·  Vision Tools 2.2.0", AutoSize = true, Location = new Point(33, 88), ForeColor = Color.FromArgb(180, 180, 188) };
        status.SetBounds(33, 157, 452, 45);
        status.Text = "安装后即可从桌面打开 Krita";
        progress.SetBounds(33, 210, 452, 5);
        action.SetBounds(337, 245, 148, 40);
        action.Text = "安装";
        action.FlatStyle = FlatStyle.Flat;
        action.FlatAppearance.BorderSize = 0;
        action.BackColor = Color.FromArgb(189, 172, 239);
        action.ForeColor = Color.FromArgb(37, 32, 47);
        action.Click += ClickInstall;
        Controls.AddRange(new Control[] { title, detail, status, progress, action });
        FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) e.Cancel = true; };
    }

    void UpdateProgress(int p, string text)
    {
        if (IsDisposed) return;
        BeginInvoke((Action)delegate { progress.Value = Math.Max(0, Math.Min(100, p)); status.Text = text; });
    }

    void ClickInstall(object sender, EventArgs e)
    {
        if (installed != null) { Process.Start(new ProcessStartInfo(installed) { UseShellExecute = true }); Close(); return; }
        if (busy) return;
        busy = true;
        action.Enabled = false;
        action.Text = "安装中…";
        var worker = new Thread(delegate()
        {
            try
            {
                string result = Setup.Install(UpdateProgress);
                BeginInvoke((Action)delegate { installed = result; status.Text = "安装完成，Mori 插件已启用。"; progress.Value = 100; action.Text = "打开 Krita"; action.Enabled = true; busy = false; });
            }
            catch (Exception error)
            {
                BeginInvoke((Action)delegate { status.Text = error.Message; action.Text = "重新安装"; action.Enabled = true; busy = false; });
            }
        });
        worker.IsBackground = true;
        worker.Start();
    }
}
