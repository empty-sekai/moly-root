# moly-root 角色导出数据契约

[English](data-contract.en.md)

包清单（AssetBundleInfoNew）由用户自行提供，本仓不内置、不分发任何游戏数据。

本文是 `moly characters` 和 `moly facial-tables` 输出文件的公开使用者契约。所有 JSON 数字都是 JSON number；四元数使用 `[x, y, z, w]`，向量使用 `[x, y, z]`。

## 坐标系与身份

角色输出为 glTF 右手、Y 向上、米制。Unity 位置转换为 `(x,y,z) -> (-x,y,z)`，旋转转换为 `(x,y,z,w) -> (x,-y,-z,w)`，三角形绕序反转，UV 的 V 写为 `1-v`。反射只能应用一次；重复反射会恢复到源坐标系并导致剔除和动画方向错误。

节点数组索引是一次导出内稳定的身份。名称是标签而非唯一键；可能出现同名兄弟节点。蒙皮、布料和动画目标应使用节点索引（以及存在时的路径）。

## `.glb`

二进制 glTF 包含完整的运行时角色表现。`asset.extras.coordinates` 声明反射后的坐标约定；`nodes[]` 保存 prefab 变换树；`skins[].joints[]` 是渲染器骨骼顺序；`inverseBindMatrices` 是 glTF 空间中的绑定姿势；`meshes[].primitives[]` 的材质整数和顺序权威；`TEXCOORD_0..2` 的 V 已翻转；`COLOR_0` 可选；`materials[].extras` 保存着色器角色输入和纹理索引；`images[]` 保存 PNG 缓冲区；`animations[]` 保存烘焙人形旋转、辅助扭转旋转和髋部平移。

面部渲染器材质顺序是 `[eye, mouth]`；身体渲染器以 `body` 开始，可能包含 `accessory`。mesh primitive 顺序是权威顺序。

## `.rig.json`

sidecar 提供 glTF 不建模的数据：`name`、`unitId`、`defaultEye`、`defaultMouth`、`eyeAtlas`、`mouthAtlas`、`materials` 和可选 `cloth`。

`anchors` 给出角色身上的挂点：键是模型里声明这些挂点的字段名，值是对应的节点名（当前内容为 `_headRoot` → `Head`、`_headTopRoot` → `HeadRoot`、`_spineRoot` → `Spine`、`_hipsRoot` → `Hips`、`_lightingHeadCenter` → `null`）。字段存在但未指向节点时为 `null`，字段不存在则不出现——**「这个骨架没有这个挂点」与「导出器没看」是两件事**。

头顶件就挂在其中一个挂点上，**局部位移为零**（不要自行抬高）；sprite 类头顶件在运行时每帧朝向相机。头顶件数据里 `view.anchor` 的 `Face`/`Spine`/`Hips` 与这些字段的确切对应关系**尚未取证**,因此本仓不给出该映射。

眼睛和嘴巴图集都包含 `texture`、`cell`、`columns:4`、`rows`、`indexBase:1`、`clampMinIndex:1` 和每格 UV 偏移。眼睛是 4x8 网格（2048x2048，单格 512x256），嘴巴是 4x4 网格（2048x1024，单格 512x256）。选择格子的公式是：

```text
if index < 2: index = 1
i = index - 1
col = i % 4
row = i >> 2
```

只使用偏移，不使用缩放。`unityOffsetPerCell` 的 Y 为负，`gltfOffsetPerCell` 的 Y 为正。静态默认值与闭眼模式字段必须区分；`lip01` 到 `lip16` 直接对应 16 个嘴巴图集格子。

每个材质角色（`body`、`eye`、`mouth`，可选 `accessory`）包含 `name`、`floats`、`colors`、`textures` 和 `renderQueue`。保留浮点源值，不归一化或截断；缺少纹理属性表示缺失，不是隐式替换。

cloth 包含坐标系、版本声明、组件、碰撞体、统计和结构检查。胶囊 `length` 是半长；`startRadius` 对应减方向端，`endRadius` 对应加方向端。忽略烘焙 TRS 或把半长当全长会造成不稳定布料或碰撞体尺寸翻倍。

## `characters.json`

角色在册与移动人格。**输入来自使用者自备的 master 表**（用 `--master <目录>` 指定）：本仓不内置、不分发任何 master 数据；没给这个输入时不产此文件，`extraction-report.json` 的 `derived` 里会留一条 `status: "skipped"` 说明，而不是静默省略。

