// Real WebView2 shutdown regressions. Uses only a temporary local HTTP fixture.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class HostLifecycleTests
{
    private sealed class Commands : TextReader
    {
        private readonly System.Collections.Concurrent.BlockingCollection<string> lines =
            new System.Collections.Concurrent.BlockingCollection<string>();
        internal void Send(string line) { lines.Add(line); }
        public override string ReadLine() { return lines.Take(); }
    }

    private sealed class Messages : TextWriter
    {
        internal Action<string> Received;
        public override Encoding Encoding { get { return Encoding.UTF8; } }
        public override void WriteLine(string line)
        {
            Action<string> handler = Received;
            if (handler != null) handler(line);
        }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length != 1) return 2;
        string root = Path.GetFullPath(args[0]);
        Directory.CreateDirectory(root);
        TextWriter output = Console.Out;
        Messages messages = new Messages();
        Console.SetOut(messages);
        var failures = new List<string>();
        var records = new List<object>();
        string current = "startup";
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
        Application.ThreadException += delegate(object sender, ThreadExceptionEventArgs e)
        {
            failures.Add(current + ": " + e.Exception.ToString());
        };
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        // Krita can remove the foreign child HWND before WinForms receives
        // WM_DESTROY. A subsequent Close then takes the no-handle Dispose path
        // with no FormClosed/HandleDestroyed event. The shared context must
        // still signal that its message loop is over.
        using (var parent = new Form())
        using (var host = new HostForm(parent.Handle, new Uri("http://127.0.0.1:1/"), Path.Combine(root, "no-handle")))
        using (var context = new HostApplicationContext(host))
        {
            bool loopExited = false, formClosed = false, handleDestroyed = false;
            context.ThreadExit += delegate { loopExited = true; };
            host.FormClosed += delegate { formClosed = true; };
            host.HandleDestroyed += delegate { handleDestroyed = true; };
            host.Close();
            bool passed = loopExited && host.IsDisposed && !formClosed && !handleDestroyed;
            if (!passed) failures.Add("no-handle-close: disposed form did not end its message loop");
            records.Add(new { scenario = "no-handle-close", loopExited = loopExited, passed = passed });
        }
        var reservation = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        reservation.Start();
        int port = ((IPEndPoint)reservation.LocalEndpoint).Port;
        reservation.Stop();
        HttpListener server = new HttpListener();
        string origin = "http://127.0.0.1:" + port.ToString() + "/";
        server.Prefixes.Add(origin);
        server.Start();
        Task.Run(async delegate
        {
            while (server.IsListening)
            {
                try
                {
                    HttpListenerContext request = await server.GetContextAsync();
                    Task.Run(async delegate
                    {
                        try
                        {
                            if (request.Request.Url.AbsolutePath.Contains("slow")) await Task.Delay(600);
                            byte[] html = Encoding.UTF8.GetBytes("<!doctype html><html><head><link rel='icon' href='data:,'></head><body>Local shutdown fixture<img src='/slow-image'><script>setInterval(()=>fetch('/slow-request').catch(()=>{}),40)</script></body></html>");
                            request.Response.ContentType = "text/html; charset=utf-8";
                            request.Response.ContentLength64 = html.Length;
                            await request.Response.OutputStream.WriteAsync(html, 0, html.Length);
                            request.Response.Close();
                        }
                        catch (Exception) { }
                    });
                }
                catch (HttpListenerException) { break; }
                catch (ObjectDisposedException) { break; }
            }
        });
        foreach (string scenario in new[] { "close-initializing", "dispose-initializing", "close-navigation", "close-ready", "dispose-ready", "parent-close-ready", "parent-dispose-ready", "queued-focus-close", "closed-controller-parent" })
        {
            string selection = Environment.GetEnvironmentVariable("MORI_HOST_QA_SCENARIO");
            if (!String.IsNullOrEmpty(selection) && selection != scenario) continue;
            current = scenario;
            int before = failures.Count;
            bool ready = false, closed = false, timedOut = false, actionTaken = false;
            Commands commands = new Commands();
            Console.SetIn(commands);
            var context = new ApplicationContext();
            Form parent = new Form { Text = "Mori shutdown QA", Size = new Size(480, 360), ShowInTaskbar = false, StartPosition = FormStartPosition.Manual, Location = new Point(-2000, -2000) };
            HostForm host = new HostForm(parent.Handle, new Uri(origin + "test"), Path.Combine(root, scenario));
            var finish = new System.Windows.Forms.Timer { Interval = 1200 };
            var watchdog = new System.Windows.Forms.Timer { Interval = 20000 };
            Action teardown = delegate
            {
                if (actionTaken) return;
                actionTaken = true;
                try
                {
                    if (scenario == "queued-focus-close") commands.Send("{\"op\":\"focus\"}");
                    if (scenario == "closed-controller-parent")
                    {
                        // Reproduce the exact SDK state observed on Krita exit:
                        // native controller closed before managed Dispose reads
                        // CoreWebView2.Profile. Reflection is test-only, pinned
                        // to the SDK compiled by this test script.
                        var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
                        var view = (Microsoft.Web.WebView2.WinForms.WebView2)typeof(HostForm).GetField("browser", flags).GetValue(host);
                        var controller = (Microsoft.Web.WebView2.Core.CoreWebView2Controller)typeof(Microsoft.Web.WebView2.WinForms.WebView2).GetField("_coreWebView2Controller", flags).GetValue(view);
                        typeof(HostForm).GetMethod("BeginShutdown", flags).Invoke(host, null);
                        controller.Close();
                        parent.Dispose();
                        host.Dispose();
                    }
                    else if (scenario.StartsWith("parent-close")) parent.Close();
                    else if (scenario.StartsWith("parent-dispose")) parent.Dispose();
                    else if (scenario.StartsWith("dispose")) host.Dispose();
                    else host.Close();
                }
                catch (Exception ex) { failures.Add(scenario + ": " + ex.ToString()); }
                commands.Send("{\"op\":\"close\"}");
                finish.Start();
            };
            finish.Tick += delegate { finish.Stop(); context.ExitThread(); };
            watchdog.Tick += delegate { watchdog.Stop(); timedOut = true; teardown(); };
            messages.Received = delegate(string line)
            {
                File.AppendAllText(Path.Combine(root, "progress.txt"), scenario + ": " + line + Environment.NewLine);
                if (line.Contains("\"closed\"")) closed = true;
                if (line.Contains("\"error\"")) failures.Add(scenario + ": " + line);
                if (line.Contains("\"ready\""))
                {
                    ready = true;
                    if (!scenario.EndsWith("initializing") && scenario != "close-navigation")
                        parent.BeginInvoke(teardown);
                }
            };
            parent.Shown += delegate
            {
                host.Show();
                watchdog.Start();
                if (scenario.EndsWith("initializing")) teardown();
                else if (scenario == "close-navigation")
                {
                    var navigationClose = new System.Windows.Forms.Timer { Interval = 250 };
                    navigationClose.Tick += delegate { navigationClose.Stop(); navigationClose.Dispose(); teardown(); };
                    navigationClose.Start();
                }
            };
            parent.Show();
            Application.Run(context);
            messages.Received = null;
            watchdog.Dispose();
            finish.Dispose();
            try { host.Dispose(); parent.Dispose(); }
            catch (Exception ex) { failures.Add(scenario + " cleanup: " + ex.ToString()); }
            if (timedOut) failures.Add(scenario + ": timed out");
            if (!closed) failures.Add(scenario + ": missing closed acknowledgement");
            if (!scenario.EndsWith("initializing") && scenario != "close-navigation" && !ready) failures.Add(scenario + ": never ready");
            records.Add(new { scenario = scenario, ready = ready, closeReported = closed, passed = failures.Count == before });
        }
        server.Close();
        Console.SetOut(output);
        string result = new JavaScriptSerializer().Serialize(new { passed = failures.Count == 0, scenarios = records, errors = failures, modelCalls = 0 });
        File.WriteAllText(Path.Combine(root, "host-lifecycle-qa.json"), result, new UTF8Encoding(false));
        output.WriteLine(result);
        return failures.Count == 0 ? 0 : 1;
    }
}
