# Mori for Krita 0.4.12 · macOS

Mori 原生实时生成、选区助手、独立设置和工作台适配 macOS。Apple 芯片与 Intel 使用同一份通用安装包。

## 安装

适用于 macOS 12 或更新版本。完整磁盘映像包含官方签名的 Krita 5.3.4.1、Mori 0.4.12、AI Diffusion 1.49.0。

1. 保存作品并完全退出 Krita。
2. 打开 `Mori-Krita-0.4.12-macOS-Setup.dmg`，运行「安装 Mori.app」。
3. 点击安装，完成后打开 Krita。新建或打开画布后即可看到 Mori。

安装器无需 Python、Xcode 或管理员权限。Krita 位于当前用户的 `Applications/MoriKrita`，插件安装到 Krita 的资源目录；现有 Mori 与 AI Diffusion 及配置先备份，保留其他插件、画笔和作品。
已有 Krita 的用户可导入 `Mori-Krita-0.4.12-macOS-Plugin.zip`，在 Python 插件管理器启用 Mori 并重开。

Mori 安装器和桥接组件使用本地签名，尚未取得 Apple Developer ID 签名与公证。首次打开如提示开发者无法验证，确认下载来源后，可在「系统设置 → 隐私与安全性」找到安装器并点击「仍要打开」；macOS 12 对应「系统偏好设置 → 安全性与隐私 → 通用」。操作见 [Apple 官方说明](https://support.apple.com/zh-cn/102445)。
Vision Tools 2.2.0 官方未提供 Mac 包，未随此包安装。AI Diffusion 的生成后端及模型仍需在其设置中配置。

## Mac 适配

- 使用系统 WebKit，在 Krita 的原生 Qt 面板内显示工作台，无需 WebView2、Qt WebEngine 或额外 Python 模块。
- 登录会话存入 Mac 钥匙串；读取失败时正常重新登录，不把密码写入文件。
- 分离 Mac 插件更新渠道，官网不可用时读取独立的 GitHub Mac 发布清单，拒绝向 Mac 提供 Windows 更新包。
- 参考图默认按原始尺寸无损上传，实时、工作台、选区共用偏好；结果继续等比放入原画布位置。
- Mac 主机生命周期包含显示隐藏、调整大小、焦点及退出释放。

## 构建与验证

`.github/workflows/macos.yml` 在 Apple 芯片与 Intel 的 macOS runner 上编译并测试。官方 Krita DMG 与 AI Diffusion ZIP 均校验固定 SHA256；打包不修改 Krita 的官方应用签名。

```sh
python3 scripts/build-macos-native.py
python3 tests/mac_native_smoke.py
python3 scripts/run-macos-qa.py --krita build/krita.app --suite run_mac_qa --output build/native-qa
python3 scripts/build-macos-bundle.py --krita-dmg build/krita.dmg --output build/releases
python3 tests/mac_installer_qa.py --stage build/mac-package-stage --output build/installer-qa
```

Windows 保留原有 WebView2 和 DPAPI 分支。此前 0.4.11 Windows 安装包保持不变；此分支只发布 Mac 安装包。
云端自动测试不能替代学员电脑上的数位板、Retina 显示缩放与实际创作验收。