顶层为 `version`（当前为 1）、`semantics`、`units`、`player`、`characters`、`summary`。

**成员集合由调用方决定**——从清单里的角色包名推出，本模块不猜谁属于这个包。

`player` 是对象或 `null`。它来自使用者的 `clientConfigs.json`，每行格式为 `{id, type, value}`。`type` 为 `Int`、`Float`、`String` 或 `Bool`，`value` 按声明类型解析。本契约使用的玩家行是：

| id | 语义 |
|---:|---|
| `77` | 普通场地移动倍率。 |
| `78` | 采集场地移动倍率。 |
| `95` | 冲刺速度倍率。 |

三行齐全时，`player` 包含 `normalMoveScale`、`harvestMoveScale`、`dashSpeedRate`、`configRows` 和 `derived`。`configRows` 按字符串键（`"77"`、`"78"`、`"95"`）保留这三行的解析值。`derived.walkSpeedMetersPerSecond` 等于 `normalMoveScale`；`derived.dashSpeedMetersPerSecond` 等于 `normalMoveScale * dashSpeedRate`。

玩家在普通场地使用普通倍率，在采集场地使用采集倍率。冲刺是明确的移动状态，会把当前倍率乘以 `dashSpeedRate`。摇杆未满推时保留输入幅值；会减速的相机状态再额外乘以 `0.5`。这些是玩家移动语义，不是角色移动人格数值。

`clientConfigs.json` 缺失，或 `77`、`78`、`95` 任一行缺失时，`player` 为 `null`，不填默认值。缺口登记在 `summary.missing.playerConfig`：整表缺失时记录 `clientConfigs` 以及三个必需 id；只缺行时记录缺失 id。只要各自来源行存在，identity、locomotion、soloAction 仍照常输出。

`characters[<unitId>]` 包含：

| 字段 | 语义 |
|---|---|
| `unitId` | 角色单位 id。 |
| `identity` | `gameCharacterId`、`unit`、`colorCode`、`skinColorCode`、`skinShadowColorCode1`、`skinShadowColorCode2`；来源行缺失时为 `null`。 |
| `locomotion` | 移动人格，见下；来源行缺失时为 `null`。 |
| `soloAction` | 该角色独处编排脚本的名字（脚本本身在另一个包里）；来源行缺失时为 `null`。 |

`locomotion` 先给**存储值**：`idleMotion`、`walkMotion`、`runMotion`、`walkSpeed`、`runSpeed`、`runOccurRate`、`pauseMilliSeconds`、`changeMotionMilliSeconds`；再给**运行时值**：`walkSpeedMetersPerSecond`、`runSpeedMetersPerSecond`、`pauseSeconds`、`changeMotionSeconds`。

> **存储值是运行时单位的 1000 倍。** 运行时读表之后立刻把这四个值各除以 1000，所以 `walkSpeed` 400 是**每秒 0.4 米**、`pauseMilliSeconds` 15000 是**15 秒**。请用 `*MetersPerSecond` / `*Seconds` 这几个字段；直接用 `walkSpeed` 会让角色快 1000 倍。

`runOccurRate` 是「跑而不是走」的百分比概率，按存储值给出。三个动作名都是共享动作库索引的键，与编排数据里的动作名同一套。

`summary` 给出 `requested`、`withIdentity`、`withLocomotion`、`withSoloAction`、`missing`（角色 → 缺失的来源种类）、`motionsChecked`、`motionsNotInLibrary`（角色 → 不在动作库索引里的动作名）。**缺失一律登记，不填默认值**：当前内容里 31 个成员的身份与移动人格齐全，`soloAction` 少一个（`unitId` 21），31×3 个动作名全部能在动作库索引里找到。

## `facial-tables.json`

这是一个值为行对象数组的 JSON 对象。三个公共逻辑表是 defaults、eyes 和 mouths，但键名由资源定义；应按字段识别行，不要假定文件名。

`defaults.CharacterUnitId` 为 1..55 的整数。55 行都存在，但只有 31 个 ID 有模型；其余必须报告为空模型，不得伪造。`EyePatternName` 和 `MouthPatternName` 链接眼睛及嘴巴行。肌肉通道不限制在 `[-1,1]`，不要截断；观察到的源值约为 `-12.06..+9.31`。

