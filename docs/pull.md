# 拉取流程

[English](pull.en.md)

包清单（AssetBundleInfoNew）由用户自行提供，本仓不内置、不分发任何游戏数据。

`moly --unity-version 2022.3.62f3 pull --manifest manifest.json --asset-base-url https://assets.example.invalid/base` 接受 `AssetBundleInfoNew` JSON 对象或数组。根 bundle 从 `bundleName` 选择——**提取器已支持的每个域各选一批**，包括角色模型、共享角色动作、角色设置、表演编排、头顶件、对话、天气（现象）环境包与现象缩略图包、站点（`mysekai/site/**`）路径下的全部包，加上材质查找源——随后递归解析带循环检测的 `dependencies`。选根用的就是提取时用的同一套路由。

**音频包是唯一按名字选不出来的一类，靠 master 表点名补上。** 音乐与环境音的包不被任何包声明为依赖，包名也不写在任何清单字段里：它们由 master 行给出（音乐两层各自点名自己的包，环境音全部在同一个共享包里，所以环境音行只决定「要不要这个共享包」）。所以给了 `--master`（或 `--master-url`）时，`pull` 先读这些表拿到包名，再把清单里确有的那些并入根集合一起下载；**没给 master 就一个音频包都不下载**，默认摘要里直接写明原因：

```text
audio: 13 sound packages named by master tables
audio: no sound package pulled (no master directory or base URL was supplied, and only master rows name the sound packages: ...)
```

包名清单**完全由使用者的 master 表决定**，本仓不内置任何包名。master 点名而这份清单里没有的包会逐个列在 `audio.notInManifest`（默认摘要里也打印），而不是让整次下载因缺依赖中断。完整报告（`--json`）里的 `audio` 段为 `{status, roots, error, tables, absentTables, notInManifest}`：`tables` 给出每张表点名了几个包，`absentTables` 给出没读到的表；这一段只有包名与表名，**不含本机路径**。

`AssetBundleInfoNew` 清单是唯一的 bundle 元数据来源，必须由用户提供。每个入口 bundle 应包含 `bundleName`、`downloadPath` 和 `dependencies`；`cacheFileName` 可作为缓存元数据，但不参与网络 URL。`--asset-base-url` 是用户提供的不透明资源网络端点，工具不解析其内部结构。每个请求 URL 按以下规则组装：

```text
<asset-base-url>/<downloadPath>/<bundleName>
```

`bundleName` 原样保留，包括其中的斜杠。网络端点与包清单均由用户自行提供，本仓不内置、不分发。下载会重试并续传部分文件，并校验清单中可选的 SHA-256 摘要。wrapper 会先解密，然后由 UnityPy 读取，现有提取编排器会写入角色资源包和 `extraction-report.json`。游戏资产 bundle 不携带可读的引擎版本号，所以全局参数 `--unity-version` 对 `pull` 是必需的——缺省时提取段会对每个 bundle 报 `UnityVersionFallbackError` 并以非零码退出。

提取产物默认写入 `local-data/`（`--extract-out` 可改）；工作区目录（默认 `moly-pull-output/`，`--out` 可改）包含 `raw/`、`decrypted/`、`downloads.json` 和 `extraction-manifest.txt`。角色 glb **不内嵌动画**:全部动作在共享动作库 glTF 里,按人形骨骼名绑定(见 [retarget.md](retarget.md));表情表单独成 JSON。头顶件产物使用一个共享的 `emoticons.json`，另为每张贴图写一个 PNG；PNG 文件名带包名前缀，格式为 `<件名>__<贴图名>.png`。 同一次运行内，多个头顶件包会**合并**写入这一份索引；上一次运行留下的索引会在本次运行开始时删除，因此一次运行的产物只含本次提取的件。天气（现象）产物写入 `phenomena/`：一份共享 `index.json` 加每个现象一个目录，详见[数据契约](data-contract.md)。现象的粒子材质与贴图住在同一现象的共享包里，`pull` 会随根 bundle 一并拉取这些依赖，所以走 `pull` 的材质是解析好的。**音频包也一并拉取**：它由 master 行点名而不是被任何包声明为依赖（见上），所以给了 master 时它是根集合的一部分，`local-data/phenomena/audio/` 里会有音频档、`loop.json`，装了解码器时还有波形；没给 master 时一个也不拉，音频记为「音频包未提供或不可达」，原因写在摘要里。
master 表是**使用者自备**的输入:角色在册、移动人格、对话归属都只在那里,本仓不内置、不分发任何 master 数据。两种给法:

- `--master <目录>` —— 读本地目录下的 `<表名>.json`;
- `--master-url [基址]` —— 按 `<基址>/<表名>.json` 逐表取;**不带值时用公开镜像的默认基址**(`raw.githubusercontent.com` 上的 `Team-Haruki/haruki-sekai-sc-master`)。配合 `--master-cache <目录>` 把取到的表落盘,第二次运行就读本地不再联网。

master 表是角色 registry、移动人格、对话归属和玩家移动配置的使用者输入。使用 `--master <目录>` 读取本地 `<表名>.json`，或使用 `--master-url [基址]` 获取 `<基址>/<表名>.json`；省略 URL 值时使用公开默认基址。`--master-cache <目录>` 把取到的表落盘，供后续本地读取。

有 master 时，`characters.json` 从 `clientConfigs.json` 生成 `player`：`77`、`78`、`95` 分别为普通移动倍率、采集场地移动倍率和冲刺速度倍率。派生普通走路速度等于普通倍率，派生冲刺速度等于普通倍率乘冲刺倍率。整表或必需行缺失时，registry 仍成功写出，`player` 为 `null` 并登记 `summary.missing.playerConfig`；identity、locomotion、soloAction 与其它提取产物不受阻断。完全没有 master 来源时，registry artifact 跳过；对话包因无法判定归属而记为 `unsupported`。

**清单里的每个条目自带 `downloadPath` 一段**(不同条目可能不同),因此 `--asset-base-url` 必须给到该段**之前**的前缀;把某个条目的 `downloadPath` 一起写进 base 会导致 404。

