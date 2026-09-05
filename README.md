# moly-root

[English](README.en.md)

`moly-root` 是面向《世界计划 缤纷舞台！feat. 初音未来》（Project SEKAI，简称 PJSK）资产的提取工具链，不依赖 Unity 引擎。

**当前范围：角色资产包、天气（现象）资产包与家具演出资产包**——角色侧输出 glTF 2.0 角色文件、共享人形动作库、表情表、头顶件数据；天气侧输出每个现象的环境配置、天空渐变、后处理档案、站点覆盖与粒子效果；家具演出侧输出家具接口（角色挂点与格集合）、两族演出 timeline（剧情与家具）、相机曲线、家具旁对话，以及由 timeline 派生的动画片段，另有对话 UI 与头顶 HUD 的窗树字段（输入是调用方提供的 APK player data，不是可下载的包）；三侧共写一份机器可读提取报告。公开动作输出使用人形骨骼名称绑定动画，因此可以重定向到兼容的人形骨骼。浏览器参考消费者是 `examples/viewer/`（角色与天气），呈现语义见 `docs/presentation.md`。

**路线图**：装饰等资产包在后续版本逐步支持。浏览器示例是 `examples/viewer/`，还原单人（独处）表演场景；**提取器输出的是数据，示例是预览，不是成品呈现层**。两条限制照实登记：动画片段的 glTF 可播率不是全部（`cut_scene` 361/371、`fixture_timeline` 859/1401，差额是 1658 条通道路径哈希未解析，已分四类且无兜底；四类说的是「本次搜索过的层级里都没有它」，不是「下发 prefab 里没有它」——跨包搜索底是本工具已导出的模型文件，是下发内容的子集）；对话 UI 的字段手解未全部收口（真数据上 290 个对象解完残余 0 字节、45 个记为 partial）。两条都见[清单提取](docs/extract.md)。天气侧的音乐与环境音**会一并提取**：音频包由使用者的 master 行点名，`pull` 会把它们一起拉下来；解码成波形需要一个可选的外部程序（见[可选外部依赖](#可选外部依赖音频解码)）。

包清单（AssetBundleInfoNew）由用户自行提供，本仓不内置、不分发任何游戏数据。

## 在线预览

<https://chara.mizore.art/> 是 `examples/viewer/` 的一份托管构建：在浏览器里直接查看角色、共享动作库、表情、头顶件与天气现象，不必先在本地跑一遍提取。

该页面运行在一份**已提取的产物**上，产物由部署方自行准备；**本仓库不内置、不分发游戏数据**，预览页与仓库分开托管。

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

   若已持有解密后的 bundle 目录，可以不经网络直接提取；无需提供清单，工具会识别目录中属于已支持资产包的 bundle（角色与现象）：

   ```sh
   moly --unity-version 2022.3.62f3 extract --bundles path/to/decrypted
   ```

3. 查看：在仓库根目录启动一个静态 HTTP 服务器，打开示例页查看角色、动作与头顶件：

   ```sh
   python -m http.server 8000
   # 浏览器打开 http://localhost:8000/examples/viewer/index.html?base=../../local-data
   ```

master 表由调用方提供。使用 `--master <目录>` 读取本地 `<表名>.json`，或使用 `--master-url [基址]` 按 `<基址>/<表名>.json` 获取；省略 URL 值时使用公开默认基址。`--master-cache <目录>` 缓存远程表。没有 master 输入时会跳过并报告 `characters.json`，但角色模型、动作、表情、表演和头顶件提取仍可运行。直接对话提取需要 master 才能判定单角色归属。

`characters.json` 在有 master 时还读取 `clientConfigs.json`。`77`、`78`、`95` 分别表示普通移动倍率、采集场地移动倍率和冲刺速度倍率。registry 会输出这些解析行及派生的普通走路、冲刺速度。若 `clientConfigs.json` 或任一必需行缺失，只有 `player` 为 `null` 并在 `summary.missing.playerConfig` 登记缺口；只要各自表存在，registry 的其它部分仍会输出。

## 命令

查看可用命令参数：

```sh
moly --help
moly extract --help
moly pull --help
moly characters --help
moly motion-library --help
moly facial-tables --help
moly phenomena --help
moly fetch-apk --help
```

从已解密 bundle 目录批量提取（`--manifest` 可选，省略时选择目录中所有可识别的资产包成员）：

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

单独提取天气（现象）资产包。一个现象跨多个包（全局、共享、每站点一个），全部一起传入；`--bundle-root` 指定依赖包（材质、贴图与着色器）所在目录，缺了它材质会解析不出：

```sh
moly --unity-version 2022.3.62f3 phenomena --bundle path/to/decrypted/<现象全局包> --bundle path/to/decrypted/<现象站点包> --bundle-root path/to/decrypted --out-dir out/phenomena --master path/to/master
```

现象的音乐与环境音要解码时再加 `--vgmstream`（见下面[可选外部依赖](#可选外部依赖音频解码)）：

```sh
moly --unity-version 2022.3.62f3 phenomena --bundle path/to/decrypted/<现象全局包> --bundle-root path/to/decrypted --out-dir out/phenomena --master path/to/master --vgmstream path/to/vgmstream
```

从 `AssetBundleInfoNew` 清单拉取：

```sh
moly --unity-version 2022.3.62f3 pull --manifest path/to/AssetBundleInfoNew.json --asset-base-url https://assets.example.invalid/base
```

`AssetBundleInfoNew` 清单是唯一的 bundle 元数据来源。清单必须由用户提供，并应包含每个入口 bundle 的 `bundleName`、`downloadPath`、`cacheFileName` 和 `dependencies`；不要从本地目录名或网络 URL 猜测依赖关系。`pull` 从清单选择**提取器已支持的每个域**的根 bundle——角色模型、共享动作、角色设置、表演编排、头顶件、对话、天气（现象）环境包与现象缩略图包，加上材质查找源——随后递归解析依赖并检测循环。选根用的就是提取时用的同一套路由，两边不会走偏。**音频包多一步**：它不被任何包声明为依赖，包名只写在 master 行里，所以给了 `--master`（或 `--master-url`）时 `pull` 先读表拿到包名再一并下载，没给就一个也不拉并在摘要里写明原因（详见 [docs/pull.md](docs/pull.md)）。提取产物默认写入 `local-data/`（`--extract-out` 可改）；下载与解密工作区默认在 `moly-pull-output/`（`--out` 可改）。

一条命令拿到含音频的完整产物（`--vgmstream` 可选，缺了只是不解码）：

```sh
moly --unity-version 2022.3.62f3 pull --manifest path/to/AssetBundleInfoNew.json --asset-base-url https://assets.example.invalid/base --master path/to/master --vgmstream path/to/vgmstream
```

`--asset-base-url` 指定资源网络端点。它是不透明前缀，工具不解析其内部结构；每个请求 URL 按 `<asset-base-url>/<downloadPath>/<bundleName>` 组成，结尾保留清单中的 `bundleName`（包括其中的斜杠）。端点和包清单均由用户自行提供，本仓不内置、不分发。下载支持重试和 `.part` 续传，并校验清单中可选的 SHA-256 摘要。wrapper 会先解密，然后调用提取流程。

发现、下载或检查 Android APK：

```sh
moly fetch-apk latest
moly fetch-apk download package.apk --url https://example.invalid/package.apk --sha256 sha256:HEX
moly fetch-apk inspect package.apk
```

`fetch-apk` 可发现公开 Android 包、续传 `.part` 下载、校验可选 SHA-256，或列出 APK 中的资源容器候选；它不会自动提取游戏数据。

### `moly site`

提取站点（place）资产包：九个站点的场景、室内套件与扩建档、房间皮肤、场地物件、世界地图与站点系统外壳。

```sh
moly --unity-version 2022.3.62f3 site \
  --bundle path/to/decrypted/mysekai__site__field__grasslands \
  --bundle path/to/decrypted/mysekai__site__my_room_asset__common \
  --bundle-root path/to/decrypted \
  --out-dir local-data/site \
  --master path/to/master
```

`--bundle` 可重复；`--bundle-root` 是依赖查找目录（材质的 shader 与室内套件都从这里找）。不给 master 表时几何、碰撞面、导航与普查照样出，只有放置表空着并写明理由。`moly extract` 会把站点路径下的包自动认成一次站点任务，不必单独跑这条。

## 可选外部依赖：音频解码

音频包里没有音频文件，只有一个中间件容器格式的音频档（`.acb`）。**取出这个档不需要任何外部程序**，所以它总是被写出来；**解码**它需要一个外部程序：

- **[vgmstream](https://vgmstream.org/)** 的命令行版 `vgmstream-cli`。**本仓不内置、不分发、不为它设置构建步骤**，它有自己的许可与归属（见其自带的 `COPYING`）；请自行获取，然后用 `--vgmstream <可执行文件或所在目录>` 指给本工具，或放进 `PATH` 让它自动找到。
- **ffmpeg**（可选，`--ffmpeg`）：只用来在解码出的波形旁边再写一份压缩副本（`.ogg`）。不给它就只有未压缩波形。

**两者都缺不会让提取失败。** 没有解码器时音频条目记为 `status: "skipped"` 并写明缺什么，音频档仍在盘上，装好工具后重跑即可解码——这与没给 master 表时 `characters.json` 记为 `skipped` 是同一套语义：**缺输入就说清缺什么，不静默少产物，也不假装成功**。

未压缩波形很大（当前内容全部解码约 150 MB），压缩副本约为其百分之一；两者都只落在 `local-data/`（不进版本库）。

## 输出

角色提取写入 `<name>.glb` 和 `<name>.rig.json`。动作提取写入 glTF 动作库和 `<name>.index.json`。表情提取写入表格 JSON。表演编排提取写入 `alone-actions.json`（动作与表情的配对与时序）。对话语料提取写入 `talks.json`（单角色直接对话，需 `--master`）。头顶件提取写入 `emoticons/`（一份共享 `emoticons.json` 加每张贴图一个 PNG）。天气（现象）提取写入 `phenomena/`：一份共享 `index.json`（现象清单；有 `--master` 时并入现象行、刷新窗口与音乐/环境音的 cue 与包名），每个现象一个目录（`config.json`、`ramp.png`、`postprocess.json`、`fx/effects.json`、`textures/`，以及带覆盖的站点的 `overrides/<站点>/`），另有全部现象图标的 `icons/`、去重后的几何 `models/`（模型资产与网格发射器的 `.glb`）与 `audio/`（音频档原样，加上给了外部解码器时的波形与 `loop.json` 循环点）。站点（place）提取写入 `site/`：`index.json`（域索引：常量、场景表、室内拼装口径、各族清单）、`sites.json`（九行放置表 + 世界坐标 + 网格常量 + 等级与网格范围 + 脚步声表）、`packages.json`（站点路径下 109 个包的普查与逐对象记账），以及每包一个目录：`scenes/<站点>/`（几何 `.glb`，一个 prefab 根一个 glTF scene；碰撞面一面一个文件；出货烘焙的 `navmesh/`；`textures/`）、`indoor/`（室内套件 + 每个扩建档的模块与可行走面）、`skins/`、`props/`、`sitemap/`、`travel/`、`preview/`、`shell/`。**站点几何一律保持包自己的原点，世界偏移只在 `sites.json` 里**——烤进几何会让共用一个包的二三楼永久丢失。给了 `--master` 时另写 `characters.json`（角色在册与移动人格；master 表由使用者自备，本仓不内置）。清单提取始终写入 `extraction-report.json`，并在有角色产物时按盘上实有的 `sd_<编号>.glb` 重建 `manifest.json`（浏览器示例直接读取的角色清单）。`pull` 的提取产物与 `extract` 相同地落在 `local-data/`；其工作区另外生成 `raw/`、`decrypted/`、`downloads.json` 和 `extraction-manifest.txt`。

参阅 [数据契约](docs/data-contract.md)、[清单提取](docs/extract.md)、[拉取流程](docs/pull.md)、[动作重定向](docs/retarget.md) 和 [着色器提取](docs/shader.md)。

## 许可证

MIT。第三方归属见 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