## `alone-actions.json`

角色**表演编排**数据：哪个动作配哪张脸、在什么时刻切换。动作与表情是**两条独立通道**，
动作名不隐含任何表情；配对只存在于这份编排数据里。没有它，消费者只能挂一张静止默认脸，
角色会「动作对、表情不对」。

顶层：`version`（当前为 2）、`semantics`、`constantTables`、`constantScalars`、`units`、`summary`。

`units` 按角色数字标识分组，每个角色是：

- `scenarios[]` —— 互斥的表演场景，**不要把它们串成一条时间线**。`trigger.kind` 两种：
  - `timeGated`：`timeLimitSeconds`（距上次满足的最短间隔）、`probability`（0..1 掷骰）、
    `motionSlot` 与 `slotMemorySeconds`（同槽位去重窗口）。多个场景各自独立判定。
  - `randomBranch`：`low`/`high`/`weight`，来自一次 0..99 抽样的互斥分支。同一角色的
    各分支权重之和为 1。
- `tail.steps[]` —— 循环尾段，**每轮都执行**，不属于任何场景。
- `constants` —— 该脚本自带的门限与槽位常量原值。

`steps[]` 按源顺序排列，每步带 `t` 与 `op`：

| `op` | 字段 | 语义 |
|---|---|---|
| `animation` | `motion`、可选 `phase`、`speed`、`playbackSpeed`、`playEndMotion`、`blend`、`alias`、`phaseSource` | `motion` 是共享动作库索引 `clips` 的键；`phase` 为 `S`/`L`/`E`/`O` 时播该相位段，缺省时由消费者按索引里存在的相位决定。`speed` 是脚本写的播放速率，当前 747 个 animation 步骤全部为 0；`playbackSpeed` 是运行时实际使用的速率，因为运行时把 0 读成 1.0，所以按它播放，**直接使用 `speed` 会让角色定格**。`playEndMotion` 为真时在主段之后再播该动作的 End 段，脚本不指定时为假。`blend` 是切入新动作的交叉淡入秒数，每个 animation 步骤都有；脚本省略时取表演库默认值，当前为 0.5。 |
| `eye` | `pattern` | 眼表的 `PatternName`；取该行 `OpenEyeIndex` / `CloseEyeIndex` 得图集格 |
| `mouth` | `pattern` | 口表的 `Name`；取该行 `OpenLipSyncIndex` / `CloseLipSyncIndex` 得图集格 |
| `emoticon` / `hideEmoticon` | `name`、可选 `showSeconds` | 头顶件名，资源在独立的特效包里，不在角色资产包内 |
| `wait` | `seconds` | 推进标称时间轴 |

`t` 是**标称时间轴**：此前所有 `wait` 之和，即作者编排的时刻。运行时以整毫秒延时在协程上等待，
动作调用不等待完成，所以真实切换时刻带量化与帧调度误差。把 `t` 当顺序 + 标称偏移的契约，
不要当逐帧时刻表。

表演库不会转发它动作调用的第 4 个位置参数，因此 `alone-actions.json` 不记录该参数。

`alias` 与 `phaseSource` 记录该值在源常量表里的名字，仅供溯源；相位别名与相位字母同名，
判断是否已解析只能看 `phaseSource` 是否存在。

编排在角色处于休息状态且无过场时运行，打断是协作式的（循环在自己的守卫处退出）。

## `talks.json`

单角色直接对话的语料。**需要使用者自备的 master 表**（`--master <目录>`）：一条对话归属哪个角色、在什么条件下出现，都只写在 master 里；没给这个输入时 `extraction-report.json` 会把该包记成 `unsupported` 并说明原因，而不是产出一份范围不明的语料。

顶层为 `version`（当前为 1）、`semantics`、`units`、`summary`。

**收录判据**：一条对话的角色组恰好一人，**且**它的条件组不含家具型条件（家具不属于角色）。当前内容里 6180 条对话按此保留 **1412 条**（4354 条因家具条件、414 条因角色组多于一人被排除），覆盖 **30 个角色**，每人 46–48 条。判据的两半各自计数都在 `summary.filter.dropped` 里。

`units[<unitId>].talks[]` 每项：

