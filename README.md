# Mori for Krita 0.4.9

修复插件没有检查更新页面、实时模式收不到提醒，以及 0.4.8 工作台网页更新被兼容保护拦住的问题。

## 本次更新

- Mori 面板顶部常驻「更新」入口；实时模式和工作台使用同一个原生更新窗口。
- 更新窗口重新设计，显示当前与最新版本、更新说明、工作台网页状态和自动检查开关。
- 插件启动后在后台检查，有新版本时入口显示「有更新」；支持手动重新检查。
- 优先从 Mori 官网读取公开插件版本，官网暂不可用时回退 GitHub；网络失败会明确提示，不会误报最新。
- 原生版本和网页同步能力分别检查，网页包和缓存均校验同步能力声明、文件大小与 SHA256。
- 保留已安装 0.4.8 的云端画布、旧本地画布迁移和同步冲突保护。新版网页从当前线上网站版本构建。

## 安装

这是已安装 Mori 0.3.0–0.4.9 的 Windows 小更新包。

1. 保存作品，完全退出 Krita。
2. 完整解压 Compatibility-Update.zip。
3. 双击「更新已安装插件.cmd」。
4. 重开 Krita，点击 Mori 面板顶部「更新」。

旧插件尚无原生更新检查，需要先手动安装这一次。此后可在插件中接收后续已发布版本提醒。
安装器先备份原插件，保留账号、画布和其他插件；运行中的 Python 模块仍需重开 Krita 后生效。
工作台网页已打开时，新下载的网页将在下次重开 Krita 后生效，避免混用资源。

## 维护

公开插件版本清单位于 /krita-plugin/manifest.json；工作台网页清单位于 /krita-ui/manifest.json。
工作台使用 bridgeMinVersion: 0.4.8 和 canvasSyncVersion: 1；未来网页构建必须保留云端同步声明和行为。
原生小更新包发布到本仓库 Releases 后，再发布官网插件清单，确保下载地址可用。
网页打包说明与源码版本见 web-ui/build-reference.json；网站工程由独立私有仓库维护。

运行验证：
- python -m unittest discover -s tests -p test_plugin_updates.py
- python -m unittest discover -s tests -p test_web_updates.py
- node --test tests/cloud-sync.test.mjs
- scripts/run-native-qa.ps1 -KritaRunner <kritarunner.com> -Suite run_update_qa
- tests/test_updater.ps1

本包使用已验证的 WebView2 宿主 0.4.8。支持资源说明见 THIRD-PARTY.md 和 licenses/。
