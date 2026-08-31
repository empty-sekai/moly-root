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

路由器当前识别角色模型 bundle、共享角色动作 bundle、角色设置 bundle、表演编排 bundle、头顶件（emoticon）bundle、对话 bundle（需要用户提供 master 表），天气（现象）的环境 bundle、共享现象缩略图 bundle 与音频 bundle（`mysekai/sound/**`），站点（`mysekai/site/**`）路径下的全部包，家具接口包（`mysekai__fixture__**`）、剧情演出 timeline 包（`mysekai__cut_scene__**`）与家具演出 timeline 包（`mysekai__fixture_timeline__**`），以及对话相机包（`mysekai__camera`，精确名而非前缀）。每个请求项都会生成包含 `status`、`artifacts`、`counts` 以及错误信息的报告项。缺失 bundle 会标记为 `failed` 并使命令以非零状态退出。未注册提取器的域会保留为 `unsupported`，为未来的资源域扩展保留报告入口。

**头顶件与现象各自只有一次任务。** 两者都写一份共享索引，所以一次运行里它们各自的全部 bundle 一起提取一次，而不是逐包各跑一次：现象跨多个包（全局、共享、每站点一个），把它们分开跑会让站点覆盖与跨包材质都解析不出。逐包的 `counts` 仍逐条给出该包贡献了什么，另有一条 `derived` 项记 `phenomena/index.json` 的总计。**现象提取会额外读取每个包自己声明的依赖包**（材质、贴图与着色器），它们从 `--bundles` 同一目录里找；找不到的依赖登记在 `phenomena/index.json` 的 `summary.missing.dependencies`，指针保留为未解析状态而不是当成「没有材质」。