| 字段 | 语义 |
|---|---|
| `talkId` | 对话 id。 |
| `lua` | 脚本名。脚本本体在对话脚本包里（全部对话共用一个包），包内资产名比这里**多一个 `.lua` 后缀**。 |
| `siteGroupId` / `termId` | 站点组与时段；取值语义未取证，原样给出。 |
| `conditions` | 该对话条件组里的条件类型（家具型的对话已被判据排除，所以这里只会出现现象、访问次数、活动剧情三类）。 |
| `tweet` | `{id, text, motion, eye, mouth}`；`text` 是原文含 `\n`。**这是除独处编排之外的第二处「动作↔表情」配对来源。** |
| `voices` | 脚本里引用的语音 cue 名。**语音字节不在这个包里**，本仓也不提供 cue 到语音包的映射（未取证）。 |
| `steps` | 按脚本顺序解析出的编排步骤，每步带 `op`。 |

`steps` 的 `op` 是脚本调用名。当前内容实际出现 14 种：`change_npc_eye` 5037、`change_animation` 3827、`change_npc_mouth` 3724、`label` / `text` / `wait_click` 各 3571、`voice` 3137、`look_at_body` 2823、`wait_time` 1413、`emoticon` 355、`show_talk_window` / `hide_talk_window` 各 2、`hide_emoticon` 1、`wait_time_on_auto_mode` 1。解析器认识的调用名多于这 14 种；**每个匹配到的调用都必须被计入 `steps` 恰好一次,数不上就报错**，不会静默丢步骤。

动作步骤的播放速率与独处编排同一套：`speed` 是脚本写的值、`playbackSpeed` 是运行时实际使用的值（运行时把 0 读作 1.0）。

**常量未解析**：对话脚本包里没有常量表，所以 `Characters.X`、`EyePresets.x`、`LipSyncPresets.x`、`Motions.x` 这类具名常量**原样保留为字符串**（`summary.constantTables` 为空即表示如此）。本仓不猜这些名字到真值的映射；需要真值时请自行提供常量表。

## `emoticons/`

头顶件（情绪气泡）效果包。**跨角色共用**：角色资产包只按名字引用它们，不内嵌。
编排数据里 `op` 为 `emoticon` 的步骤，其 `name` 就是这里的键。

整个导出只有一个共享的 `emoticons.json`，角色资产包只按名字引用它们，不内嵌；每张贴图另有一个 PNG 文件。编排数据里 `op` 为 `emoticon` 的步骤，其 `name` 就是这里的键。

当前内容规模为 53 个件、74 张贴图、85 个粒子发射器、93 条 `unsupported`。

**粒子的发射量本来就很小**：32 个 particle 件合计只有 155 个爆发粒子 + 12 个连续发射粒子,**每件理论发射量的中位数是 3 个**(最多的一件 30 个,最少的 1 个);85 个粒子系统里只有 51 个带发射模块,`rateOverTime` 有 47 个系统就是 `0`(靠 `bursts` 出粒子)。`bursts` 每项为 `{time, count, cycleCount, repeatInterval, probability}`；**`cycleCount` 为 `0` 表示无限轮**——每过 `repeatInterval` 秒重发一轮（当前内容里 8 个件共 13 个这样的 burst），非零值是固定轮数；上面的爆发计数按**单轮**口径统计。渲染出两三个粒子是**对的**,不要当成渲染故障去提高发射率——`0` 就是 `0`。件分为 21 个 `sprite` 件和 32 个 `particle` 件：前者有 Animator 以及 `start`/`loop`/`end` 三个片段；后者没有 Animator 或片段，视觉是粒子发射器，导出的是**发射参数**（形状、发射率、寿命、随生命变化的大小与颜色、以及所用材质）而非烘好的帧，消费者据此模拟。

`emoticons.json` 顶层为 `version`（当前为 2）、`semantics`、`items` 和 `summary`。`summary` 为 `{items, textures, unsupported[]}`，其中 `unsupported` 的每项都带 `item` 字段。

提取器的输入分为两类：`mysekai/effect/emoticon/*`（或等价的双下划线名称）是**目标包**，每个目标包产出一个 `items` 条目；显式传入的其它包是**查找源**，只建立材质和 Shader 的跨包解引用索引，不产出 `items`，也不计入 `summary.items`。因此 `mysekai/shader` 只能作为查找源。没有传入的依赖不会被猜测，仍按 `external` 状态保留。

