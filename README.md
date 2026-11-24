# Mori for Krita 0.4.16

修复「购买兑换码」点击无反应，使用系统默认浏览器打开当前配置的购买页面。

## 本次更新

- 修复个人中心「购买兑换码」外链被内嵌浏览器拦截的问题。仅打开用户主动点击且与当前购买按钮一致的 HTTPS 地址。
- 配套 WebView2 宿主更新到 0.4.16，同时保留 Pinterest 和 API 服务外链支持。

## 已有功能

- 原生登录窗口和工作台均提供「通过浏览器登录（支持 Google）」。在浏览器确认后，插件自动使用原账号登录，保留积分和画布。
- 浏览器授权完成页保留原按钮样式，显示「授权成功」和「请返回 Krita 继续创作」，按钮不跳转网站。配套云端登录服务已经上线。
- 网页更新独立校验浏览器登录能力，旧缓存不能覆盖新版登录入口。
- 工作台加载时播放 Mori SVG 路径动画，不显示加载说明文字；页面准备完成后才显示浏览器。
- 修复 Windows 进程管道读取停滞导致工作台空白的问题，以及切换、隐藏面板时外部窗口闪烁的问题。
- 设置新增「连接与恢复」：刷新工作台页面、重新连接工作台、刷新画面、刷新账号与积分。刷新不会提交生成任务。
- 统一使用连写 Mori 矢量标志。设置使用黑白灰配色，主要按钮为紫色；选中描边为灰色，版本号位于左下角。
- 保留原图无损默认、实时模式、选区助手、云端画布同步和版本冲突保护。

## 安装

首次安装：下载 `Mori-Krita-0.4.15-Windows-x64-Setup.exe`，保存作品并退出 Krita，然后运行安装程序。
完整包包含 Krita 5.3.4.1、Mori 0.4.15、AI Diffusion 1.49.0、Vision Tools 2.2.0 和 WebView2 离线运行组件。
首次安装后，再使用本次 0.4.16 小更新包升级 Mori 插件。

已安装 Mori 0.3.0–0.4.16 的 Windows 用户：

1. 保存作品，完全退出 Krita。
2. 完整解压 `Mori-Krita-0.4.16-Compatibility-Update.zip`。
3. 双击「更新已安装插件.cmd」。
4. 重开 Krita，在「设置 → 更新与版本」核对版本。

更新器先备份原插件，保留账号、画布和其他插件。工作台网页已打开时，新下载的网页在重开 Krita 后生效。

## 维护

插件发布清单：`https://morinet.chat/krita-plugin/manifest.json`。
工作台网页清单：`https://morinet.chat/krita-ui/manifest.json`。
先发布 GitHub Releases 中的安装文件并校验，再更新官网插件清单。

工作台使用 `bridgeMinVersion: 0.4.12`、`canvasSyncVersion: 1` 和 `browserLoginVersion: 1`。
网站工程由独立私有仓库维护，随包网页的源码版本和校验值见 `web-ui/build-reference.json`。
插件使用已验证的 WebView2 宿主 0.4.16；宿主和插件版本独立校验。

运行验证：

- `python -m unittest discover -s tests -p test_browser_login.py`
- `python -m unittest discover -s tests -p test_plugin_updates.py`
- `python -m unittest discover -s tests -p test_web_updates.py`
- `node --test tests/cloud-sync.test.mjs`
- `scripts/run-native-qa.ps1 -KritaRunner <kritarunner.com> -Suite run_loading_qa`
- `tests/test_updater.ps1`
- `tests/test_full_setup.ps1 -Setup <Setup.exe> -OutputDirectory <新的测试目录>`

第三方资源说明见 `THIRD-PARTY.md` 和 `licenses/`。
