// Copyright (c) Mori. Windows-only WebView2 host for the Krita plugin.
// Build with build.ps1. C# 5 syntax keeps the Windows .NET Framework compiler usable.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class Program
{
    internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    private static readonly object OutputLock = new object();

    internal static void Send(string name, string message)
    {
        lock (OutputLock)
        {
            // Krita can close its pipe before the last native shutdown callback.
            try
            {
                Console.Out.WriteLine(Json.Serialize(new { type = name, message = message }));
                Console.Out.Flush();
            }
            catch (IOException) { }
            catch (ObjectDisposedException) { }
        }
    }

    [STAThread]
    private static int Main()
    {
        try
        {
            // WinExe has no console window. Initialize the redirected pipes explicitly.
            Console.SetIn(new PipeCommandReader(Console.OpenStandardInput()));
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true });
            string line = Console.ReadLine();
            if (String.IsNullOrEmpty(line) || line.Length > 32768)
                throw new ArgumentException("配置无效");
            Dictionary<string, object> config = Json.Deserialize<Dictionary<string, object>>(line);
            Uri url;
            if (!Uri.TryCreate(Convert.ToString(config["url"]), UriKind.Absolute, out url)
                || url.Scheme != "http" || url.Host != "127.0.0.1" || url.Port < 1
                || !String.IsNullOrEmpty(url.UserInfo))
                throw new ArgumentException("只允许连接本机画布桥接服务");
            IntPtr parent = new IntPtr(Convert.ToInt64(config["parent"]));
            if (parent == IntPtr.Zero || !Native.IsWindow(parent))
                throw new ArgumentException("Krita 画布容器已关闭");
            string profile = Path.GetFullPath(Convert.ToString(config["profile"]));
            string tempRoot = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar)
                + Path.DirectorySeparatorChar;
            if (!profile.StartsWith(tempRoot, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("临时浏览器目录无效");
            try { Native.SetProcessDpiAwarenessContext(new IntPtr(-4)); }
            catch (EntryPointNotFoundException) { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            object requested;
            bool visible = !config.TryGetValue("visible", out requested) || Convert.ToBoolean(requested);
            using (HostForm form = new HostForm(parent, url, profile, visible))
            using (HostApplicationContext context = new HostApplicationContext(form))
            {
                Application.Run(context);
            }
            return 0;
        }
        catch (Exception ex)
        {
            Send("error", "无法启动内嵌画布（" + ex.GetType().Name + "）。请检查 WebView2 Runtime。");
            return 1;
        }
    }
}

internal sealed class PipeCommandReader : TextReader
{
    private readonly Stream input;
    private readonly UTF8Encoding encoding = new UTF8Encoding(false, true);
    private readonly byte[] buffer = new byte[4096];
    private int offset, count;

    internal PipeCommandReader(Stream stream) { input = stream; }

    public override string ReadLine()
    {
        // Bound every JSON line, including the initial private configuration.
        var bytes = new List<byte>();
        int value;
        while ((value = ReadPipeByte()) >= 0)
        {
            if (value == 10) break;
            if (bytes.Count >= 32768) throw new InvalidDataException("Command too long");
            bytes.Add((byte)value);
        }
        if (value < 0 && bytes.Count == 0) return null;
        if (bytes.Count > 0 && bytes[bytes.Count - 1] == 13) bytes.RemoveAt(bytes.Count - 1);
        return encoding.GetString(bytes.ToArray());
    }

    private int ReadPipeByte()
    {
        if (offset >= count)
        {
            // Qt provides an overlapped Windows pipe. A pending synchronous
            // ConsoleStream read can stall even after the next command arrives.
            // Read only currently available bytes, so visibility/reload/close
            // remain responsive without replacing the private pipe transport.
            uint available;
            while (true)
            {
                if (!Native.PeekNamedPipe(Native.GetStdHandle(-10), IntPtr.Zero, 0, IntPtr.Zero, out available, IntPtr.Zero))
                    return -1;
                if (available > 0) break;
                System.Threading.Thread.Sleep(10);
            }
            count = input.Read(buffer, 0, (int)Math.Min(available, (uint)buffer.Length));
            offset = 0;
            if (count == 0) return -1;
        }
        return buffer[offset++];
    }
}

