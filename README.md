# moly-root

[English](README.en.md)

`moly-root` 是面向《世界计划 缤纷舞台！feat. 初音未来》（Project SEKAI，简称 PJSK）资产的提取工具链，不依赖 Unity 引擎。

**当前范围：仅角色资产包**——输出包含 glTF 2.0 角色文件、共享人形动作库、表情表、头顶件数据和机器可读提取报告。公开动作输出使用人形骨骼名称绑定动画，因此可以重定向到兼容的人形骨骼。浏览器参考消费者见 `examples/viewer/`，呈现语义见 `docs/presentation.md`。

**路线图**：天气（现象）、站点场景、家具与装饰等资产包在后续版本逐步支持。浏览器示例当前还原单人（独处）表演场景；与家具互动的表演属于家具资产包域，暂未支持。

包清单（AssetBundleInfoNew）由用户自行提供，本仓不内置、不分发任何游戏数据。

## 安装

```sh
python -m pip install .
```

## 快速开始

需要 Python ≥ 3.11 与一个现代浏览器。游戏数据（`AssetBundleInfoNew` 包清单、资源端点或已解密的 bundle）由你自行提供，本仓不内置、不分发。

1. 在仓库根目录安装（安装后提供命令 `moly`）：`python -m pip install .`
2. 拉取并提取。`pull` 从用户提供的包清单中选择角色资产包所需的全部 bundle（角色模型、共享动作库、表情表、表演编排、头顶件与材质查找源），递归解析依赖，随后下载、解密并提取：

   ```sh
   moly --unity-version 2022.3.62f3 pull --manifest path/to/AssetBundleInfoNew.json --asset-base-url <你的资源端点>
   ```

   提取产物写入 `local-data/`（默认，已被仓库忽略），并生成浏览器示例直接读取的 `manifest.json`；下载与解密的中间产物在 `moly-pull-output/`。

   若已持有解密后的 bundle 目录，可以不经网络直接提取；无需提供清单，工具会识别目录中属于角色资产包的 bundle：

   ```sh
   moly --unity-version 2022.3.62f3 extract --bundles path/to/decrypted
   ```

3. 查看：在仓库根目录启动一个静态 HTTP 服务器，打开示例页查看角色、动作与头顶件：

   ```sh
   python -m http.server 8000
   # 浏览器打开 http://localhost:8000/examples/viewer/index.html?base=../../local-data
   ```

## 命令

所有资源路径和清单文件都由使用者提供。命令的参数帮助可用以下方式查看：

```sh
moly --help
moly extract --help
moly pull --help
moly characters --help
moly motion-library --help
moly facial-tables --help
moly fetch-apk --help
```

从已解密 bundle 目录批量提取（`--manifest` 可选，省略时选择目录中所有可识别的角色资产包成员）：

```sh
moly --unity-version 2022.3.62f3 extract --bundles path/to/decrypted
```

省略 `--out` 时产物写入 `local-data/`（默认输出目录，已被 `.gitignore` 忽略）。提供 `--manifest` 可限定子集：每行一个逻辑 bundle 名称，斜杠名称和规范的双下划线名称等价，也接受 JSON 数组以及 `{ "bundles": [...] }` 文档。缺失 bundle 会使命令以非零状态退出；未注册提取器的域会保留为 `unsupported`。

提取单个角色、共享动作库或表情表：

```sh
moly --unity-version 2022.3.62f3 characters --bundle path/to/character --out-dir out --name character
moly --unity-version 2022.3.62f3 motion-library --reference-bundle path/to/character --motion-bundle path/to/motion --out-dir out
moly --unity-version 2022.3.62f3 facial-tables --bundle path/to/settings --out tables.json
```

从 `AssetBundleInfoNew` 清单拉取：

```sh
moly --unity-version 2022.3.62f3 pull --manifest path/to/AssetBundleInfoNew.json --asset-base-url https://assets.example.invalid/base
```

`AssetBundleInfoNew` 清单是唯一的 bundle 元数据来源。清单必须由用户提供，并应包含每个入口 bundle 的 `bundleName`、`downloadPath`、`cacheFileName` 和 `dependencies`；不要从本地目录名或网络 URL 猜测依赖关系。`pull` 从清单选择角色模型、共享动作、角色设置、表演编排、头顶件与材质查找源等根 bundle，递归解析依赖并检测循环。提取产物默认写入 `local-data/`（`--extract-out` 可改）；下载与解密工作区默认在 `moly-pull-output/`（`--out` 可改）。

`--asset-base-url` 指定资源网络端点。它是不透明前缀，工具不解析其内部结构；每个请求 URL 按 `<asset-base-url>/<downloadPath>/<bundleName>` 组成，结尾保留清单中的 `bundleName`（包括其中的斜杠）。端点和包清单均由用户自行提供，本仓不内置、不分发。下载支持重试和 `.part` 续传，并校验清单中可选的 SHA-256 摘要。wrapper 会先解密，然后调用提取流程。

发现、下载或检查 Android APK：

```sh
moly fetch-apk latest
moly fetch-apk download package.apk --url https://example.invalid/package.apk --sha256 sha256:HEX
moly fetch-apk inspect package.apk
```

`fetch-apk` 可发现公开 Android 包、续传 `.part` 下载、校验可选 SHA-256，或列出 APK 中的资源容器候选；它不会自动提取游戏数据。

## 输出

角色提取写入 `<name>.glb` 和 `<name>.rig.json`。动作提取写入 glTF 动作库和 `<name>.index.json`。表情提取写入表格 JSON。表演编排提取写入 `alone-actions.json`（动作与表情的配对与时序）。对话语料提取写入 `talks.json`（单角色直接对话，需 `--master`）。头顶件提取写入 `emoticons/`（一份共享 `emoticons.json` 加每张贴图一个 PNG）。给了 `--master` 时另写 `characters.json`（角色在册与移动人格；master 表由使用者自备，本仓不内置）。清单提取始终写入 `extraction-report.json`，并在有角色产物时按盘上实有的 `sd_<编号>.glb` 重建 `manifest.json`（浏览器示例直接读取的角色清单）。`pull` 的提取产物与 `extract` 相同地落在 `local-data/`；其工作区另外生成 `raw/`、`decrypted/`、`downloads.json` 和 `extraction-manifest.txt`。

参阅 [数据契约](docs/data-contract.md)、[清单提取](docs/extract.md)、[拉取流程](docs/pull.md) 和 [动作重定向](docs/retarget.md)。

## 许可证

MIT。第三方归属见 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