**站点也只有一次任务。** 站点路径下的全部包（场景、室内套件、扩建档、房间皮肤、场地物件、世界地图、外壳）在一次运行里一起提取一次：一个扩建档的网格住在套件包里、一份材质的 shader 住在共享包里、放置表横跨全部站点，逐包各跑一次这三样都解析不出。逐包 `counts` 给该包贡献的几何、碰撞面、导航件、材质与贴图数，另有一条 `derived` 项记 `site/index.json` 的总计。产物落在输出目录的 `site/` 下，形状见[数据契约](data-contract.md#site)。

站点也可以单独跑：

```sh
moly --unity-version 2022.3.62f3 site \
  --bundle path/to/decrypted/mysekai__site__field__grasslands \
  --bundle path/to/decrypted/mysekai__site__my_room_asset__common \
  --bundle-root path/to/decrypted \
  --out-dir local-data/site \
  --master path/to/master
```

`--bundle-root` 是依赖查找目录（shader 与套件都从这里找）；不给 master 表时几何、碰撞、导航与普查照样出，只有放置表 `sites.json` 空着并在 `summary.missing.master` 写明理由。**站点几何一律保持包自己的原点**，世界偏移只在 `sites.json` 里——这条是消费方须知，理由见契约。

**音乐与环境音是第三类输入，且要求两件东西同时在场。** 现象的音频包名由 master 行给出（音乐两层各自点名自己的包，环境音全部在同一个共享包里），提取时按这些名字**在 `--bundles` 同一目录里**查找——它们不是任何包声明的依赖，所以 `extract` 不会自动发现它们：走 `extract` 时需要自行把这些包放进同一目录（**走 `pull` 不必**，`pull` 会按同样的 master 行把它们一并下载，见 [拉取流程](pull.md)）。找不到的包逐条记进 `unsupported`，原因为「音频包未提供或不可达」。取出音频档不需要外部程序，**解码**才需要（`--vgmstream`，见 README 的可选外部依赖一节）；解码器缺席时音频记 `skipped` 而不是失败。

**音频包自己也是一个域。** 路由器把 `mysekai/sound/**` 认成 `sound` 域，所以在清单里点名它们、或让 `--bundles` 目录发现它们时，报告里都有对应条目：`counts` 给 `{streams, cues, archiveBytes}`。**没有任何 master 行点名的音频包记为 `unsupported`**（原因写明「没有 master 行点名这个音频包」）——它与「点名了却读不出」是两个答案，不能长得一样。

**家具接口也只有一次任务。** 家具路径下的全部包（当前内容 999 个）在一次运行里一起提取一次：角色挂点索引与逐家具的格集合各写一份共享产物，逐包各跑一次这两份索引都写不成。产物落在输出目录的 `fixture-interface/` 下：`attach-points.json` 记挂点对（当前内容 441 对，来自 195 个包），`areas.json` 记每个包 `FixtureBundleMeta` 里的格集合（当前内容 858 份 meta，其中非空的 `motionArea` 184 份、`stackEnables` 92 份、`AddUsingGrid` 22 份、`cutsceneArea` 2 份）。**域名是 `fixture-interface` 而不是 `fixture`**：这一族只读出上面两样，叫 `fixture` 就等于承诺读出了整族。

**两族演出 timeline 各自只有一次任务。** 剧情演出（`mysekai__cut_scene__**`，当前内容 92 包、92 条 timeline）与家具演出（`mysekai__fixture_timeline__**`，当前内容 87 包、682 条 timeline）是两个域，不是一个「演出」域——产物按宿主分开，不合并。每族的一次任务里做同样的三次读取：轨树、clip 的时间字段与 clip target，各写到该族输出目录的 `tracks/`、`clips/`、`clip-targets/` 下；一个包的指针会伸到同族的其它包，逐包各跑一次这三样都解析不出。

角色产物按 `sd_<unit>` 命名（例如 `sd_112.glb` 与 `sd_112.rig.json`），与浏览器示例的消费约定一致。有角色产物时会按盘上实有的 `sd_<unit>.glb` 重建 `manifest.json`（浏览器示例直接读取的角色清单），重复或分批运行始终如实反映当前目录内容。默认在 stdout 打印几行摘要与查看提示；`--json` 改为输出完整报告。`extraction-report.json` 始终落盘。角色条目会使用同一清单中存在的动作 bundle 和设置 bundle。第一个角色模型为动作库提供参考骨架。批量输出和 `extraction-report.json` 都写入输出目录。
**对话相机是一个精确包名的域。** 路由器把 `mysekai__camera` 这一个包认成 `camera` 域——用的是精确名而不是前缀：写成前缀会把日后出现的 `mysekai__camera_*` 邻居一起吞进相机任务，而那些包的内容没人读过。邻居因此仍然以 `unsupported` 的形态留在报告里。`counts` 给该包的 `CameraParam` / `CameraSetting` 实例数、空曲线数与字段完整性问题数，逐包产物落在输出目录的 `camera/` 下。

**对话包同时喂两个提取器。** `mysekai__talk__scenario__talk` 这一个包里装着两族对话：不在家具旁的单角色对话，和家具旁的对话。两者各自读同一个包、各写各的产物——前者写 `talks.json`，后者写 `fixture-talks/talks.json`——所以这个包上跑两次提取，而不是两条路由：一个包不能属于两个域。家具旁那一族另有一条 `derived` 项记它自己的成败，好让它失败时不被读成前者失败、成功时也不替前者背书。当前内容里家具旁那一族共 4768 条（自发型 258 条、玩家参与型 4510 条）。

**动画片段的导出是一次派生任务，不是一个域。** 导出哪些片段由两个时间轴任务刚写下的 clip-target 文档决定，从来不由包名决定，所以它没有路由——给它编一条路由就等于承诺某个包名能选中它，而没有任何包名能。它在两个时间轴任务之后运行，读它们的产物，写到输出目录的 `perf-animations/` 下，并在 `derived` 里记 `targets` 与导出、跳过、剩余的计数；导出失败的包记进这一项的 `error`，不折进已导出的计数里。当前内容里 `cut_scene` 侧的 target 导出 371/371、`fixture_timeline` 侧 1401/1403。**导出不等于可播**：glTF 可播的分别是 361/371 与 859/1401，差额来自 1658 条通道路径哈希未解析——它们已分类、没有兜底类，指向的是下发 prefab 里不存在的层级，即游戏侧作者态与下发态的漂移，不是提取缺陷；未解析的绑定原样留在产物里并单独计数，不被丢掉。

**对话 UI 的输入不是任何可下载的包。** 它在 APK 的 player data 里，没有包名能指到它，所以它不由路由认领：`extract` 接受调用方传入这个文件的路径（`player_data`），传了就读，报告的 `playerData` 一节记 `ui/talk.json` 的窗口数、节点数、组件数与「解完剩余 0 字节」的自证计数。**没传时这一节照样在，状态记 `skipped` 并写明理由**——条目缺席会被读成「这个域不存在」，而事实是「没人给它输入」，两者不能长得一样。读出的是每个根行为下的整棵窗树；没有手解器的组件带着读取方自己的 partial 标记留在产物里并单独计数，不被丢掉。**字段手解尚未全部收口**：真数据上 290 个对象解完残余 0 字节，另有 45 个记为 partial。

**现象域的关键帧刻意不带加权字段,值已知全为零。** `core.particles` 导出的每个关键帧带 `weightedMode` / `inWeight` / `outWeight`——Unity 的加权模式改变三次插值的求值式,读不到它的消费者会在加权帧上静默算错,所以这三个字段一律导出而不是省略。**但现象域的产物没有它们,这是有意的**:直接扫该域的 111 个 bundle 的原始 typetree,8,289 条曲线、18,668 个关键帧的 `weightedMode` 全为 0;产物导出的曲线是原始曲线的子集,超集为零则子集必为零,所以不重跑该域缺失的就是「零」本身。**同一次扫描在站点域测出 457 个、头顶件域测出 16 个加权关键帧**,所以那个零是量出来的零而不是量具失灵。不重跑的另一半理由是代价:该域还吃 `--vgmstream` / `--ffmpeg` 两个音频解码器路径,而它们没有留在任何一份运行记录里,不带它们重跑会让已经产出的 `.ogg` 消失——拿正在服务的音频换一个已知为零的增量不成交易。该域的 bundle 名单本身不需要考古:路由器在解密根上把 111 个包认成 `phenomena` 域。