internal sealed class HostApplicationContext : ApplicationContext
{
    internal HostApplicationContext(HostForm form) : base(form)
    {
        // A foreign HWND parent may disappear without FormClosed. If it is
        // already gone, Close can dispose a form without a managed HWND and
        // without raising HandleDestroyed. Either terminal event ends the loop.
        form.HandleDestroyed += delegate { if (form.IsShuttingDown) ExitThread(); };
        form.Disposed += delegate { ExitThread(); };
    }
}

internal sealed class HostForm : Form
{
    private readonly IntPtr parentWindow;
    private readonly Uri initialUrl;
    private readonly string origin;
    private readonly string profileDirectory;
    private readonly WebView2 browser;
    private readonly Timer resizeTimer;
    private readonly TextReader commandInput;
    private readonly string gestureNonce = Guid.NewGuid().ToString("N");
    private DateTime downloadGestureUntil = DateTime.MinValue;
    private volatile bool closed;
    private bool closedReported;
    private bool browserReleaseAttempted;
    private CoreWebView2 coreView;
    private CoreWebView2Environment browserEnvironment;
    private bool requestedVisible;
    private bool pageReady;
    private bool initializationStarted;
    private string navigationNonce;
    private int lastWidth = -1, lastHeight = -1;

    internal bool IsShuttingDown { get { return closed; } }

    private bool BrowserAvailable
    {
        get { return !closed && !IsDisposed && !Disposing && !browser.IsDisposed && !browser.Disposing; }
    }

    internal HostForm(IntPtr parent, Uri url, string profile, bool visible = true)
    {
        parentWindow = parent;
        initialUrl = url;
        origin = url.GetLeftPart(UriPartial.Authority);
        profileDirectory = profile;
        requestedVisible = visible;
        commandInput = Console.In;
        TopLevel = false;
        Text = "Mori Krita 画布";
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        Location = new Point(0, 0);
        Size = new Size(800, 600);
        BackColor = Color.FromArgb(34, 34, 34);
        AutoScaleMode = AutoScaleMode.None;
        browser = new HostedWebView2(delegate { return closed || !Native.IsWindow(parentWindow); });
        browser.Dock = DockStyle.Fill;
        browser.DefaultBackgroundColor = BackColor;
        Controls.Add(browser);
        resizeTimer = new Timer();
        resizeTimer.Interval = 100;
        resizeTimer.Tick += delegate { SyncBounds(); };
    }

    protected override bool ShowWithoutActivation { get { return true; } }

    protected override CreateParams CreateParams
    {
        get
        {
            CreateParams value = base.CreateParams;
            // Create as a hidden child from the first HWND. Reparenting a shown
            // top-level Form briefly exposes an 800x600 black window on desktop.
            value.Parent = parentWindow;
            value.Style = (int)(((long)value.Style | Native.WS_CHILD | Native.WS_CLIPCHILDREN | Native.WS_CLIPSIBLINGS)
                & ~Native.WS_POPUP & ~Native.WS_VISIBLE);
            value.ExStyle = (value.ExStyle | 0x08000000 | 0x00000080) & ~0x00040000;
            value.X = 0; value.Y = 0;
            Native.Rect rect;
            if (Native.GetClientRect(parentWindow, out rect))
            {
                value.Width = Math.Max(1, rect.Right); value.Height = Math.Max(1, rect.Bottom);
            }
            return value;
        }
    }

    protected override void SetVisibleCore(bool value)
    {
        if (value && !IsHandleCreated && !closed) CreateHandle();
        base.SetVisibleCore(value && pageReady && requestedVisible && !closed
            && Native.IsWindowVisible(parentWindow));
    }

    protected override void OnHandleCreated(EventArgs args)
    {
        base.OnHandleCreated(args);
        if (initializationStarted || closed) return;
        initializationStarted = true;
        // A hidden Form has no Shown event. Initialize through its message loop.
        BeginInvoke((Action)(async delegate
        {
            if (!BrowserAvailable) return;
            resizeTimer.Start();
            Task.Run((Action)ReadCommands);
            await InitializeBrowser();
        }));
    }

    private void ApplyVisibility()
    {
        if (!BrowserAvailable || !IsHandleCreated) return;
        bool visible = requestedVisible && pageReady && Native.IsWindowVisible(parentWindow);
        if (visible != Visible) base.SetVisibleCore(visible);
    }

