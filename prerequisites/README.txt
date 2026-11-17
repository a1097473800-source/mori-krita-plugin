WebView2 x64 完整离线安装文件

本目录包含从微软官方下载的原始安装文件，未修改或重新签名。
文件：MicrosoftEdgeWebView2RuntimeInstallerX64.exe
官方固定入口：https://go.microsoft.com/fwlink/?LinkId=2124701
来源与SHA256：manifest.json

安装器会先检查目标电脑是否已有WebView2 Runtime；缺少时才调用本文件。
安装前核对文件SHA256及Microsoft Corporation有效签名。
这是一份完整离线安装文件，不是需要再联网下载浏览器的Bootstrapper。

微软官方分发流程明确支持把Standalone Installer随应用一起分发：
https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution

WebView2 Runtime由微软按其安装文件内的许可条款提供。
微软官方许可文本同时保存在Microsoft-WebView2-Runtime-LICENSE.html。
文本来源：https://developer.microsoft.com/microsoft-edge/api/eula/webview2?locale=en-us
浏览器组件可离线安装；Mori账号和生成服务仍需要联网。
