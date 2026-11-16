// Independent owner-process regressions. The owner exits before the host;
// unlike an in-process Form test, this invalidates WebView2's native controller.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class OwnerExitTests
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--owner")
        {
            Application.EnableVisualStyles();
            Form owner = new Form { Text = "Synthetic native owner", Size = new Size(480, 360), ShowInTaskbar = false,
                StartPosition = FormStartPosition.Manual, Location = new Point(-2000, -2000) };
            Panel container = new Panel { Dock = DockStyle.Fill };
            owner.Controls.Add(container);
            owner.Shown += delegate
            {
                Console.WriteLine(container.Handle.ToInt64());
                Console.Out.Flush();
                Task.Run(delegate
                {
                    string command = Console.ReadLine();
                    owner.BeginInvoke((Action)delegate
                    {
                        if (command == "container")
                        {
                            container.Dispose();
                            var finish = new System.Windows.Forms.Timer { Interval = 2000 };
                            finish.Tick += delegate { finish.Stop(); finish.Dispose(); owner.Close(); };
                            finish.Start();
                        }
                        else owner.Close();
                    });
                });
            };
            Application.Run(owner);
            return 0;
        }
        if (args.Length != 2) return 2;
        string executable = Path.GetFullPath(args[0]);
        string output = Path.GetFullPath(args[1]);
        Directory.CreateDirectory(output);
        var reservation = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        reservation.Start();
        int port = ((IPEndPoint)reservation.LocalEndpoint).Port;
        reservation.Stop();
        string origin = "http://127.0.0.1:" + port.ToString() + "/";
        HttpListener server = new HttpListener();
        server.Prefixes.Add(origin);
        server.Start();
        Task.Run(async delegate
        {
            while (server.IsListening)
            {
                try
                {
                    HttpListenerContext request = await server.GetContextAsync();
                    byte[] html = Encoding.UTF8.GetBytes("<!doctype html><body>Local independent-owner fixture</body>");
                    request.Response.ContentType = "text/html";
                    request.Response.OutputStream.Write(html, 0, html.Length);
                    request.Response.Close();
                }
                catch (HttpListenerException) { break; }
                catch (ObjectDisposedException) { break; }
            }
        });
        var records = new List<object>();
        bool allPassed = true;
        foreach (string mode in new[] { "exit", "container" })
        {
            var logs = new List<string>();
            bool ready = false, closed = false, error = false;
            using (var readyEvent = new ManualResetEvent(false))
            using (var owner = new Process())
            using (var host = new Process())
            {
                owner.StartInfo = StartInfo(Application.ExecutablePath);
                owner.StartInfo.Arguments = "--owner";
                owner.Start();
                string handle = owner.StandardOutput.ReadLine();
                host.StartInfo = StartInfo(executable);
                host.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (e.Data == null) return;
                    lock (logs) logs.Add(e.Data);
                    if (e.Data.Contains("\"closed\"")) closed = true;
                    if (e.Data.Contains("\"error\"")) error = true;
                    if (e.Data.Contains("\"ready\"")) { ready = true; readyEvent.Set(); }
                };
                host.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (e.Data != null) { lock (logs) logs.Add("STDERR: " + e.Data); error = true; }
                };
                host.Start();
                host.BeginOutputReadLine();
                host.BeginErrorReadLine();
                host.StandardInput.WriteLine(new JavaScriptSerializer().Serialize(
                    new { parent = Int64.Parse(handle), url = origin,
                          profile = Path.Combine(Path.GetTempPath(), "Mori-owner-qa-" + Guid.NewGuid().ToString("N")) }));
                host.StandardInput.Flush();
                readyEvent.WaitOne(20000);
                owner.StandardInput.WriteLine(mode);
                owner.StandardInput.Flush();
                bool ownerExited = owner.WaitForExit(5000);
                bool hostExited = host.WaitForExit(5000);
                if (hostExited) host.WaitForExit(); // Drain redirected callback events.
                bool passed = ready && closed && !error && ownerExited && hostExited && host.ExitCode == 0;
                int? hostExitCode = hostExited ? (int?)host.ExitCode : null;
                // Cleanup is only for test-owned processes; a timeout is a failure.
                if (!hostExited) { host.Kill(); host.WaitForExit(1000); }
                if (!ownerExited) { owner.Kill(); owner.WaitForExit(1000); }
                allPassed &= passed;
                lock (logs) File.WriteAllLines(Path.Combine(output, mode + ".log"), logs);
                records.Add(new { mode = mode, ready = ready, closed = closed, ownerExited = ownerExited,
                    hostExited = hostExited, hostExitCode = hostExitCode, error = error, passed = passed });
            }
        }
        server.Close();
        string report = new JavaScriptSerializer().Serialize(new { passed = allPassed, scenarios = records, modelCalls = 0 });
        File.WriteAllText(Path.Combine(output, "owner-exit-qa.json"), report);
        Console.WriteLine(report);
        return allPassed ? 0 : 1;
    }

    private static ProcessStartInfo StartInfo(string path)
    {
        return new ProcessStartInfo(path) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            WorkingDirectory = Path.GetDirectoryName(path) };
    }
}
