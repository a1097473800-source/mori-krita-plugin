# 第三方资源

- Mori 前端：初版来自本机 `local-canvas-pricing`；0.3.0同步 `local-canvas` 的 `ef3ad57`，0.3.7同步 `27d92331301f469ff0fbd1b7c1c7db022af3cad7`。后续兼容网页由同一网站源码经Krita桥接补丁构建，不从其他网站拉取界面。仅用于本次用户授权的内部Krita适配，不声明其开源或对外再分发授权。
- React / React DOM、React Flow / XYFlow、Zustand、Lucide 和原项目字体：沿用原项目版本与声明，相关许可保留在 `licenses/`；网页包同时保留 `web/fonts/NOTICE.txt` 等字体声明。网页更新校验不应省略这些许可文件。
- Microsoft.Web.WebView2 SDK：1.0.4314.35，官方 NuGet 来源与 SHA256 记录于 `webview-host/build.ps1`；SDK 许可附于 `mori_krita/webview-host/WebView2-LICENSE.txt`。
- Krita、PyQt5、Qt及.NET Framework由目标电脑提供。0.3.1完整包附带微软原始签名的WebView2 x64离线安装文件，来源、SHA256及分发说明见 `prerequisites/`；不修改微软安装文件。
- macOS 0.4.12 完整包附带 KDE 官方原始签名 Krita 5.3.4.1 磁盘映像与 Acly 官方 AI Diffusion 1.49.0 插件；固定来源和 SHA256 记录于 `scripts/build-macos-bundle.py`。Krita 的许可及组件声明保留在其原始应用内，AI Diffusion 的 GPL 许可与网页依赖许可同时安装到当前用户 `Applications/MoriKrita/Notices-0.4.12`。Mac 使用 Apple 系统 WebKit 与 Security 框架，不包含 Windows WebView2 组件。

本目录构建产物面向内部试用。对外分发前应由项目所有者确认原项目、依赖与相关运行组件的许可条件。