`items[<件名>]` 包含：

| 字段 | 语义 |
|---|---|
| `viewKind` | `"sprite"` 或 `"particle"`。 |
| `view` | `{class, kind, soundLabelType, soundInput}`；`particle` 件另有 `anchor` 与 `keepPosition`。 |
| `animator` | `{node, loopEndFlag}`；无 Animator 时为 `null`。 |
| `nodes` | 节点树，父节点在前。每项有 `name`、`path`、`parent`、`animationPath`、`active`、`position`、`rotation`、`scale`；画图节点另有 `sprite`、`sortingOrder`、`color`、`flipX`、`flipY`、`rendererEnabled`。 |
| `sprites` | `{<sprite 名>: {texture, file, rect, pivot, pixelsToUnits}}`。 |
| `textures` | `[{name, file, width, height}]`。 |
| `clips` | `{start|loop|end: {name, rate, duration, frames, channels}}`；`particle` 件为空对象。 |
| `particles` | 发射器数组，见下节；`sprite` 件为空数组。 |
| `dependencies` | 该件还需要哪些包（变体件可能复用主件材质）。 |
| `unsupported` | 本件未建模内容，逐条带原因。 |

`nodes[]` 的 `path` 相对包根，根自身为 `""`；`parent` 是父节点的 `path`，根为 `null`。`animationPath` 相对 Animator 所在节点；`null` 表示该节点在 Animator 之上。画图节点还带 `material`：无材质为 `null`，未解析的跨包指针保留 `{external: true, fileId, archive}`，解析成功时为材质对象。`position` 为 `[x,y,z]`，`rotation` 是四元数 `[x,y,z,w]`，`scale` 为 `[x,y,z]`。

动画通道用 `animationPath` 匹配，**不是** `path`：片段绑定路径相对 Animator 所在节点，而不是包根。通道同时给出 `pathHash` 与解析出的 `path`（就是 `animationPath` 的值）；解析不出的哈希会进入 `unsupported`，原因为 `path hash unresolved`。

一个件可以有多张贴图、多个 sprite 和多个画图节点（最多 3 个 sprite、6 个画图节点）；`sortingOrder` 决定叠放次序。贴图文件名带包名前缀，格式为 `<件名>__<贴图名>.png`，因为不同变体件可能使用同名贴图；应按 `sprites[..].file` 或 `textures[..].file` 取文件，不要自行拼接名称。

`clips` 的播放顺序为：`start` 播一次，随后 `loop` 循环；运行时置 `animator.loopEndFlag` 命名的 Animator 标志后退出循环，`end` 播一次，1 秒后销毁。`channels[].values` 已按片段自身 `rate` 重采样，第 n 帧为 `round(t * rate)`。

`view` 中，`soundLabelType` 是音效调用类型，取值为 `PlaySE`、`StopSE`、`FadePlaySE`、`FadeSEVolume` 或 `FadeStopSE`；`soundInput` 是该调用的参数串，`PlaySE` 时为逗号分隔的 cue 列表，运行时随机取一个。`particle` 件的 `anchor` 取值为 `Face`、`Spine` 或 `Hips`，表示件挂到身上的锚点；`keepPosition` 为真表示留在生成处、不跟随锚点。`sprite` 件由调用方决定位置，因此没有 `anchor` 字段。

`unsupported` 的当前构成为：非 Transform 绑定 63 条、`CanvasRenderer` 18 条、`CustomDataModule` 8 条、`NoiseModule` 2 条、`SubModule` 1 条、`Texture2DArray` 1 条。所有未建模内容都逐条保留。

### `particles[]`

每个会发射的节点一项，`{node, system, renderer}`。`node` 是该节点的 `path`。

`system` 给出发射参数：`duration`、`looping`、`prewarm`、`playOnAwake`、`simulationSpeed`、`simulationSpace`、`randomSeed`、`maxParticles`、`start`，以及仅在对应模块启用时出现的 `emission`、`shape`、`sizeOverLifetime`、`colorOverLifetime`、`rotationOverLifetime`、`velocityOverLifetime`、`limitVelocity`、`textureSheet`。模块启用但本导出器未建模时，该模块名进 `unsupported`，原因为 `particle module not modelled`。

