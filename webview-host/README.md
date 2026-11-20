# Mori WebView2 host

Windows x64 helper for showing the existing Mori HTML canvas inside a Krita dock.
The helper uses Microsoft Edge WebView2 Runtime already installed on the machine;
it does not load a Chromium/Qt build into Krita's process.

Build with `powershell -NoProfile -File .\webview-host\build.ps1` from the plugin
project. The script uses the Windows .NET Framework compiler and the official
`Microsoft.Web.WebView2` NuGet package, pinned to `1.0.4314.35`.
Build dependencies stay under `build/deps`; only the EXE, SDK runtime DLLs and
license are copied into `mori_krita/webview-host`, with a version and SHA-256
manifest (`host-release.json`) verified by the compatibility updater.

The helper receives one UTF-8 JSON line on stdin:

```
{"url":"http://127.0.0.1:PORT/SESSION/","parent":12345,"profile":"TEMP_PATH"}
```

It emits JSON status lines (`ready`, `error`, `closed`) on stdout. Subsequent
stdin commands are `{"op":"focus"}` and `{"op":"close"}`; EOF also closes it.
No account credentials, URLs or pairing values are passed on the command line.
Application navigation, scripts and API requests remain restricted to the supplied
loopback origin. The public inspiration library can load HTTPS images from
`i.pinimg.com` only in the image resource context; scripts, API calls, credentials,
alternate hosts and custom ports are not enabled for that domain. A user-initiated
canonical Pinterest `/pin/ID/` source link opens in the default browser while the
embedded canvas stays on the loopback origin.

PNG/JPEG/WebP, SVG, PSD, ZIP and project JSON exports use a native save dialog.
The pinned SDK exposes no download `IsUserInitiated` flag, so a top-document
listener reports trusted pointer or keyboard interactions on buttons and links.
The host validates that gesture message's source and per-host nonce, then grants
one export opportunity for 120 seconds. The opportunity is consumed before the
dialog appears, including if the user cancels. Only blobs belonging to the
current loopback origin and its `/media/` images can be saved, and no file is
written to a destination without the save dialog's approval. The host never
opens a downloaded file and caps exports at 256 MiB. Web messages accept no file
paths, URLs or account data. Permissions and password saving remain disabled.
Its temporary browser profile is removed by the Python wrapper after shutdown.

Teardown starts on form closing, direct disposal, or native handle destruction.
It marks shutdown before detaching CoreWebView2 events and disposing children.
All asynchronous continuations and callbacks check the active browser lifetime;
native parent loss exits the helper message loop even without FormClosed.
`test-lifecycle.ps1` exercises eight shutdown timings with a real WebView2 and
a loopback fixture. `SmokeTest.cs` checks that the external host exits cleanly
after losing its native parent; a timeout or nonzero exit fails the test.

Offline policy checks can be run with `powershell -NoProfile -File
.\webview-host\test-policy.ps1`. They compile the real host source against the
same pinned SDK and test URL, resource-context and export-filename rules without
opening dialogs, making external requests or accessing an account. The APIs used
for deferrals, gesture flags and request contexts were checked against the SDK's
bundled `Microsoft.Web.WebView2.Core.xml` documentation.

References:

- [Microsoft WebView2 WinForms setup](https://learn.microsoft.com/en-us/microsoft-edge/webview2/get-started/winforms)
- [WebView2 threading model](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/threading-model)
- [NuGet package](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4314.35)


## Hidden startup and page presentation (0.4.13)

The native window starts as a hidden child with the Qt parent configured from creation. WinForms may park unmanaged children during handle creation; bounds synchronization restores the foreign parent while hidden. The requested visibility and usable-page readiness must both be true before presentation. Native resize calls only run when the dimensions change.

Qt displays a native loading screen using the approved SVG cubic outlines. A path stroke progressively draws each contour, then the filled wordmark appears; no loading caption is shown. Hidden/loading-failure states stop the animation timer.

Navigation emits `loading`. After successful navigation, a same-origin document script waits for a populated root, the end of account/canvas loading placeholders and loaded fonts. A per-navigation nonce binds its ready message to the current document. This works for bundled and compatible cached pages; readiness does not depend on a fixed sleep or a new website deployment. `visible:false` in initial configuration keeps the process hidden until the Qt wrapper handles `ready` and requests visibility.

`test-presentation.ps1` uses a delayed local React-like root to test hidden startup, native parent identity, page readiness and hide/show cycles. `test-lifecycle.ps1` continues to check initialization, navigation, native parent loss and shutdown. No model calls or real accounts are used.


## Pipe commands and recovery (0.4.14)

Qt QProcess supplies an overlapped standard-input pipe. Pending synchronous ConsoleStream reads stalled short visibility commands in the combined Qt/WebView2 runtime. PipeCommandReader checks available bytes before reading, bounds each UTF-8 JSON line, and keeps configuration, visibility, reload, focus and close on the same private pipe. The native loading surface remains caption-free.

`run_webview_integration_qa` launches the real bundled executable from QProcess in an isolated Qt dock with a loopback fixture. It verifies the actual native child becomes visible after readiness, mode hiding/return, reload, process restart and graceful shutdown. This covers the process boundary omitted by the in-process host presentation tests.
