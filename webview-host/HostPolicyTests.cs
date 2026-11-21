// Offline policy tests compiled against the same pinned SDK as the real host.
using System;

internal static class HostPolicyTests
{
    private static int assertions;
    private static void Check(bool value, string name)
    {
        assertions++;
        if (!value) throw new Exception("Policy assertion failed: " + name);
    }

    private static int Main()
    {
        const string origin = "http://127.0.0.1:45678";
        const string image = "https://i.pinimg.com/originals/ab/cd/ef/a123.png";
        const string pin = "https://www.pinterest.com/pin/123456789/";
        const string blob = "blob:http://127.0.0.1:45678/12345678-1234-1234-1234-123456789abc";
        try
        {
            var closedNative = new System.Runtime.InteropServices.COMException("closed controller", unchecked((int)0x8007139F));
            var closedSdk = new InvalidOperationException("SDK dispose", closedNative);
            Check(HostShutdown.ClosedController(closedSdk, true, false), "closed native controller during terminal disposal");
            Check(HostShutdown.ClosedController(closedNative, true, false), "native disposed state during terminal disposal");
            Check(!HostShutdown.ClosedController(closedSdk, false, false), "do not suppress errors before shutdown");
            Check(!HostShutdown.ClosedController(closedSdk, true, true), "do not suppress errors with a live parent");
            Check(!HostShutdown.ClosedController(new InvalidOperationException("other failure"), true, false), "do not suppress unrelated managed errors");
            Check(!HostShutdown.ClosedController(new System.Runtime.InteropServices.COMException("other native error", unchecked((int)0x80004005)), true, false), "do not suppress unrelated native errors");
            Check(BrowserPolicy.SameOrigin(origin + "/media/a.png", origin), "local media");
            foreach (string url in new[] { "https://127.0.0.1:45678/", "http://127.0.0.1:45679/", "http://127.0.0.1.evil.invalid:45678/", "http://secret@127.0.0.1:45678/", "javascript:alert(1)", "file:///C:/secret", "data:image/png;base64,AA" })
                Check(!BrowserPolicy.SameOrigin(url, origin), "foreign origin " + url);

            Check(BrowserPolicy.CanLoadInspirationImage(image, true), "catalog image");
            foreach (string extension in new[] { "jpg", "jpeg", "png", "webp" })
                Check(BrowserPolicy.CanLoadInspirationImage(image.Replace(".png", "." + extension), true), "catalog format " + extension);
            Check(!BrowserPolicy.CanLoadInspirationImage(image, false), "image URL cannot be script/fetch");
            foreach (string url in new[] { "http://i.pinimg.com/originals/a.png", "https://i.pinimg.com.evil.invalid/originals/a.png", "https://evil.i.pinimg.com/originals/a.png", "https://user@i.pinimg.com/originals/a.png", "https://i.pinimg.com:444/originals/a.png", image + "?cookie=1", image + "#fragment", image.Replace(".png", ".js"), "https://i.pinimg.com/a.svg", "https://127.0.0.1/originals/a.png" })
                Check(!BrowserPolicy.CanLoadInspirationImage(url, true), "blocked remote image " + url);

            Check(BrowserPolicy.CanOpenExternal(pin, true), "trusted Pinterest original");
            Check(!BrowserPolicy.CanOpenExternal(pin, false), "scripted popup blocked");
            const string apiService = "https://sub2api.izhe.top/register?aff=YD38MU9RTR3K";
            Check(BrowserPolicy.CanOpenExternal(apiService, true), "user-selected API registration link");
            Check(!BrowserPolicy.CanOpenExternal(apiService, false), "scripted API popup blocked");
            foreach (string url in new[] { apiService.Replace("https:", "http:"), apiService.Replace("sub2api.izhe.top", "sub2api.izhe.top.evil.invalid"), apiService.Replace("sub2api.izhe.top", "user:pass@sub2api.izhe.top"), apiService.Replace("sub2api.izhe.top", "sub2api.izhe.top:444"), apiService + "&token=secret", apiService + "#extra", "https://sub2api.izhe.top/", "https://sub2api.izhe.top/register", "https://sub2api.izhe.top/register?aff=OTHER" })
                Check(!BrowserPolicy.CanOpenExternal(url, true), "blocked noncanonical API link " + url);
            foreach (string url in new[] { "http://www.pinterest.com/pin/123/", "https://www.pinterest.com.evil.invalid/pin/123/", "https://www.pinterest.com:444/pin/123/", "https://user:pass@www.pinterest.com/pin/123/", "https://www.pinterest.com/", pin + "?token=secret", pin + "#extra", "file:///C:/Windows/notepad.exe", "javascript:alert(1)", "https://127.0.0.1/pin/123/" })
                Check(!BrowserPolicy.CanOpenExternal(url, true), "blocked external URL " + url);

            Check(BrowserPolicy.LocalBlob(blob, origin), "own blob");
            Check(BrowserPolicy.CanDownload(blob, origin), "local exported blob");
            Check(BrowserPolicy.CanDownload(origin + "/media/a123-1234.png", origin), "local original download");
            foreach (string url in new[] { blob.Replace("45678", "45679"), blob + "?secret", "blob:" + origin + "/not-a-uuid", image, pin, origin + "/api/state", origin + "/media/file.exe", "data:application/octet-stream;base64,AA", "file:///C:/secret" })
                Check(!BrowserPolicy.CanDownload(url, origin), "blocked download URL " + url);

            foreach (string name in new[] { "红色产品.png", "产品.psd", "图层.zip", "画布.mori.json", "彩色描摹.svg", "photo.jpeg", "photo.webp" })
                Check(BrowserPolicy.DownloadName(name) == name, "valid export filename " + name);
            Check(BrowserPolicy.DownloadName("C:\\temp\\产品.png") == "产品.png", "discard suggested directory");
            Check(BrowserPolicy.DownloadName("CON.png") == "_CON.png", "reserved device filename");
            Check(BrowserPolicy.DownloadName("LPT1.psd") == "_LPT1.psd", "reserved port filename");
            foreach (string name in new[] { "program.exe", "picture.png.exe", "script.ps1", "page.html", "shortcut.lnk", "unknown", "", "." })
                Check(BrowserPolicy.DownloadName(name) == null, "blocked export filename " + name);
            Check(BrowserPolicy.MaxDownloadBytes == 256L * 1024L * 1024L, "bounded export size");
            Console.WriteLine("{\"passed\":true,\"assertions\":" + assertions.ToString() + ",\"external_requests\":0,\"dialogs_opened\":0}");
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            return 1;
        }
    }
}
