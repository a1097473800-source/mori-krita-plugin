# Mori for Krita 0.4.10

右上角齿轮统一打开独立的 Mori 设置窗口，以左侧分类集中管理常用偏好与软件更新。

## 本次更新

- 深灰与淡紫配色的独立设置窗口，支持拖动、调整大小和滚动。
- 「画布与图层」：快速参考或原图无损、默认发送范围、工作台结果自动加入图层。
- 「实时与选区」：当前画面来源、模型可用质量、停笔后更新间隔、实时结果自动放入、选区助手。
- 「更新与版本」：手动检查、插件版本、更新说明、下载、网页状态和自动检查开关。
- 模式菜单只切换实时或工作台；删除设置中的服务器地址、账号登录及重复更新入口。
- 常用偏好自动保存，并支持恢复当前页默认；当前文档的图层来源随文档刷新。
- 后台检查与新版本提示保留，齿轮的小圆点持续提醒。检查过程中仍可打开其他设置。
- 登录继续由生成流程在需要时提供；云端画布同步和兼容性校验沿用原行为。

## 安装

这是已安装 Mori 0.3.0–0.4.10 的 Windows 小更新包。

1. 保存作品，完全退出 Krita。
2. 完整解压 Compatibility-Update.zip。
3. 双击「更新已安装插件.cmd」。
4. 重开 Krita，点击 Mori 面板右上角齿轮「设置」。

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
