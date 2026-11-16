// Local-only integration smoke test. Does not contact Mori or use real accounts.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class SmokeTest
{
    [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr window, uint command);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length < 2 || args.Length > 3) { Console.Error.WriteLine("Expected host.exe, test profile and optional shutdown mode"); return 2; }
        string mode = args.Length == 3 ? args[2] : "parent";
        if (mode != "parent" && mode != "command" && mode != "eof" && mode != "initializing") return 2;
        int result = 1;
        bool hostError = false;
        HttpListener server = new HttpListener();
        // Choose an unused loopback port without relying on a fixed developer port.
        var portReservation = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        portReservation.Start();
        int port = ((IPEndPoint)portReservation.LocalEndpoint).Port;
        portReservation.Stop();
        string url = "http://127.0.0.1:" + port.ToString() + "/test/";
        server.Prefixes.Add("http://127.0.0.1:" + port.ToString() + "/");
        server.Start();
        Task.Run(async delegate
        {
            while (server.IsListening)
            {
                try
                {
                    HttpListenerContext request = await server.GetContextAsync();
                    if (request.Request.Url.AbsolutePath.Contains("slow")) await Task.Delay(500);
                    byte[] html = Encoding.UTF8.GetBytes("<!doctype html><html><meta charset='utf-8'><body style='background:#101112;color:#e8e8ec;font:24px Segoe UI;padding:36px'><h1>Mori × Krita</h1><p>WebView2 embedded canvas smoke test</p><canvas width='320' height='140' style='background:#5847ff;border-radius:20px'></canvas><p>Local fixture · no account or model calls</p><script>setInterval(()=>fetch('/slow-request').catch(()=>{}),40)</script></body></html>");
                    request.Response.ContentType = "text/html; charset=utf-8";
                    request.Response.ContentLength64 = html.Length;
                    await request.Response.OutputStream.WriteAsync(html, 0, html.Length);
                    request.Response.Close();
                }
                catch (HttpListenerException) { break; }
                catch (ObjectDisposedException) { break; }
            }
        });
        Application.EnableVisualStyles();
        Form form = new Form { Text = "Mori WebView2 smoke test", Size = new Size(900, 650), StartPosition = FormStartPosition.CenterScreen };
        Process helper = null;
        Timer timeout = new Timer { Interval = 20000 };
        timeout.Tick += delegate { Console.WriteLine("FAIL: readiness timed out"); form.Close(); };
        form.Shown += delegate
        {
            ProcessStartInfo start = new ProcessStartInfo(args[0]);
            start.WorkingDirectory = Path.GetDirectoryName(args[0]);
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardInput = true;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            helper = new Process { StartInfo = start, EnableRaisingEvents = true };
            helper.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
            {
                if (e.Data == null) return;
                Console.WriteLine(e.Data);
                if (e.Data.Contains("\"error\"")) hostError = true;
                if (e.Data.Contains("\"ready\""))
                {
                    form.BeginInvoke((Action)delegate
                    {
                        IntPtr child = GetWindow(form.Handle, 5);
                        uint pid;
                        GetWindowThreadProcessId(child, out pid);
                        result = child != IntPtr.Zero && pid == helper.Id ? 0 : 1;
                        Console.WriteLine(result == 0 ? "PASS: WebView2 ready and embedded under native parent" : "FAIL: host not embedded");
                        timeout.Stop();
                        Timer close = new Timer { Interval = 200 };
                        close.Tick += delegate
                        {
                            close.Stop(); close.Dispose();
                            if (mode == "command") { helper.StandardInput.WriteLine("{\"op\":\"close\"}"); helper.StandardInput.Flush(); }
                            else if (mode == "eof") helper.StandardInput.Close();
                            else form.Close();
                        };
                        close.Start();
                    });
                }
            };
            helper.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Console.WriteLine("STDERR: " + e.Data); };
            helper.Exited += delegate
            {
                Console.WriteLine("Host exit: " + helper.ExitCode.ToString());
                if ((mode == "command" || mode == "eof") && !form.IsDisposed)
                {
                    try { form.BeginInvoke((Action)delegate { form.Close(); }); }
                    catch (InvalidOperationException) { }
                }
            };
            helper.Start();
            helper.BeginOutputReadLine();
            helper.BeginErrorReadLine();
            helper.StandardInput.WriteLine(new JavaScriptSerializer().Serialize(new { url = url, parent = form.Handle.ToInt64(), profile = args[1] }));
            helper.StandardInput.Flush();
            timeout.Start();
            if (mode == "initializing")
            {
                Timer earlyClose = new Timer { Interval = 50 };
                earlyClose.Tick += delegate { earlyClose.Stop(); earlyClose.Dispose(); result = 0; form.Close(); };
                earlyClose.Start();
            }
        };
        Application.Run(form);
        timeout.Stop();
        server.Close();
        if (helper != null)
        {
            if (!helper.WaitForExit(4000))
            {
                Console.WriteLine("FAIL: host survived parent shutdown");
                result = 1;
                helper.Kill();
                helper.WaitForExit(1000);
            }
            else if (helper.ExitCode != 0)
            {
                Console.WriteLine("FAIL: host shutdown exit code " + helper.ExitCode.ToString());
                result = 1;
            }
            helper.Dispose();
        }
        if (hostError) result = 1;
        Console.WriteLine((result == 0 ? "PASS: " : "FAIL: ") + mode + " shutdown");
        return result;
    }
}
