# 清单提取

[English](extract.en.md)

包清单（AssetBundleInfoNew）由用户自行提供，本仓不内置、不分发任何游戏数据。

`moly extract` 是面向使用者的批量入口：

```sh
moly --unity-version 2022.3.62f3 extract --bundles path/to/decrypted
```

省略 `--out` 时输出写入 `local-data/`——这是工具的默认输出目录，仓库的 `.gitignore` 已忽略它，提取产物不会进入版本控制。

`--master <目录>` 从调用方提供的 `<表名>.json` 读取 master 表。`--master-url [基址]` 从 `<基址>/<表名>.json` 获取同样的表；省略值时使用公开默认基址。`--master-cache <目录>` 缓存获取的表。这些参数在 `pull` 与 `registry` 中也可用。

提供 master 表时，派生的 `characters.json` 会合并 identity、locomotion、soloAction 与玩家移动配置。player 读取 `clientConfigs.json` 的 `77`、`78`、`95` 行，给出解析后的行和普通走路、冲刺速度。如果整表或其中一行缺失，`characters.json` 仍会写出，`player` 为 `null`，并登记 `summary.missing.playerConfig`；identity、locomotion、soloAction 与其它无关提取产物互不影响。其它 master 表缺失仍会使 registry artifact 失败，并在报告中说明。

路由器当前识别角色模型 bundle、共享角色动作 bundle、角色设置 bundle、表演编排 bundle、头顶件（emoticon）bundle，以及对话 bundle（需要用户提供 master 表）。每个请求项都会生成包含 `status`、`artifacts`、`counts` 以及错误信息的报告项。缺失 bundle 会标记为 `failed` 并使命令以非零状态退出。未注册提取器的域会保留为 `unsupported`，为未来的资源域扩展保留报告入口。

角色产物按 `sd_<unit>` 命名（例如 `sd_112.glb` 与 `sd_112.rig.json`），与浏览器示例的消费约定一致。有角色产物时会按盘上实有的 `sd_<unit>.glb` 重建 `manifest.json`（浏览器示例直接读取的角色清单），重复或分批运行始终如实反映当前目录内容。默认在 stdout 打印几行摘要与查看提示；`--json` 改为输出完整报告。`extraction-report.json` 始终落盘。角色条目会使用同一清单中存在的动作 bundle 和设置 bundle。第一个角色模型为动作库提供参考骨架。批量输出和 `extraction-report.json` 都写入输出目录。
