# Windows x64 完整安装器

包含 Krita 5.3.4.1、当前 Mori 原生插件、AI Diffusion 1.47.0、Vision Tools 2.2.0 与微软 WebView2 离线安装组件。只使用干净的发行文件，禁止从正在使用的 Krita 目录收集配置、账号、画布或缓存。

先下载并验证这些官方发行文件：

| 文件 | SHA256 |
| --- | --- |
| [krita-x64-5.3.4.1-setup.exe](https://download.kde.org/stable/krita/5.3.4.1/krita-x64-5.3.4.1-setup.exe) | `025523cf1751ff209faf7ecfe1cff851f2cdab64acb8c555b307b349ad56ce8f` |
| [krita_ai_diffusion-1.47.0.zip](https://github.com/Acly/krita-ai-diffusion/releases/tag/v1.47.0) | `7442d7dcad147e8dbdefcc36728113c9712574e6f22036b02460e218ff859c7e` |
| [krita_vision_tools-windows-x64-2.2.0.zip](https://github.com/Acly/krita-vision-tools/releases/tag/v2.2.0) | `326f736cb45c6503ed5e0d6ebd117be730c284ad5ad5bbb8dbbed1bfc32e6837` |

WebView2 离线组件的来源和摘要见 `prerequisites/manifest.json`，文件须放到该清单对应的位置。构建前确认 Krita 与微软安装文件的 Authenticode 签名有效。用 7-Zip 将官方 Krita 安装文件展开到新的 `stage/Krita/`，保留 `bin/`、`lib/`、`python/`、`share/`、`shellex/`。

在仓库根目录运行（示例路径替换为实际目录）：

```powershell
python scripts/build-complete-bundle.py --stage "构建暂存目录" --output "交付目录" --ai "AI Diffusion ZIP 路径" --vision "Vision Tools ZIP 路径" --build-date 20261117
```

构建器会检查两个上游插件的固定摘要、生成逐文件清单、压缩负载并使用 .NET Framework x64 编译器构建 EXE。安装程序先校验整个负载及各个文件，再安装 Krita、插件和快捷方式；插件版本沿用 `mori_krita/version.py`。

实际 EXE 的隔离安装验证：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/test_full_setup.ps1 -Setup "完整 EXE 路径" -OutputDirectory "新的隔离测试目录"
```

这个测试调用安装器的 `--self-test` 入口，将软件、插件、配置和快捷方式全部写入隔离目录。它校验旧配置保留、三个插件启用、旧缓存清理、官方 Krita 签名及所有安装后的文件，不操作真实用户配置或安装微软运行时。

完整安装与小更新共用 Krita 资源目录。后续原生功能继续使用 `Compatibility-Update.zip` 升级；工作台网页通过插件模式菜单中的“检查工作台更新”独立更新。