`renderer` 给出绘制设置：`renderMode`、`sortMode`、`sortingOrder`、`minParticleSize`、`maxParticleSize`、`lengthScale`、`velocityScale`、`cameraVelocityScale`、`pivot`、`alignment`、`material`。

`material` 有三种状态，不可混为一谈：为 `null` 时该渲染器未指定材质，用引擎默认材质绘制；为 `{external: true, fileId, archive}` 时材质位于 `dependencies` 列出的其它包里；否则是 `{name, shader, renderQueue, textures, floats, colors}`，其中 `shader` 是 `m_Shader.m_ParsedForm.m_Name`，解析不到时为 `null`；`renderQueue` 原样保留（包括 `-1`）；`textures` 把材质属性名映射到 PNG 文件名（属性名实测为 `_BaseMap` 或 `_BaseMap2DArray`），值为 `null` 表示该贴图不在本包内。

渲染状态按 `shader` 名称分族，不能把一个族的材质属性当成另一个族的 pass 状态。

- `Mysekai/Effect/UberUnlit`（粒子）：pass 的活跃混合因子来自 `_BlendSrc` / `_BlendDst`；`_SrcBlend` / `_DstBlend` 未声明，不驱动 pass。`_ColorMask=14` 只写 RGB，不写目标 alpha；`_ZWriteOverride` 虽声明但不驱动 pass。顶点 shader 在 `0.004 < abs(_ZOffset)` 时把 `_ZOffset` 作为线性 eye-depth（米）偏移；它不是 polygon offset。
- `Mysekai/Emoticon/Sprite`（sprite）：只有一个 pass，混合因子写死为 `One` / `OneMinusSrcAlpha`，背面剔除写死为 `Off`，`colMask=15` 会写目标 alpha。只有 `_ZTest` / `_ZWrite` 由材质驱动（实测 `_ZWrite=1`）；其它同名残留属性不应当被解释为 pass 状态。顶点 shader 的偏移条件是 `0.004 < _ZOffset`，没有 `abs`；因此负值不会生效。

**取值编码**：粒子的每个可变量都是带模式标签的取值，模式决定哪些字段有效——
`{"mode": "constant", "value": v}` 取 `v`；
`{"mode": "twoConstants", "min": a, "max": b}` 在 `[a, b]` 间均匀随机；
`{"mode": "curve", "multiplier": m, "keys": [...]}` 按归一化生命求值曲线后乘 `m`；
`{"mode": "twoCurves", "multiplier": m, "minKeys": [...], "maxKeys": [...]}` 在两条曲线之间按每粒子随机数插值后乘 `m`。
关键帧为 `{time, value, inSlope, outSlope}`，无穷斜率写作 `null`。

颜色同构：`{"mode": "color", "color": [r,g,b,a]}`，或 `{"mode": "gradient", "gradient": {colorKeys, alphaKeys}}`，还可能是 `twoColors`、`twoGradients`、`randomColor`。**颜色键与透明键各有独立时间轴、条数可以不同**（例如 2 个颜色键配 3 个透明键），必须分别插值再合并。时间轴已归一化到 `0..1`。

角速度类取值的单位是**弧度每秒**；`shape.angle` 与 `shape.rotation` 是角度。

## 共享动作库与索引

动作库与角色无关，可以下载一次并应用到共享同一人形骨架契约的成员。动作索引记录包含精确的 `name`、正数 `rate` 和 `frames`；帧包含 `t`、`muscles`、`body_q`、`body_p`、`transform_rotations`。肌肉键为 0..94 的整数，值不要截断；`body_q` 是归一化 xyzw。运行时 Lua 名称与 clip 文件名是不同命名空间。

将动作库应用到其他人形模型时，参阅[动作重定向契约](retarget.md)。

## 使用者检查表

1. 蒙皮、布料和动画绑定使用 glTF 索引，不使用名称。
2. 坐标反射和三角形绕序变换各应用一次。
3. 面部图集使用下限和四列公式，并只使用偏移。
4. 区分默认眼睛/嘴巴字段与闭合模式字段。
5. 表情状态与共享动作片段分开驱动；两者的配对来自 `alone-actions.json`，不要从动作名猜表情。
6. 表演场景互斥，逐场景播放；循环尾段每轮都跑。
7. 缺失 clip、模型和左右元数据都视为明确缺失，不猜测回退。
