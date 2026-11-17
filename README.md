# Mori for Krita 0.4.7

Mori 原生实时画布、Mori 工作台与已安装插件的小更新包。

## 本次更新

- **开始**：开启连续实时交互，停笔后按当前参数生成；运行时可暂停后续生成。
- **提交**：关闭连续模式，只生成一张，完成后停止。同样的参数可再次点击提交。
- 生成期间禁用提交，避免重复发送。
- 状态仅占一行：未开始、生成中、已完成、已暂停、出错。详细说明放在悬停提示中。
- 保留现有页面切换和停靠修复，避免工作台网页覆盖其他插件页面。

## 下载与安装

### 首次安装：完整安装包

从 [0.4.7 发布页](https://github.com/a1097473800-source/mori-krita-plugin/releases/tag/v0.4.7) 下载 **Mori-Krita-0.4.7-Windows-x64-Setup.exe**（约 597 MB），保存作品并退出 Krita，双击后点击“安装”。完成后从桌面 **Krita** 打开。

完整包包含 Krita 5.3.4.1、Mori 0.4.7、AI Diffusion 1.47.0、Vision Tools 2.2.0 和微软 WebView2 x64 离线运行组件，三个插件会自动启用。安装软件可离线完成；Mori 生成服务需要联网登录，AI Diffusion 的本地模型及 ComfyUI 服务需另行配置。

### 已安装用户：小更新包

从 [0.4.7 发布页](https://github.com/a1097473800-source/mori-krita-plugin/releases/tag/v0.4.7) 下载 `Mori-Krita-0.4.7-Compatibility-Update.zip`。

1. 保存作品并完全退出 Krita。
2. **完整解压 ZIP** 到本机文件夹。
3. 双击文件夹中的 **更新已安装插件.cmd**。
4. 重新打开 Krita，将鼠标移到面板的 MORI 标题上，确认版本为 **0.4.7**。

这个小包适用于已安装 Mori **0.3.0–0.4.7** 的电脑。更新前会备份原插件；账号、画布和已安装的 WebView2 运行时保留。安装异常时可运行 **检查安装.cmd** 查看实际插件路径及磁盘版本。

### 后续更新

完整安装后仍支持更新。工作台网页在模式菜单中选择 **检查工作台更新**，准备好后重连或下次打开工作台使用。Mori 原生插件的新功能通过发布页的小更新包升级，退出 Krita 后双击更新即可，无需重装整个 Krita。“检查工作台更新”只更新网页，不会在运行中替换 Python 插件。

使用自定义 Krita 资源目录时，在解压目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\update-installed.ps1 -ResourceDirectory "你的 Krita 资源目录"
```

暂停不会取消服务器已经收到的任务；这次任务会继续完成。出错后暂停连续生成，不自动补发结果不确定的收费请求。

## 源码与构建

`mori_krita/` 包含原生插件、随包网页资源及 Windows WebView2 宿主。`webview-host/` 包含宿主 C# 源码和构建脚本，`tests/` 包含原生功能及更新器回归检查。随包网页是已有构建资源，网页工程由 Mori 网站项目维护；本仓库不包含网站服务端、账号数据或供应商密钥。

生成小更新包（Windows PowerShell）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\package-compat-update.ps1
```

输出位于 `releases/`。原生插件版本为 0.4.7；本包沿用兼容的 WebView2 宿主 0.4.6，宿主文件由独立清单校验。

## 验证

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-native-qa.ps1 -KritaRunner "你的 kritarunner.com 路径"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\test_updater.ps1
```

原生验证使用 Krita 自带 Python/Qt 和模拟计费接口；不会调用真实付费模型。更新器测试只操作隔离资源目录。相关资源声明见 [THIRD-PARTY.md](THIRD-PARTY.md) 和 `licenses/`，本仓库不声明额外的开源许可。

完整安装器维护源码位于 `bundle/`；`scripts/build-complete-bundle.py` 使用干净的官方 Krita、校验过的插件发行 ZIP 与微软离线组件构建单文件 EXE。运行 `tests/test_full_setup.ps1 -Setup "安装程序路径" -OutputDirectory "新的隔离测试目录"` 可测试实际 EXE 的解压、安装、插件启用及文件校验，不修改用户的真实配置。
