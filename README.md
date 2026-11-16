# Mori for Krita 0.4.7

Mori 原生实时画布、Mori 工作台与已安装插件的小更新包。

## 本次更新

- **开始**：开启连续实时交互，停笔后按当前参数生成；运行时可暂停后续生成。
- **提交**：关闭连续模式，只生成一张，完成后停止。同样的参数可再次点击提交。
- 生成期间禁用提交，避免重复发送。
- 状态仅占一行：未开始、生成中、已完成、已暂停、出错。详细说明放在悬停提示中。
- 保留现有页面切换和停靠修复，避免工作台网页覆盖其他插件页面。

## 下载与安装

从 [0.4.7 发布页](https://github.com/a1097473800-source/mori-krita-plugin/releases/tag/v0.4.7) 下载 `Mori-Krita-0.4.7-Compatibility-Update.zip`。

1. 保存作品并完全退出 Krita。
2. **完整解压 ZIP** 到本机文件夹。
3. 双击文件夹中的 **更新已安装插件.cmd**。
4. 重新打开 Krita，将鼠标移到面板的 MORI 标题上，确认版本为 **0.4.7**。

这是已安装 Mori **0.3.0–0.4.7** 的更新包，不包含完整 Krita 或首次安装所需的运行环境。更新前会备份原插件；账号、画布和已安装的 WebView2 运行时保留。安装异常时可运行 **检查安装.cmd** 查看实际插件路径及磁盘版本。

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
