# Mori for Krita 0.4.11

移除标题栏多余的浮动／停靠按钮，保留齿轮设置与关闭按钮；参考图默认采用原图无损上传。

## 本次更新

- 「原图无损」设为默认及推荐选项，使用原始尺寸的无损 PNG，保留线稿、文字和纹理细节。
- 修复实时模式固定使用快速参考的问题，现在工作台、实时、选区助手均遵循参考图设置。
- 「快速参考」仍可手动选择，说明中明确提示缩小和有损压缩可能减少细节。
- 设置中区分参考图上传质量与生成输出分辨率；已有用户的主动选择保持不变。
- 结果图仍下载完整图片并等比放入原位置，保留原生设置、版本检查与云端同步功能。

## 安装

这是已安装 Mori 0.3.0–0.4.11 的 Windows 小更新包。

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
