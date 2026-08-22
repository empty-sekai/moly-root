# 拉取流程

[English](pull.en.md)

包清单（AssetBundleInfoNew）由用户自行提供，本仓不内置、不分发任何游戏数据。

`moly --unity-version 2022.3.62f3 pull --manifest manifest.json --asset-base-url https://assets.example.invalid/base` 接受 `AssetBundleInfoNew` JSON 对象或数组。角色模型根 bundle、共享角色动作和角色设置会从 `bundleName` 选择，并递归解析带循环检测的 `dependencies`。

`AssetBundleInfoNew` 清单是唯一的 bundle 元数据来源，必须由用户提供。每个入口 bundle 应包含 `bundleName`、`downloadPath` 和 `dependencies`；`cacheFileName` 可作为缓存元数据，但不参与网络 URL。`--asset-base-url` 是用户提供的不透明资源网络端点，工具不解析其内部结构。每个请求 URL 按以下规则组装：

```text
<asset-base-url>/<downloadPath>/<bundleName>
```

`bundleName` 原样保留，包括其中的斜杠。网络端点与包清单均由用户自行提供，本仓不内置、不分发。下载会重试并续传部分文件，并校验清单中可选的 SHA-256 摘要。wrapper 会先解密，然后由 UnityPy 读取，现有提取编排器会写入角色资源包和 `extraction-report.json`。游戏资产 bundle 不携带可读的引擎版本号，所以全局参数 `--unity-version` 对 `pull` 是必需的——缺省时提取段会对每个 bundle 报 `UnityVersionFallbackError` 并以非零码退出。

提取产物默认写入 `local-data/`（`--extract-out` 可改）；工作区目录（默认 `moly-pull-output/`，`--out` 可改）包含 `raw/`、`decrypted/`、`downloads.json` 和 `extraction-manifest.txt`。角色 glb **不内嵌动画**:全部动作在共享动作库 glTF 里,按人形骨骼名绑定(见 [retarget.md](retarget.md));表情表单独成 JSON。头顶件产物使用一个共享的 `emoticons.json`，另为每张贴图写一个 PNG；PNG 文件名带包名前缀，格式为 `<件名>__<贴图名>.png`。 同一次运行内，多个头顶件包会**合并**写入这一份索引；上一次运行留下的索引会在本次运行开始时删除，因此一次运行的产物只含本次提取的件。
master 表是**使用者自备**的输入:角色在册、移动人格、对话归属都只在那里,本仓不内置、不分发任何 master 数据。两种给法:

- `--master <目录>` —— 读本地目录下的 `<表名>.json`;
- `--master-url [基址]` —— 按 `<基址>/<表名>.json` 逐表取;**不带值时用公开镜像的默认基址**(`raw.githubusercontent.com` 上的 `Team-Haruki/haruki-sekai-sc-master`)。配合 `--master-cache <目录>` 把取到的表落盘,第二次运行就读本地不再联网。

master 表是角色 registry、移动人格、对话归属和玩家移动配置的使用者输入。使用 `--master <目录>` 读取本地 `<表名>.json`，或使用 `--master-url [基址]` 获取 `<基址>/<表名>.json`；省略 URL 值时使用公开默认基址。`--master-cache <目录>` 把取到的表落盘，供后续本地读取。

有 master 时，`characters.json` 从 `clientConfigs.json` 生成 `player`：`77`、`78`、`95` 分别为普通移动倍率、采集场地移动倍率和冲刺速度倍率。派生普通走路速度等于普通倍率，派生冲刺速度等于普通倍率乘冲刺倍率。整表或必需行缺失时，registry 仍成功写出，`player` 为 `null` 并登记 `summary.missing.playerConfig`；identity、locomotion、soloAction 与其它提取产物不受阻断。完全没有 master 来源时，registry artifact 跳过；对话包因无法判定归属而记为 `unsupported`。

**清单里的每个条目自带 `downloadPath` 一段**(不同条目可能不同),因此 `--asset-base-url` 必须给到该段**之前**的前缀;把某个条目的 `downloadPath` 一起写进 base 会导致 404。