    private void BeginShutdown()
    {
        if (closed) return;
        // Set this before touching WebView2: disposal may re-enter callbacks.
        closed = true;
        downloadGestureUntil = DateTime.MinValue;
        resizeTimer.Stop();
        CoreWebView2 core = coreView;
        coreView = null;
        browserEnvironment = null;
        if (core == null) return;
        try
        {
            core.NavigationStarting -= OnNavigationStarting;
            core.NewWindowRequested -= OnNewWindowRequested;
            core.PermissionRequested -= OnPermissionRequested;
            core.DownloadStarting -= OnDownloadStarting;
            core.WebMessageReceived -= OnWebMessageReceived;
            core.WebResourceRequested -= OnWebResourceRequested;
            core.NavigationCompleted -= OnNavigationCompleted;
            core.ProcessFailed -= OnProcessFailed;
        }
        catch (InvalidOperationException) { } // Native parent already destroyed.
        catch (COMException) { }
    }

    private void ReportClosed()
    {
        if (closedReported) return;
        closedReported = true;
        Program.Send("closed", "画布已关闭");
    }

    protected override void OnFormClosing(FormClosingEventArgs args)
    {
        BeginShutdown();
        base.OnFormClosing(args);
    }

    protected override void OnHandleDestroyed(EventArgs args)
    {
        if (!RecreatingHandle) BeginShutdown();
        base.OnHandleDestroyed(args);
        if (closed) ReportClosed();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            BeginShutdown();
            resizeTimer.Dispose();
            ReleaseBrowser();
        }
        base.Dispose(disposing);
        if (disposing) ReportClosed();
    }

    private void ReleaseBrowser()
    {
        if (browserReleaseAttempted) return;
        browserReleaseAttempted = true;
        // Explicit ownership prevents Form.Dispose from retrying a partial SDK
        // teardown. When Krita exits first, WebView2's native controller is
        // already closed; some SDK versions still read its Profile in Dispose.
        Controls.Remove(browser);
        try { browser.Dispose(); }
        catch (InvalidOperationException ex)
        {
            if (!HostShutdown.ClosedController(ex, closed, Native.IsWindow(parentWindow))) throw;
        }
        catch (COMException ex)
        {
            if (!HostShutdown.ClosedController(ex, closed, Native.IsWindow(parentWindow))) throw;
        }
        // The host message loop/process ends after this form is released. No
        // retry touches the already-closed native controller or user profile.
    }

    private bool SameOrigin(string value)
    {
        return BrowserPolicy.SameOrigin(value, origin);
    }

    private void OpenExternal(string value, bool userInitiated)
    {
        if (closed || !BrowserPolicy.CanOpenExternal(value, userInitiated)) return;
        try { Process.Start(new ProcessStartInfo(value) { UseShellExecute = true }); }
        catch (Exception) { Program.Send("notice", "无法打开默认浏览器，请稍后重试。"); }
    }

    private void ReadGesture(CoreWebView2WebMessageReceivedEventArgs args)
    {
        if (!BrowserAvailable || !SameOrigin(args.Source)) return;
        string message = args.WebMessageAsJson;
        if (String.IsNullOrEmpty(message) || message.Length > 256) return;
        try
        {
            Dictionary<string, object> value = Program.Json.Deserialize<Dictionary<string, object>>(message);
            object kind, nonce;
            if (value != null && value.Count == 2 && value.TryGetValue("type", out kind)
                && value.TryGetValue("nonce", out nonce) && Convert.ToString(kind) == "export-gesture"
                && Convert.ToString(nonce) == gestureNonce)
                downloadGestureUntil = DateTime.UtcNow.AddSeconds(120);
        }
        catch (ArgumentException) { }
        catch (InvalidOperationException) { }
    }

    private void BeginDownload(CoreWebView2DownloadStartingEventArgs args)
    {
        if (!BrowserAvailable) return;
        args.Handled = true;
        args.Cancel = true;
        string name = BrowserPolicy.DownloadName(args.ResultFilePath);
        if (closed || DateTime.UtcNow > downloadGestureUntil || name == null
            || !BrowserPolicy.CanDownload(args.DownloadOperation.Uri, origin)
            || args.DownloadOperation.TotalBytesToReceive > BrowserPolicy.MaxDownloadBytes)
            return;
        downloadGestureUntil = DateTime.MinValue;
        CoreWebView2Deferral deferral = args.GetDeferral();
        try
        {
            // Native dialogs run after this WebView callback unwinds. Holding a
            // deferral avoids re-entering WebView2 from a modal message loop.
            BeginInvoke((Action)delegate
            {
                try
                {
                    if (!BrowserAvailable) return;
                    using (SaveFileDialog dialog = new SaveFileDialog())
                    {
                        dialog.Title = "保存 Mori 导出文件";
                        dialog.FileName = name;
                        dialog.Filter = "Mori 导出文件 (*" + Path.GetExtension(name) + ")|*" + Path.GetExtension(name);
                        dialog.AddExtension = true;
                        dialog.DefaultExt = Path.GetExtension(name).TrimStart('.');
                        dialog.OverwritePrompt = true;
                        dialog.CheckPathExists = true;
                        dialog.RestoreDirectory = true;
                        if (dialog.ShowDialog(this) != DialogResult.OK || !BrowserAvailable) return;
                        args.ResultFilePath = dialog.FileName;
                        args.Cancel = false;
                        CoreWebView2DownloadOperation operation = args.DownloadOperation;
                        operation.BytesReceivedChanged += delegate
                        {
                            if (!BrowserAvailable) return;
                            if (operation.BytesReceived > BrowserPolicy.MaxDownloadBytes) operation.Cancel();
                        };
                    }
                }
                catch (Exception)
                {
                    try { args.Cancel = true; }
                    catch (COMException) { }
                    catch (InvalidOperationException) { }
                    if (!closed) Program.Send("notice", "导出未完成，请重新选择保存位置。");
                }
                finally
                {
                    try { deferral.Complete(); }
                    catch (COMException) { }
                    catch (InvalidOperationException) { }
                }
            });
        }
        catch (InvalidOperationException)
        {
            try { deferral.Complete(); }
            catch (COMException) { }
            catch (InvalidOperationException) { }
        }
    }

    private void SyncBounds()
    {
        if (!BrowserAvailable) return;
        if (!Native.IsWindow(parentWindow)) { Close(); return; }
        // WinForms may park a non-managed child during handle creation or Show.
        // Restore the foreign Qt parent while this child is still hidden.
        if (Native.GetParent(Handle) != parentWindow) Native.SetParent(Handle, parentWindow);
        Native.Rect rect;
        if (Native.GetClientRect(parentWindow, out rect))
        {
            int width = Math.Max(1, rect.Right), height = Math.Max(1, rect.Bottom);
            if (width != lastWidth || height != lastHeight)
            {
                Native.MoveWindow(Handle, 0, 0, width, height, true);
                lastWidth = width; lastHeight = height;
            }
        }
        ApplyVisibility();
    }

    private async Task InitializeBrowser()
    {
        try
        {
            if (!BrowserAvailable) return;
            SyncBounds();
            CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, profileDirectory);
            if (!BrowserAvailable) return;
            await browser.EnsureCoreWebView2Async(environment);
            if (!BrowserAvailable) return;
            CoreWebView2 core = browser.CoreWebView2;
            coreView = core;
            browserEnvironment = environment;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.AreDefaultScriptDialogsEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsWebMessageEnabled = true;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.Settings.IsZoomControlEnabled = false;
            core.NavigationStarting += OnNavigationStarting;
            core.NewWindowRequested += OnNewWindowRequested;
            core.PermissionRequested += OnPermissionRequested;
            core.DownloadStarting += OnDownloadStarting;
            core.WebMessageReceived += OnWebMessageReceived;
            // Only a real button/link pointer or keyboard gesture opens a short
            // export opportunity. Messages cannot supply URLs or local paths.
            string gestureScript = "(function(){if(window.top!==window)return;function send(e){"
                + "if(!e.isTrusted||!e.target||!e.target.closest||!e.target.closest('button,a,[role=menuitem]'))return;"
                + "if(e.type==='keydown'&&(e.repeat||e.isComposing||(e.key!=='Enter'&&e.key!==' ')))return;"
                + "if(e.type==='pointerdown'&&e.button!==0)return;"
                + "window.chrome.webview.postMessage({type:'export-gesture',nonce:'" + gestureNonce + "'});}"
                + "document.addEventListener('pointerdown',send,true);document.addEventListener('keydown',send,true);})();";
            await core.AddScriptToExecuteOnDocumentCreatedAsync(gestureScript);
            if (!BrowserAvailable) return;
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
            core.WebResourceRequested += OnWebResourceRequested;
            core.NavigationCompleted += OnNavigationCompleted;
            core.ProcessFailed += OnProcessFailed;
            core.Navigate(initialUrl.AbsoluteUri);
        }
        catch (Exception ex)
        {
            if (!BrowserAvailable) return; // A pending initialization completed after close.
            Program.Send("error", "内嵌画布初始化失败（" + ex.GetType().Name + "）。请检查 WebView2 Runtime。");
            Close();
        }
    }

    private void OnNavigationStarting(object sender, CoreWebView2NavigationStartingEventArgs args)
    {
        if (!BrowserAvailable) return;
        if (!SameOrigin(args.Uri))
        {
            args.Cancel = true;
            OpenExternal(args.Uri, args.IsUserInitiated);
            return;
        }
        navigationNonce = Guid.NewGuid().ToString("N");
        pageReady = false;
        ApplyVisibility();
        Program.Send("loading", "正在载入 Mori 页面…");
    }

    private void OnNewWindowRequested(object sender, CoreWebView2NewWindowRequestedEventArgs args)
    {
        if (!BrowserAvailable) return;
        args.Handled = true;
        OpenExternal(args.Uri, args.IsUserInitiated);
    }

    private void OnPermissionRequested(object sender, CoreWebView2PermissionRequestedEventArgs args)
    {
        if (BrowserAvailable) args.State = CoreWebView2PermissionState.Deny;
    }

    private void OnDownloadStarting(object sender, CoreWebView2DownloadStartingEventArgs args)
    {
        BeginDownload(args);
    }

    private void OnWebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        ReadGesture(args);
        if (!BrowserAvailable || !SameOrigin(args.Source) || pageReady) return;
        string message = args.WebMessageAsJson;
        if (String.IsNullOrEmpty(message) || message.Length > 256) return;
        try
        {
            Dictionary<string, object> value = Program.Json.Deserialize<Dictionary<string, object>>(message);
            object kind, nonce;
            if (value != null && value.Count == 2 && value.TryGetValue("type", out kind)
                && value.TryGetValue("nonce", out nonce) && navigationNonce != null
                && Convert.ToString(kind) == "mori-ui-ready" && Convert.ToString(nonce) == navigationNonce)
            {
                pageReady = true;
                SyncBounds();
                Program.Send("ready", "Mori 工作台已准备好");
            }
        }
        catch (ArgumentException) { }
        catch (InvalidOperationException) { }
    }

    private void OnWebResourceRequested(object sender, CoreWebView2WebResourceRequestedEventArgs args)
    {
        if (!BrowserAvailable) return;
        string uri = args.Request.Uri;
        if (!SameOrigin(uri) && !uri.StartsWith("data:", StringComparison.OrdinalIgnoreCase)
            && !BrowserPolicy.LocalBlob(uri, origin)
            && !BrowserPolicy.CanLoadInspirationImage(uri, args.ResourceContext == CoreWebView2WebResourceContext.Image))
            args.Response = browserEnvironment.CreateWebResourceResponse(null, 403, "Forbidden", "Content-Type: text/plain\r\n");
    }

    private async void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs args)
    {
        if (!BrowserAvailable) return;
        if (!args.IsSuccess || args.HttpStatusCode >= 400)
        {
            Program.Send("error", "画布加载失败（" + args.WebErrorStatus.ToString() + "）。");
            return;
        }
        string nonce = navigationNonce;
        // NavigationCompleted alone can precede React's first render and account
        // hydration. Wait for usable DOM and fonts; timers work while hidden,
        // whereas requestAnimationFrame can pause in a hidden WebView.
        string script = "(function(){if(window.top!==window)return;var sent=false;"
            + "var timer=setInterval(check,50),watch=new MutationObserver(check);watch.observe(document,{childList:true,subtree:true});"
            + "function check(){if(sent||document.readyState==='loading')return;var root=document.getElementById('root'),target=root||document.body;"
            + "if(!target||!target.childElementCount)return;"
            + "if(root&&!root.firstElementChild)return;var connection=root&&root.querySelector('.mori-auth-connection');"
            + "if(root&&root.querySelector('.loading-screen')||connection&&!connection.querySelector('button'))return;"
            + "sent=true;clearInterval(timer);watch.disconnect();"
            + "Promise.resolve(document.fonts?document.fonts.ready:null).then(function(){setTimeout(function(){"
            + "window.chrome.webview.postMessage({type:'mori-ui-ready',nonce:'" + nonce + "'});},80);});}check();})();";
        try { await coreView.ExecuteScriptAsync(script); }
        catch (InvalidOperationException) { if (BrowserAvailable) Program.Send("error", "画布尚未准备好，请重新加载。"); }
        catch (COMException) { if (BrowserAvailable) Program.Send("error", "画布尚未准备好，请重新加载。"); }
    }

    private void OnProcessFailed(object sender, CoreWebView2ProcessFailedEventArgs args)
    {
        if (BrowserAvailable) Program.Send("error", "画布引擎异常，请重新打开画布。");
    }

    private void SetEmbeddedVisible(bool visible)
    {
        if (closed) return;
        requestedVisible = visible;
        SyncBounds();
    }

    private void ReadCommands()
    {
        try
        {
            string line;
            while ((line = commandInput.ReadLine()) != null)
            {
                if (line.Length > 32768) break;
                Dictionary<string, object> command = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(line);
                object operation;
                if (!command.TryGetValue("op", out operation)) continue;
                string op = Convert.ToString(operation);
                if (op == "close") break;
                if (op == "visibility")
                {
                    object value;
                    bool visible;
                    if (command.TryGetValue("visible", out value)
                        && Boolean.TryParse(Convert.ToString(value), out visible))
                    {
                        BeginInvoke((Action)delegate
                        {
                            if (BrowserAvailable) SetEmbeddedVisible(visible);
                        });
                    }
                    continue;
                }
                if (op == "focus" && !closed)
                    BeginInvoke((Action)delegate { if (BrowserAvailable) browser.Focus(); });
                if (op == "reload" && !closed)
                    BeginInvoke((Action)delegate
                    {
                        if (BrowserAvailable && coreView != null) coreView.Reload();
                    });
            }
        }
        catch (Exception) { }
        if (!closed)
        {
            try { BeginInvoke((Action)delegate { if (!closed) Close(); }); }
            catch (InvalidOperationException) { }
        }
    }
}

