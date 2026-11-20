// Real hidden startup/readiness/visibility tests; loopback-only synthetic UI.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class HostPresentationTests
{
    [DllImport("user32.dll")] private static extern IntPtr GetParent(IntPtr window);
    private sealed class Commands : TextReader
    {
        private readonly System.Collections.Concurrent.BlockingCollection<string> lines = new System.Collections.Concurrent.BlockingCollection<string>();
        internal void Send(string line) { lines.Add(line); }
        public override string ReadLine() { return lines.Take(); }
    }
    private sealed class Messages : TextWriter
    {
        internal Action<string> Received;
        public override Encoding Encoding { get { return Encoding.UTF8; } }
        public override void WriteLine(string line) { if (Received != null) Received(line); }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string output = Path.GetFullPath(args[0]); Directory.CreateDirectory(output);
        TextWriter standard = Console.Out;
        var messages = new Messages(); Console.SetOut(messages);
        var errors = new List<string>();
        bool preparedOnServer = false;
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
        Application.ThreadException += delegate(object sender, ThreadExceptionEventArgs eventArgs) { errors.Add(eventArgs.Exception.ToString()); };
        var reservation = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        reservation.Start(); int port = ((IPEndPoint)reservation.LocalEndpoint).Port; reservation.Stop();
        string origin = "http://127.0.0.1:" + port + "/";
        var server = new HttpListener(); server.Prefixes.Add(origin); server.Start();
        Task.Run(async delegate
        {
            while (server.IsListening)
            {
                try
                {
                    var request = await server.GetContextAsync();
                    if (request.Request.Url.AbsolutePath == "/prepared")
                    {
                        Volatile.Write(ref preparedOnServer, true);
                        request.Response.StatusCode = 204; request.Response.Close(); continue;
                    }
                    byte[] html = Encoding.UTF8.GetBytes("<!doctype html><html><head><link rel='icon' href='data:,'></head><body style='background:#222'><div id='root'><div class='mori-auth-connection'>Starting account</div></div><script>setTimeout(function(){document.getElementById('root').innerHTML='<main>Prepared synthetic Mori workbench</main>';fetch('/prepared');},1400);</script></body></html>");
                    request.Response.ContentType = "text/html";
                    request.Response.ContentLength64 = html.Length;
                    await request.Response.OutputStream.WriteAsync(html, 0, html.Length); request.Response.Close();
                }
                catch (HttpListenerException) { break; }
                catch (ObjectDisposedException) { break; }
            }
        });
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        var commands = new Commands(); Console.SetIn(commands);
        var parent = new Form { Text = "Mori hidden startup QA", Size = new Size(660, 480), ShowInTaskbar = false,
                                StartPosition = FormStartPosition.Manual, Location = new Point(-2500, -2500) };
        var host = new HostForm(parent.Handle, new Uri(origin), Path.Combine(output, "profile"), false);
        var context = new HostApplicationContext(host);
        bool ready = false, beganVisible = false;
        int samples = 0, stage = 0;
        DateTime started = DateTime.UtcNow, changed = DateTime.UtcNow;
        var timer = new System.Windows.Forms.Timer { Interval = 20 };
        messages.Received = delegate(string line)
        {
            File.AppendAllText(Path.Combine(output, "progress.txt"), line + Environment.NewLine);
            if (line.Contains("\"error\"")) errors.Add(line);
            if (!line.Contains("\"ready\"")) return;
            ready = true;
            if (!Volatile.Read(ref preparedOnServer)) errors.Add("Readiness arrived before the fixture finished hydration");
            if ((DateTime.UtcNow - started).TotalMilliseconds < 1200) errors.Add("Revealed before root hydration");
            if (Native.IsWindowVisible(host.Handle)) errors.Add("Visible before caller requested presentation");
            changed = DateTime.UtcNow;
            commands.Send("{\"op\":\"visibility\",\"visible\":true}");
            stage = 1;
        };
        timer.Tick += delegate
        {
            samples++;
            if (host.IsHandleCreated && !host.IsDisposed)
            {
                if (GetParent(host.Handle) != parent.Handle && !errors.Contains("Host escaped native parent")) errors.Add("Host escaped native parent");
                if ((Native.GetWindowStyle(host.Handle) & Native.WS_CHILD) == 0) errors.Add("Host created as a desktop popup");
                if (!ready && Native.IsWindowVisible(host.Handle)) beganVisible = true;
            }
            if ((DateTime.UtcNow - started).TotalSeconds > 15) { errors.Add("Presentation timed out at stage " + stage); host.Close(); }
            if (!ready || (DateTime.UtcNow - changed).TotalMilliseconds < 250) return;
            if (stage == 1)
            {
                if (!Native.IsWindowVisible(host.Handle)) errors.Add("Prepared page did not become visible");
                commands.Send("{\"op\":\"visibility\",\"visible\":false}"); stage = 2;
            }
            else if (stage == 2)
            {
                if (Native.IsWindowVisible(host.Handle)) errors.Add("Hidden mode left browser visible");
                commands.Send("{\"op\":\"visibility\",\"visible\":true}"); stage = 3;
            }
            else if (stage == 3)
            {
                if (!Native.IsWindowVisible(host.Handle)) errors.Add("Returning mode did not restore prepared page");
                parent.Hide(); stage = 4;
            }
            else if (stage == 4)
            {
                if (Native.IsWindowVisible(host.Handle)) errors.Add("Hidden dock left browser visible");
                parent.Show(); stage = 5;
            }
            else if (stage == 5)
            {
                if (!Native.IsWindowVisible(host.Handle)) errors.Add("Restoring dock failed");
                commands.Send("{\"op\":\"close\"}");
                host.Close(); stage = 6;
            }
            changed = DateTime.UtcNow;
        };
        parent.Show(); timer.Start(); Application.Run(context);
        timer.Stop(); timer.Dispose(); host.Dispose(); parent.Dispose(); server.Close();
        if (beganVisible) errors.Add("Observed browser before ready");
        if (!ready || stage != 6) errors.Add("Did not complete visibility sequence");
        string result = new JavaScriptSerializer().Serialize(new { passed = errors.Count == 0, ready = ready,
            hiddenBeforeReady = !beganVisible, sampledWindows = samples, errors = errors, modelCalls = 0 });
        File.WriteAllText(Path.Combine(output, "presentation-qa.json"), result, new UTF8Encoding(false));
        Console.SetOut(standard); standard.WriteLine(result); return errors.Count == 0 ? 0 : 1;
    }
}
