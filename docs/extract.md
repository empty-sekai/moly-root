# 清单提取

[English](extract.en.md)

包清单（AssetBundleInfoNew）由用户自行提供，本仓不内置、不分发任何游戏数据。

`moly extract` 是面向使用者的批量入口：

```sh
moly --unity-version 2022.3.62f3 extract --bundles path/to/decrypted
```

省略 `--out` 时输出写入 `local-data/`——这是工具的默认输出目录，仓库的 `.gitignore` 已忽略它，提取产物不会进入版本控制。

`--manifest` 可选：省略时选择 `--bundles` 目录中所有可识别的角色资产包成员（角色模型、共享动作库、表情表、表演编排、头顶件与 shader 查找源），报告的 `discovery` 项记录扫描、入选与忽略的计数。提供清单时按清单提取。清单可以是 UTF-8 文本，每行一个逻辑 bundle 名称；也可以是 JSON 数组或包含 `bundles` 数组的对象。斜杠分隔的名称会规范化为双下划线形式。重复项会被删除，同时保留首次出现的顺序。

路由器当前识别角色模型 bundle、共享角色动作 bundle、角色设置 bundle、表演编排 bundle、头顶件（emoticon）bundle，以及对话 bundle（需要用户提供 master 表）。每个请求项都会生成包含 `status`、`artifacts`、`counts` 以及错误信息的报告项。缺失 bundle 会标记为 `failed` 并使命令以非零状态退出。未注册提取器的域会保留为 `unsupported`，为未来的资源域扩展保留报告入口。

角色产物按 `sd_<unit>` 命名（例如 `sd_112.glb` 与 `sd_112.rig.json`），与浏览器示例的消费约定一致。有角色产物时会按盘上实有的 `sd_<unit>.glb` 重建 `manifest.json`（浏览器示例直接读取的角色清单），重复或分批运行始终如实反映当前目录内容。默认在 stdout 打印几行摘要与查看提示；`--json` 改为输出完整报告。`extraction-report.json` 始终落盘。角色条目会使用同一清单中存在的动作 bundle 和设置 bundle。第一个角色模型为动作库提供参考骨架。批量输出和 `extraction-report.json` 都写入输出目录。