internal static class BrowserPolicy
{
    internal const long MaxDownloadBytes = 256L * 1024L * 1024L;

    internal static bool SameOrigin(string value, string origin)
    {
        Uri uri;
        return Uri.TryCreate(value, UriKind.Absolute, out uri)
            && uri.GetLeftPart(UriPartial.Authority).Equals(origin, StringComparison.OrdinalIgnoreCase)
            && String.IsNullOrEmpty(uri.UserInfo);
    }

    internal static bool LocalBlob(string value, string origin)
    {
        return value != null && value.StartsWith("blob:" + origin + "/", StringComparison.OrdinalIgnoreCase)
            && Regex.IsMatch(value.Substring(("blob:" + origin + "/").Length), @"^[a-fA-F0-9-]{36}$");
    }

    internal static bool CanLoadInspirationImage(string value, bool isImage)
    {
        Uri uri;
        return isImage && Uri.TryCreate(value, UriKind.Absolute, out uri)
            && uri.Scheme == "https" && uri.IsDefaultPort && String.IsNullOrEmpty(uri.UserInfo)
            && uri.Host.Equals("i.pinimg.com", StringComparison.OrdinalIgnoreCase)
            && String.IsNullOrEmpty(uri.Query) && String.IsNullOrEmpty(uri.Fragment)
            && Regex.IsMatch(uri.AbsolutePath, @"^/(?:[A-Za-z0-9_-]+/)+[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp)$", RegexOptions.IgnoreCase);
    }

    internal static bool CanOpenExternal(string value, bool userInitiated)
    {
        Uri uri;
        return userInitiated && Uri.TryCreate(value, UriKind.Absolute, out uri)
            && uri.Scheme == "https" && uri.IsDefaultPort && String.IsNullOrEmpty(uri.UserInfo)
            && String.IsNullOrEmpty(uri.Fragment)
            && ((uri.Host.Equals("www.pinterest.com", StringComparison.OrdinalIgnoreCase)
                 && String.IsNullOrEmpty(uri.Query)
                 && Regex.IsMatch(uri.AbsolutePath, @"^/pin/[0-9]{1,30}/?$"))
                || (uri.Host.Equals("sub2api.izhe.top", StringComparison.OrdinalIgnoreCase)
                    && uri.AbsolutePath == "/register" && uri.Query == "?aff=YD38MU9RTR3K"));
    }

    internal static bool CanDownload(string value, string origin)
    {
        Uri uri;
        return LocalBlob(value, origin) || SameOrigin(value, origin)
            && Uri.TryCreate(value, UriKind.Absolute, out uri)
            && Regex.IsMatch(uri.AbsolutePath, @"^/media/[a-f0-9-]+\.(?:png|jpg|webp)$");
    }

    internal static string DownloadName(string proposed)
    {
        if (String.IsNullOrEmpty(proposed)) return null;
        string name;
        try { name = Path.GetFileName(proposed); }
        catch (ArgumentException) { return null; }
        string extension = Path.GetExtension(name).ToLowerInvariant();
        if (!Regex.IsMatch(extension, @"^\.(?:png|jpg|jpeg|webp|svg|psd|zip|json)$")) return null;
        string stem = Path.GetFileNameWithoutExtension(name);
        foreach (char invalid in Path.GetInvalidFileNameChars()) stem = stem.Replace(invalid, '_');
        stem = stem.Trim().TrimEnd('.', ' ');
        if (stem.Length == 0) stem = "Mori";
        if (Regex.IsMatch(stem, @"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", RegexOptions.IgnoreCase)) stem = "_" + stem;
        if (stem.Length > 150) stem = stem.Substring(0, 150);
        return stem + extension;
    }
}

internal sealed class HostedWebView2 : WebView2
{
    private readonly Func<bool> shuttingDown;

    internal HostedWebView2(Func<bool> state) { shuttingDown = state; }

    protected override void OnHandleDestroyed(EventArgs args)
    {
        try { base.OnHandleDestroyed(args); }
        catch (InvalidOperationException ex)
        {
            // The SDK's own HandleDestroyed event also writes IsVisible on
            // the closed controller. This boundary is only native destruction;
            // normal rendering, navigation and other HRESULTs remain errors.
            if (!HostShutdown.ClosedController(ex, shuttingDown(), false)) throw;
        }
        catch (COMException ex)
        {
            if (!HostShutdown.ClosedController(ex, shuttingDown(), false)) throw;
        }
    }
}

internal static class HostShutdown
{
    internal static bool ClosedController(Exception error, bool shuttingDown, bool parentExists)
    {
        if (!shuttingDown || parentExists) return false;
        COMException nativeError = error as COMException;
        if (nativeError == null && error is InvalidOperationException)
            nativeError = error.InnerException as COMException;
        return nativeError != null && nativeError.ErrorCode == unchecked((int)0x8007139F);
    }
}

internal static class Native
{
    [DllImport("kernel32.dll")] internal static extern IntPtr GetStdHandle(int handle);
    [DllImport("kernel32.dll")] internal static extern bool PeekNamedPipe(IntPtr handle, IntPtr data, uint size, IntPtr read, out uint available, IntPtr remaining);
    internal const long WS_CHILD = 0x40000000L;
    internal const long WS_VISIBLE = 0x10000000L;
    internal const long WS_POPUP = 0x80000000L;
    internal const long WS_CLIPCHILDREN = 0x02000000L;
    internal const long WS_CLIPSIBLINGS = 0x04000000L;
    [StructLayout(LayoutKind.Sequential)]
    internal struct Rect { internal int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] internal static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] internal static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] internal static extern IntPtr GetParent(IntPtr hWnd);
    [DllImport("user32.dll")] internal static extern bool GetClientRect(IntPtr hWnd, out Rect rect);
    [DllImport("user32.dll")] internal static extern IntPtr SetParent(IntPtr child, IntPtr parent);
    [DllImport("user32.dll")] internal static extern bool MoveWindow(IntPtr hWnd, int x, int y, int width, int height, bool repaint);
    [DllImport("user32.dll")] internal static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] private static extern IntPtr GetWindowLongPtr(IntPtr hWnd, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] private static extern IntPtr SetWindowLongPtr(IntPtr hWnd, int index, IntPtr value);
    internal static long GetWindowStyle(IntPtr window) { return GetWindowLongPtr(window, -16).ToInt64(); }
    internal static void SetWindowStyle(IntPtr window, long style) { SetWindowLongPtr(window, -16, new IntPtr(style)); }
}
