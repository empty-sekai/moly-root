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

当前内容规模为 53 个件、74 张贴图、85 个粒子发射器、82 条 `unsupported`。

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

`unsupported` 的当前构成为：非 Transform 绑定 63 条、`CanvasRenderer` 18 条、`Texture2DArray` 1 条。所有未建模内容都逐条保留。

<a id="particles"></a>

### `particles[]`

每个会发射的节点一项，`{node, system, renderer}`。`node` 是该节点的 `path`。

`system` 给出发射参数：`duration`、`looping`、`prewarm`、`playOnAwake`、`simulationSpeed`、`simulationSpace`、`randomSeed`、`maxParticles`、`start`，以及仅在对应模块启用时出现的 `emission`、`shape`、`sizeOverLifetime`、`colorOverLifetime`、`rotationOverLifetime`、`velocityOverLifetime`、`limitVelocity`、`textureSheet`、`customData`、`subEmitters`、`noise`、`forceOverLifetime`、`collision`、`trails`。模块启用但本导出器未建模时，该模块名进 `unsupported`，原因为 `particle module not modelled`。

后六项的形状与**消费者必须照着做的四件事**见下面 [随生命变化之外的模块](#modules) 一节。

`renderer` 给出绘制设置：`renderMode`、`sortMode`、`sortingOrder`、`minParticleSize`、`maxParticleSize`、`lengthScale`、`velocityScale`、`cameraVelocityScale`、`pivot`、`alignment`、`material`，以及仅在渲染器有第二个材质槽时出现的 `trailMaterial`。

`material` 有三种状态，不可混为一谈：为 `null` 时该渲染器未指定材质，用引擎默认材质绘制；为 `{external: true, fileId, archive}` 时材质位于 `dependencies` 列出的其它包里；否则是 `{name, shader, renderQueue, textures, floats, colors}`，其中 `shader` 是 `m_Shader.m_ParsedForm.m_Name`，解析不到时为 `null`；`renderQueue` 原样保留（包括 `-1`）；`textures` 把材质属性名映射到 PNG 文件名（属性名实测为 `_BaseMap` 或 `_BaseMap2DArray`），值为 `null` 表示该贴图不在本包内。

渲染状态按 `shader` 名称分族，不能把一个族的材质属性当成另一个族的 pass 状态。

- `Mysekai/Effect/UberUnlit`（粒子）：pass 的活跃混合因子来自 `_BlendSrc` / `_BlendDst`；`_SrcBlend` / `_DstBlend` 未声明，不驱动 pass。`_ColorMask=14` 只写 RGB，不写目标 alpha；`_ZWriteOverride` 虽声明但不驱动 pass。顶点 shader 在 `0.004 < abs(_ZOffset)` 时把 `_ZOffset` 作为线性 eye-depth（米）偏移；它不是 polygon offset。
- `Mysekai/Emoticon/Sprite`（sprite）：只有一个 pass，混合因子写死为 `One` / `OneMinusSrcAlpha`，背面剔除写死为 `Off`，`colMask=15` 会写目标 alpha。只有 `_ZTest` / `_ZWrite` 由材质驱动（实测 `_ZWrite=1`）；其它同名残留属性不应当被解释为 pass 状态。顶点 shader 的偏移条件是 `0.004 < _ZOffset`，没有 `abs`；因此负值不会生效。

**取值编码**：粒子的每个可变量都是带模式标签的取值，模式决定哪些字段有效——
`{"mode": "constant", "value": v}` 取 `v`；
`{"mode": "twoConstants", "min": a, "max": b}` 在 `[a, b]` 间均匀随机；
`{"mode": "curve", "multiplier": m, "keys": [...]}` 按归一化生命求值曲线后乘 `m`；
`{"mode": "twoCurves", "multiplier": m, "minKeys": [...], "maxKeys": [...]}` 在两条曲线之间按每粒子随机数插值后乘 `m`。
关键帧为 `{time, value, inSlope, outSlope}`，无穷斜率写作 `null`——它表示阶梯键，不是 JSON 读得回来的数。

**非有限数写成名字**。JSON 没有无穷与非数的写法，而这些参数里确实有：永不按寿命消亡的粒子、没有下一轮的爆发、不设上限的材质参数。因此除上面那个阶梯键之外，凡非有限数一律写成字符串 `"Infinity"`、`"-Infinity"`、`"NaN"`——原样保留、`JSON.parse` 读得进、按数字串取回即得原值（JS 里 `Number("Infinity")` 就是无穷）。**不要把它当成缺值**：缺值是 `null`，而 `null` 在本契约里另有含义。裸写 `Infinity` 的文档整份都读不进去，所以本仓的写盘一律拒绝裸写。

一个已知实例：`start.lifetime` 为 `{"mode": "constant", "value": "Infinity"}` 的发射器（当前内容里 24 个）表示粒子不按寿命消亡；其中 17 个同时把 `emission.bursts[].repeatInterval` 写成 `"Infinity"`，配 `cycleCount = 0`，即**发一轮就再也不发**——`cycleCount = 0` 本身只说轮数无限，下一轮什么时候来由 `repeatInterval` 决定，两者要一起读。当前内容里也有 `cycleCount = 0` 配有限间隔的爆发（头顶件里 13 个，间隔 0.05–0.5 秒），那些是真的一直重发。

颜色同构：`{"mode": "color", "color": [r,g,b,a]}`，或 `{"mode": "gradient", "gradient": {colorKeys, alphaKeys}}`，还可能是 `twoColors`、`twoGradients`、`randomColor`。**颜色键与透明键各有独立时间轴、条数可以不同**（例如 2 个颜色键配 3 个透明键），必须分别插值再合并。时间轴已归一化到 `0..1`。

角速度类取值的单位是**弧度每秒**；`shape.angle` 与 `shape.rotation` 是角度。

<a id="modules"></a>

### 随生命变化之外的模块

除了随生命变化的大小/颜色/旋转/速度，发射器还可能带六个模块。它们全部复用上面那套模式标签的取值编码，**没有新的取值原语**。

| 键 | 何时出现 | 内容 |
|---|---|---|
| `customData` | 自定义数据模块启用 | `{custom1, custom2}`，每项 `{mode, componentCount, components[4], color}`。`mode` 为 `disabled` / `vector` / `color`。 |
| `subEmitters` | 子发射器模块启用 | 数组，每项 `{emitter, type, properties, inherit, emitProbability}`。 |
| `noise` | 噪声模块启用 | `{separateAxes, strength/strengthY/strengthZ, frequency, damping, octaves, octaveMultiplier, octaveScale, quality, dimensions, scrollSpeed, remapEnabled, remap/remapY/remapZ, positionAmount/rotationAmount/sizeAmount}`。 |
| `forceOverLifetime` | 力模块启用 | `{x, y, z, inWorldSpace, randomizePerFrame}`。三轴是**加速度**：乘时间步长后加到速度上，不是位移、也不是冲量。 |
| `collision` | 碰撞模块启用 | `{type, mode, dampen, bounce, lifetimeLoss, minKillSpeed, maxKillSpeed, radiusScale, quality, voxelSize, collidesWith, collidesWithDynamic, interiorCollisions, maxCollisionShapes, collisionMessages, colliderForce, multiplyColliderForceBy*, planeSlots, planes}`。`type` 为 `planes` / `world`；`mode` 为 `3d` / `2d`；`collidesWith` 是层掩码位；`lifetimeLoss` 是每次碰撞扣掉的**寿命比例**（`1.0` = 当场消亡），不是秒数。 |
| `trails` | 轨迹模块启用 | `{mode, ratio, lifetime, minVertexDistance, textureMode, textureScale, ribbonCount, shadowBias, worldSpace, dieWithParticles, sizeAffectsWidth, sizeAffectsLifetime, inheritParticleColor, generateLightingData, splitSubEmitterRibbons, attachRibbonsToTransform, colorOverLifetime, widthOverTrail, colorOverTrail}`。`mode` 为 `perParticle` / `ribbon`；`textureMode` 为 `stretch` / `tile` / `distributePerSegment` / `repeatPerSegment` / `static`。 |

`noise.quality` 是**维度数**，不是画质档：随附的 `dimensions` 把它写明白——`3` 采三维场，`1` 与 `2` 采二维场。`damping` 为真表示强度按频率缩放（即除以 `frequency`），频率升高时视觉幅度不变。噪声作用在**速度**上，与力同一个通道。

`subEmitters[].emitter` 是子发射器所在的**节点路径**，`null` 表示这一项没指任何发射器、因而什么也不发（不要当成「发自己」）。`inherit` 是 `{color, size, rotation, lifetime, duration}` 五个布尔，从 `properties` 的位逐位取出；`properties` 同时原样保留，因为内容里存在把「全继承」写成全 1 位而不是枚举值 31 的情况，两种写法位测试的结果相同。指针指到本包外时该项 `emitter` 为 `null`，并在 `unsupported` 里登记，原因为 `sub-emitter is not in this package`——**空指针与解析不到是两回事**，前者不进 `unsupported`。

#### 四件必须照着做的事

1. **自定义数据要逐帧求值。** `customData` 的分量常常是曲线模式（当前内容里 2068 个活分量中 958 个是 `curve` 或 `twoCurves`；347 个发射器里 **305 个必须逐帧求值**，只有 42 个是出生定死的）。把它当成出生时定死的随机值，值仍然落在合理区间，于是**不报错、只是停止动画**——由它驱动贴图层号或 UV 滚动的效果会冻在第 0 帧。另外每个流的每个分量各有**独立**的每粒子随机数，x/y/z/w 不相关；同一粒子同一分量的随机数**终生不变**，不要每帧重摇。`componentCount` 说的是**求值几个**，四个分量在盘上一直都在——求值全部四个会把作者没动过的分量当数据用。
2. **碰撞平面槽是空的，别读成平面。** `planeSlots` 是序列化了几个槽，`planes` 只列真的指到节点的那些。当前内容里 25 个碰撞模块的 `planeSlots` 为 `1` 而 `planes` 为 `[]`（另 1 个两者都是 0），且 26 个全部 `type: "world"`。把那个空槽读成「有一个平面」会在原点造出一层看不见的地板，雨雪会在 `y = 0` 溅开。
3. **轨迹材质在第二个槽。** 盘上没有单独的轨迹材质字段：`material` 是粒子的，`trailMaterial` 是轨迹的，后者只在渲染器真有第二个槽时出现。当前内容里 560 个发射器中 550 个只有一个槽（无 `trailMaterial`）、10 个有两个槽（有 `trailMaterial`），与轨迹模块是否启用**完全对应、零例外**。用 `material` 画轨迹会拿到粒子头部的材质，混合方式不对且通常过亮。
4. **`subEmitters[].type` 决定它什么时候发，`birth` 之外的不从每帧列表里发。** `birth` 由发射器每帧更新触发；`death` 在粒子死亡时触发一次，`collision` 在碰撞时触发。只遍历这个列表做每帧发射的实现会让 `death` 的项**永不触发**（当前内容里 42 项），也让 `collision` 的 7 项永不触发；反过来把 `death` 当每帧发射，则 42 项会变成连续不断的流。

## `phenomena/`

天气资产包。游戏把天气叫**现象**：一个现象是一整套视听参数——天空渐变、场景光与角色着色、云影、风、雾与后处理、以及雨雪流星这类粒子效果。

一个现象跨多个包：一个 **global** 包（配置、天空渐变、后处理档案、天空与相机粒子）、一个 **common** 包（粒子用到的共享网格、材质与贴图）、以及**每站点一个**包。站点包要么带该站点自己的粒子 prefab，要么——只有那个室内站点——带**覆盖** global 的配置与后处理。所有包在**一次任务**里一起提取，因为它们共写一份 `index.json`。

目录布局：

```text
phenomena/
  index.json                             现象清单
  icons/<图标名>.png                     全部现象图标（来自单一共享缩略图包）
  models/<资产名>-<摘要>.glb             模型资产与网格发射器的几何（跨现象共享，见下）
  audio/loop.json                        音频状态与全部循环点
  audio/<档名>/<档名>.acb                音频档原样
  audio/<档名>/<cue>.wav|.ogg            解码后的波形（需外部解码器）
  <现象资产名>/
    config.json                          环境配置（摊平）
    ramp.png                             天空渐变，32x1
    postprocess.json                     后处理档案
    timeline.json                        驱动该现象的时间轴（只有闪电那一个有）
    textures/<包名>__<贴图名>.png        配置与材质引用的贴图
    textures/<包名>__<数组名>.<层号>.png 贴图数组的逐层导出
    fx/effects.json                      该现象全部粒子效果
    overrides/<站点名>/config.json       该站点覆盖的配置
    overrides/<站点名>/postprocess.json  该站点覆盖的后处理
```

`index.json` 里的一切文件路径都**相对该文件自身**。贴图文件名带包名前缀，因为贴图名在现象之间会重复；请按 `file` 字段取文件，不要自行拼接。

当前内容规模：15 个现象、29 份配置（15 全局 + 14 覆盖）、29 份后处理、15 张渐变、14 处站点覆盖、107 个粒子效果（560 个发射器）、283 张贴图、29 处贴图数组绑定（共 124 层 PNG）、10 个模型资产（去重后 20 个 glTF 几何文件，合计约 182 KB）、1 份时间轴、13 个音频包共 31 段音频（给了外部解码器时；音频包由 master 行点名，与提取了几个现象无关）、15 张图标、192 条 `omitted`、9 条 `unsupported`。全部现象环境包合计约 9.9 MB——**现象资产很轻**，不必按大体量资产去做分批与缓存。15 个现象里 13 个六个站点齐全，一个只有两个站点，投递站那个没有站点包。

### 两级查找与覆盖

一个站点的配置与后处理按**两级查找**：先看该站点自己的包，再落回该现象的 global 包。`index.json` 的 `overrides` 只列**真的带覆盖**的站点，所以某站点不在其中意味着「该站点用全局值」，**不是**「查找失败」。当前内容里 14 个现象各有一处覆盖，全部是同一个室内站点；投递站现象没有任何覆盖。

**家园站没有覆盖包**。它用全局配置，并在 `light.homeSiteLightAngle.active` 为真时以其中的 `angleXZ`/`angleY` **替换**全局太阳角——这就是该字段存在的理由，不要把它当成一份无用的重复数据。

### 交叉淡化

切换现象时，两份配置在 **0.25 秒**内交叉淡化：两张渐变同时在位、按进度混合，光色与光向逐帧插值（光向按球面插值）。截图捕获是唯一的例外，它**瞬切**，以免快门期间取到插值中的中间态。

### `config.json`

配置按源结构摊平成三个标量加五个参数组，源字段名的前导下划线不属于契约。**值一律按存储原样给出**，不重标定、不取整、不填默认。

| 字段 | 语义 |
|---|---|
| `asset` | 该配置在包内的资产文件名。 |
| `description` | 作者写在资产上的说明文字。 |
| `rendererType` | 渲染路径选择位，随现象存储。 |
| `gridColorKey` | 地格配色档名（实测取值 `0_Default` 与 `1_Dark`）。**不是死字段**，消费方据此换地格配色。 |
| `emissionType` | 自发光档位，作为整型全局量推给着色器。 |
| `light` | 11 项：见下。 |
| `cloud` | 5 项：云影贴图与其滚动。 |
| `character` | 5 项：角色描边。 |
| `fixture` | 5 项：家具描边。 |
| `wind` | 9 项：风与顶点动画。 |

`light` 的 11 项是 `characterDirectionalLightColor`、`characterShadeSkinColor`、`characterBodyShadeColor`、`phenomenaDirectionalLightColor`、`phenomenaShadeColor`、`angleXZ`、`angleY`、`homeSiteLightAngle`、`dropShadowColor1`、`dropShadowColor2`、`dropShadowEdgeSmoothness`。

> **这一组不只是「场景光」。** 现象带三份角色相关色：一份角色方向光色、一份皮肤暗部色、一份身体暗部色，**与场景自身的现象光色和现象暗部色是两路**。所以换天气会同时改角色的面部球面阴影与身体暗部，而不只是改天空。`angleXZ`/`angleY` 是太阳方位与仰角（度）；`homeSiteLightAngle` 为 `{active, angleXZ, angleY}`；两个落影色加一个落影边缘柔和度描述角色投在地面上的影子。

`cloud` 的 5 项是 `cloudShadowTexture`、`cloudShadowOpacity`、`cloudShadowTextureSize`、`cloudScrollVelocity`、`cloudScrollSpeed`。**两处不能直接照用**：采样云影贴图用的是 `cloudShadowTextureSize` 的**倒数**；实际滚动速度是 `cloudScrollVelocity` 乘 `cloudScrollSpeed`。不透明度为 0 等于关掉云影。

`character` 与 `fixture` 是**两套彼此独立**的描边设置，字段名相同（`outlineWidth`、`outlineDepthOffset`、`outlineWidthMaxRate`、`outlineWidthMinRate`、`outlineColor`）；角色那一组同时参与面部与身体着色，所以它不只是描边。

`wind` 的 9 项是 `windSpeed`、`windColor`、`vertexWaveAnimationAmount`、`vertexWaveExponent`、`vertexRandomAnimationAmount`、`vertexRandomAnimationSpeed`、`windWaveDistortionAmount`、`windWaveDistortionFrequency`、`windNoiseTexture`。顶点波动幅度为 0 等于关掉风动。

颜色是 `[r,g,b,a]`，二维向量是 `[x,y]`。贴图字段是 `{name, file}`；`file` 为 `null` 表示该图不是单张位图（例如它是贴图数组），此时缺口登记在 `summary.unsupported` 里。

**站点决定的着色参数不在这个包里。** 场景里另有 **29 个**全局着色量由**站点**而非现象决定（来自站点自身的视图与图形设置，涉及场地扩展、道路、树、掉落物、以及物件着色器等分组）；本包不含它们，所以在没有站点的场景里渲染时，消费方应给这些量喂中性常量，否则读它们的着色器会跑偏。**别把它们误并入现象**——它们与天气无关，随站点变而不随天气变。

### `postprocess.json`

后处理档案，`components[]` 按档案自身顺序给出——那也是体积栈的应用顺序。每项为 `{name, class, active, parameters}`：`name` 是资产里的组件名，`class` 是它实例化的脚本类名（两者可以不同），`active` 为假表示该组件整体不生效。

每个参数是 `{overrideState, value}`。**`overrideState` 为 `false` 表示该档案根本不设置这个参数**，从周围体积栈继承来的值仍然生效；把这个标志丢掉就把「别管它」变成了「强制成这个值」。

当前内容里每份档案都有五个组件：`MysekaiFogVolume`（9 参：开关、密度、近/远色与近/远密度、雾起止距离、雾高）、`MysekaiFlarePara`（类名 `MysekaiFlareParaVolume`，15 参：屏幕耀斑 8 + 太阳耀斑 7）、`MysekaiParticleBloomVolume`（11 参）、`MysekaiDiffusionVolume`（6 参）、`ColorAdjustments`（5 参）。另有三份额外组件只出现在个别现象上：一个 `Bloom`（11 参）、一个 `SplitToning`（3 参）与一个 `WhiteBalance`（2 参）。**组件集合按实际存在提取**，不按固定名单假定；出现新组件时它照样会被摊平，其形状读不出的参数才进 `unsupported`。

### `fx/effects.json`

粒子效果。顶层为 `version`（当前为 1）、`phenomenon`、`effects`、`summary`。

`effects[<prefab 名>]` 为 `{kind, site, variant, nodes, particles, effectors, effectiveRotation}`：

| 字段 | 语义 |
|---|---|
| `kind` | `sky` 挂天空、`camera` 挂相机、`site` 属于某个站点；`other` 表示该 prefab 不符合上面的命名式样，**它的挂法不由名字说明**。 |
| `site` | `kind` 为 `site` 时的站点名，**取自包的站点变体而非从名字猜**；否则为 `null`。 |
| `variant` | 该 prefab 来自哪个包变体（`global` / `common` / `unique__<站点>`）。 |
| `nodes` | 节点树，父节点在前。每项有 `name`、`path`、`parent`、`active`、`position`、`rotation`、`scale`。 |
| `particles` | 发射器数组，见下。 |
| `effectors` | 生命周期组件数组，见下；没有则为 `[]`。 |
| `effectiveRotation` | 该效果每帧的旋转律，只对 `kind` 为 `camera` 的效果给出，其余为 `null`。 |

`nodes[]` 的 `path` 相对该 prefab 根，根自身为 `""`；`parent` 是父节点的 `path`，根为 `null`。`position` 为 `[x,y,z]`，`rotation` 是四元数 `[x,y,z,w]`，`scale` 为 `[x,y,z]`。

`particles[]` 每个会发射的节点一项，`{node, system, renderer}`，**编码与头顶件的粒子完全同一套**——`system` 是发射参数、`renderer` 是绘制设置与材质，取值都是带模式标签的范围。详见上面 [`emoticons/` 的 `particles[]`](#particles) 一节，那里给出模式标签、关键帧与渐变的完整取值编码；此处不重复。

`effectors[]` 每项 `{node, timeUntilDestroy, rotationType}`，是挂在节点上的**生命周期**组件而非视觉效果：它播放自己以下所有发射器，停止时先停发射，然后等到子发射器全部播完**且**至少过了 `timeUntilDestroy` 秒才销毁整个对象。当前内容里 10 个实例的 `timeUntilDestroy` 均为 `2.0`、`rotationType` 均为 `normal`。**换天气时不要立刻销毁旧效果**：旧天气的粒子要在新天气已经开始发射的同时走完自己的寿命，这 2 秒交叠是照实现来的。

`effectiveRotation` 取 `normal`（随相机一起转）或 `fix`（每帧把自身旋转设为父级旋转的逆，即抵消相机旋转、保持与世界轴对齐，位置仍跟随相机）。**这条律不统一**：宿主只在 prefab 根上找生命周期组件，找到就用组件自己序列化的 `rotationType`（当前 4 个是 `normal`），找不到就自行补一个并要求 `fix`（当前 11 个）。所以 15 个相机效果里 4 个随相机转、11 个反向抵消——**全部套用抵消或全部不套用都是错的**。组件挂在子节点上不算，宿主只看根。非相机效果的挂法本仓未取证，因此写 `null` 而不猜。

当前内容里 107 个效果分为 15 个 `sky`、15 个 `camera`、75 个 `site` 与 2 个 `other`，共 560 个发射器。材质 524 个解析成功（着色器为 `Mysekai/Effect/UberUnlit` 380 个、`Particles/Standard Unlit` 144 个）、36 个为 `null`。**材质与贴图常常在别的包里**：提取时按每个包自己声明的依赖清单一并加载，只有单独加载 global 包才会退化成解析不出材质。未提供的依赖不会被猜测，指针保留为 `{external: true, fileId, archive}`。

### 贴图数组与采层

有些效果贴图不是一张图，而是一张**贴图数组**——N 张同尺寸同格式的图叠成一叠。采样它需要三个坐标 `(u, v, layer)`，而这里的 `layer` **不是常量**：材质里存着一小组标量，说明层号从粒子的哪个自定义值读、以及怎么把它换算成整数层号。

因此贴图数组**不进** `textures` 那张「一槽一文件」的映射，而进**兄弟字段** `textureArrays`：只跟着单文件映射走的消费方会去采数组的 2D 伴生槽，而那个槽是空的，采样返回 (1,1,1,1)，画出**白方块**。

`_material.textureArrays[<属性名>]` 为：

| 字段 | 语义 |
|---|---|
| `name` / `kind` | 数组资产名与类型（`Texture2DArray`）。 |
| `width` / `height` / `layers` | 单层尺寸与层数（层数即资产的 `m_Depth`）。 |
| `graphicsFormat` | **原始整数**格式码。`Texture2DArray` 的格式字段是 `GraphicsFormat` 而**不是** `TextureFormat`，两张表值域不同，喂错表会静默拿到垃圾——所以这里保留原值不做映射。当前内容里 5 张数组全为 `134`。 |
| `colorSpace` / `mipCount` | 色彩空间与 mip 数，按原样。 |
| `files` | **逐层文件，按层序**，第 i 项就是第 i 层。文件名形如 `<包名>__<数组名>.<层号>.png`。 |
| `sampling` | 采层参数，见下。 |

`sampling` 为：

| 字段 | 语义 |
|---|---|
| `mode` | 该槽的模式标量。`1.0` 是数组档，其余是单图档。 |
| `arrayMode` | `mode` 是否为数组档，即**这张数组到底会不会被采**。 |
| `keyword` / `keywordEnabled` | 与该模式对应的着色器关键字名，以及它在材质的关键字集合里是否开着。给出它是为了让消费方能**双向核对**模式标量与着色器变体；当前内容里两者一致，无例外。 |
| `sliceCount` | 参与换算的层数。**它与 `layers` 是两个独立的数，且实测会不相等**（有 8 层的数组把它写成 4）——所以算式用 `sliceCount`，结果再夹到真实存在的层。 |
| `progress` | 加在层进度上的常量偏移。 |
| `progressCoord` | **打包的选择子**，形如 `分量 * 10 + 向量`：向量部分选读哪个粒子自定义值（`0` 选零向量，意即这一槽根本不由粒子驱动），分量部分选它的 x/y/z/w。 |
| `progressSource` | 上面那个选择子的解码结果 `{vector, component, constant}`。`constant` 为真表示选到了零向量。它指的那个粒子自定义值就是发射器的 [`customData`](#modules)：`vector` 选 `custom1` / `custom2`，`component` 选该流 `components` 里的第几项——**那一项常常是曲线模式，必须逐帧求值**，否则层号永远停在第 0 帧算出的那个。 |
| `offsetXCoord` / `offsetYCoord` | 同一套编码的 UV 滚动选择子，按原样带出。 |
| `progressClamp` | 换算里那个夹取常量，按原样（`0.999000013`）。 |
| `layerFormula` | 层号算式，见下。给出它是为了让「导出的参数」只有一种解释。 |

层号算式（`progressSource` 为选择子指到的那个粒子值）：

```text
layer = min(layers - 1, max(0, floor(fract(clamp(progressSource + progress, 0, 0.999000013)) * sliceCount)))
```

**这里的 `floor` 不能写成四舍五入。** 着色器算出的层坐标自带一个 `-0.5`，正是为了抵消图形 API 采数组时的「就近取整」，把它变成**精确 floor**——这是刻意做整数翻页索引的签名，不是层间混色。写成四舍五入会让每个边界移动八分之一个区间，于是**四分之一的粒子取错层**，而平均看上去毫无异常。同理，漏掉 `0.999000013` 的夹取会让取到 1.0 的值经 `fract` 回绕到**层 0**。

**不存在逐帧翻页动画，也不存在跨层混色**：模式标量只有单图与数组两档，顶点流里没有任何帧号/混合量，且每个着色器子程序对数组只有一次采样。

当前内容里 5 张数组被 **80 处**槽位绑定（`_BaseMap2DArray` 74、`_EmissionMap2DArray` 3、`_AlphaTransitionMap2DArray` 3），其中**只有 29 处真的在数组档**，其余 51 处绑着数组而槽位停在单图档——**那些绑定是惰的**，把它们当成会被采的贴图去画就是错的。`index.json` 里 29 处贴图数组绑定共导出 124 张层 PNG（同一张数组被多个现象引用时，每个现象各自导出一份到自己的 `textures/` 下）。

### `models/`：模型资产与网格发射器的几何

`common` 包里还有**模型资产**：一小棵节点树，若干节点上挂着网格——天空穹顶、云环、雨圈、彩虹扇面、银河面片这类。它们导出为 **glTF 二进制**（`.glb`），**连节点变换一起**，所以消费方画的是作者定的形状，而不是它的近似。

另有一类几何同样不可省：`renderMode` 为 `Mesh` 的发射器把每个粒子画成**一份网格的副本**，没有那份网格就什么也画不出来；`shape.type` 为 `Mesh` 的发射器则把粒子**生在一份网格的表面上**。这两类网格都按单网格文件导出，并分别在 `renderer.meshes[]` 与 `shape.meshes[]` 里按 `{file, node}` 引用。

**几何文件按内容命名**（`<资产名>-<内容摘要前8位>.glb`）：包与包之间共享网格，同一个包里也会重复，所以同样的几何**只写一份**，第二个引用只是多一个指针；而两份不同的几何即使同名也各自成文件。共享文件的条目里**不放任何随现象而变的东西**，所以一个共享文件对每个指向它的现象含义相同。

`index.json` 顶层 `models[]` 是去重后的几何文件清单，每项 `{name, nodes, vertices, triangles, meshes[], materials[], file, sha256, bytes}`，外加**只在几何来自引擎自带资源时才出现**的 `source`（见下面「引擎自带图元」）。每个现象的 `models[]` 是它引用的模型资产，同样的字段外加 `asset`（容器里的资产名）与 `variant`（来自哪个包变体）。`meshes[]` 每项 `{node, mesh, vertices, triangles}`；`materials[]` 每项 `{node, material}` **只给材质名**——模型资产自带的材质是导入时的默认材质（当前内容里 19 个网格渲染器全部是它），现象真正用来画天空的材质与属性**不在模型资产里**，天空是靠一个材质属性块在运行时喂两张渐变的。

当前内容里 10 个模型资产（`001_sunny` / `006_rain` / `007_rainnight` / `009_meteorshower` / `014_sekai` 三个 / `017_rainbow` 两个 / `999_festivalgarden`）、90 个网格发射器与 29 个网格形状发射器合起来去重成 **29 个 glTF 文件**，合计约 211 KB。

#### 引擎自带图元

90 个网格发射器里有 **8 个**画的不是游戏资产，而是**引擎自己内置的图元**：容器名 `unity default resources`，`pathId` 10209 是 Plane、10207 是 Sphere（`008_thunder` 的闪电按站点摊开共 7 个，`014_sekai` 的云 1 个）。**没有任何资产包分发那个容器**，所以默认导出解析不到它们：这 8 条逐条进 `unsupported`，`mesh` 字段带上 `{fileId, pathId, archive}`，发射器的 `renderer.meshes` 是空数组——**整条发射器一颗粒子也画不出来**，打雷天看不见闪电就是这么来的。

那份容器是**引擎自带**的、不是游戏资产，所以本仓不分发它，也**不按记忆生成几何去顶替**。但持有相同版本引擎的调用方可以把它交进来：`phenomena` 子命令的 `--builtin-resources <路径>` 收下**容器文件本身，或装着它的目录**（可重复；给目录时只取引擎自己的那几个容器，不会把目录里别的文件卷进来）。给了之后这些指针就像别的网格指针一样解析，几何按**同一套命名与目录约定**写进 `models/`，`renderer.meshes` 与 `shape.meshes` 给出正常的 `{file, node}` 条目。当前内容里给了它，那 8 条 `unsupported` 全部消失，`models/` 多出 `Plane`（121 顶点 / 200 三角）与 `Sphere`（515 顶点 / 768 三角）两个文件，共 31 个文件、约 248 KB。

**`source` 字段**：来自引擎容器的几何，`index.json` 顶层 `models[]` 的条目多一个 `"source": "engineBuiltin"`；模型资产内部 `meshes[]` 的条目同理。**其它任何几何都没有这个字段**，所以「没有 `source`」恒等于「由这个游戏的资产包分发」。之所以要标：这两份形状写在同一个目录、按同一套规则命名，消费方画法也完全一样，不标就无从分辨引擎的形状与作者的形状。

**不给 `--builtin-resources` 时的产物与从前逐字节相同。** 这是契约的一部分：新增的输入不许移动任何既有消费方已经在读的东西。

另有 3 个 `shape.type` 为 `Mesh` 的发射器，`shape.meshes` 无论给不给容器都是空数组——它们的网格指针本身就是**空指针**（`mesh` 字段为 `{fileId: 0, pathId: 0, archive: null}`），作者没给网格，不是这里解析不到。它们进 `unsupported` 时沿用的还是同一句 `reason`，判断以 `mesh` 里的指针为准。

坐标与绕序按本仓统一约定处理（X 轴反射一次、三角形绕序翻一次、UV 的 V 翻一次），与角色 `.glb` 同一套，见开头的[坐标系](#坐标系与身份)一节。

### `timeline.json`：唯一一个由时间轴驱动的现象

闪电那个现象不是一组常量：天空闪、光跟着闪，两者都按作者定的曲线衰减。这份时程住在一个**时间轴资产**里——若干轨道，每条轨道上若干剪辑，共用一根时间轴。**110 个包里只有它一个带这种资产。**

顶层为 `{asset, name, duration, durationMode, frameRate, tracks[], summary}`。`duration` 是固定时长（秒），`frameRate` 是编辑时的帧率（当前内容为 60），`summary` 为 `{tracks, clips}`。

`tracks[]` 每项：

| 字段 | 语义 |
|---|---|
| `name` | 轨道在编辑器里的标签，**给人看的**。 |
| `class` | 轨道类名。 |
| `target` | **这条轨道驱动哪个值**，取自轨道上的一个枚举字段而不是从标签猜：颜色轨为 `none`/`skyAdditiveColor`/`lightAdditiveColor`，数值轨为 `none`/`skyAdditiveIntensity`/`lightAdditiveIntensity`。`targetValue` 是该枚举的原始整数。 |
| `role` | `null` 是资产的主轨道列表里的轨道，`markerTrack` 是标记轨，`child of <名字>` 是某条轨道的子轨道。 |
| `scale` | 数值轨自带的缩放系数（只有带这个字段的轨道有）。 |
| `muted` / `locked` | 轨道状态，按原样。 |
| `clips[]` | 剪辑数组，按 `start` 排序。 |

`clips[]` 每项 `{start, duration, clipIn, timeScale, label, class, blendInDuration, blendOutDuration, easeInDuration, easeOutDuration, preExtrapolation, postExtrapolation, asset}`。**时间有两种形式且不可互换**：`start` 与 `duration` 是时间轴上的**秒**，而剪辑自己的曲线跑在 **0..1 归一轴**上、铺满这个 `duration`，再受 `timeScale` 与 `clipIn` 影响。

`asset` 是剪辑自己的内容，按剪辑类分三种：颜色剪辑给 `{gradient}`（渐变编码与粒子的渐变**完全同一套**，见 [`emoticons/` 的 `particles[]`](#particles)）；数值剪辑给 `{scale, curve}`；噪声剪辑给 `{intensity, frequency, intensityCurve}`。曲线为 `{keys[], preInfinity, postInfinity}`，`keys[]` 每项 `{time, value, inSlope, outSlope, weightedMode, inWeight, outWeight}`；**无穷斜率表示阶梯键，写成 `null`**——它不是 JSON 读得回来的数。剪辑类读不出时 `asset` 为 `null`，并在 `unsupported` 里登记类名，**剪辑在时间轴上的位置照样保留**。

当前内容里这份时间轴为 23.333333 秒、7 条轨道（4 条主轨 + 2 条子噪声轨 + 1 条标记轨）、25 个剪辑。

### `index.json`

现象清单。顶层为 `version`（当前为 1）、`semantics`、`phenomena`、`icons`、`refreshTimePeriods`、`siteBgms`、`siteSoundFallbacks`、`ambiencePackage`、`summary`。

`phenomena[<现象资产名>]` 包含：

| 字段 | 语义 |
|---|---|
| `assetName` | 现象资产名（即目录名）。 |
| `id` | 现象 id；**只在给了 master 时有值**，否则为 `null`。 |
| `variants` | 本次提取到的包变体名。 |
| `config` / `ramp` / `postprocess` | 三份产物的相对路径；`ramp` 为 `{file, width, height}`。 |
| `overrides` | `{<站点名>: {config, postprocess}}`，只含真的带覆盖的站点。 |
| `fx` | `{sky, camera, site, other, emitters, file}` 计数与效果文件路径。 |
| `icon` | 图标文件的相对路径；图标归属写在 master 行里，所以**没给 master 时为 `null`**（图标本身照样导出，只是不与现象挂钩）。 |
| `master` | master 行，见下；没给 master 或该包没有对应行时为 `null`。 |
| `models` | 该现象引用的模型资产（几何在共享的 `models/` 里，见上）；没有则为空数组。 |
| `timeline` | 该现象的时间轴摘要 `{file, duration, tracks, clips}`；没有时间轴时为 `null`。 |
| `audio` | 该现象**实际解码出的**音频流，每项在 `loop.json` 的流字段之外再带 `package`。**三态**：数组非空是有音频，**空数组是「行是知道的，但这个现象没有音频行」**（它保留站点自己的音乐与环境音），`null` 是**行本身不知道**（没给 master）。混同后两者会让消费方分不清「没有」与「不知道」。 |
| `bgms` | 该现象的音乐行（每项带 `package`）；没给 master 时为 `null`。 |
| `siteSounds` | 该现象的环境音行（每项带 `package`）；没给 master 时为 `null`。 |
| `note` | 说明为何没有 master 行（仅在需要说明时出现）。 |

**master 表由使用者自备**（`--master <目录>`）。现象的名字、时段、亮度、音乐与环境音都只写在 master 里，所以没给这个输入时这些字段一律缺省，并在 `summary.missing.master` 写明原因，**不填默认值**。读取的表是 `mysekaiPhenomenas`、`mysekaiPhenomenaBgms`、`mysekaiSiteBgms`、`mysekaiSiteMysekaiPhenomenaSounds`、`mysekaiRefreshTimePeriods` 与 `clientConfigs`；缺哪张表登记在 `summary.missing.masterTables`。

`master` 为 `{id, name, englishName, description, timePeriodType, brightnessType, backgroundColorId, iconAssetbundleName, rampTextureAssetbundleName}`。**时段与亮度是行属性，不是时钟状态**：`timePeriodType` 取 `daytime`/`evening`/`night`，`brightnessType` 取 `none`/`normal`/`bright`/`dark`；它们随「哪一行现象生效」一起变，本地时钟不推进现象。

`refreshTimePeriods` 是刷新窗口，每项 `{id, startHour, endHour}`。当前内容为两窗：5 时到 17 时、17 时到 **29** 时。**29 大于 24 是刻意的**：判断落在哪个窗口时，先把 0 到 5 时之间的时刻加 24 小时再比较，所以一天从 5 时算到次日 5 时，两窗意味着每天两个现象。

**有一个现象没有 master 行**：投递站用的现象由 `clientConfigs` 的两行指定（一行给 id、一行给资产名），`note` 会说明这件事。它的 `master` 为 `null`，`id` 取自那两行。

**音乐与环境音的匹配律相反，不要写成同一个**：

- 音乐按现象**精确匹配、无回退**——没有对应行的现象保留站点自己的音乐。
- 环境音**先取「本现象 × 本站点」的行，取不到落回该站点的兜底行**；顶层 `siteSoundFallbacks` 就是那批兜底行。当前内容里只有四个采集站有环境音行。

**音乐还是两层的，`bgms` 只是上面那层。** 底层按「站点 × 亮度」查，写在顶层 `siteBgms`，每项 `{id, siteId, brightnessType, cue, assetbundleName, package}`；现象自己那行（`bgms`）**整条盖掉**底层。当前内容里 `siteBgms` 有 28 行、7 个包，而只有 6 个现象有自己的音乐行——**其余现象听到的就是底层**，所以只提上层等于漏掉大多数情况下真正在放的音乐。两层的包都会提取；底层的流属于站点而不属于某个现象，所以**不出现在任何现象的 `audio` 里**。表缺席时 `siteBgms` 为 `null`（不是空数组）：与 `siteSoundFallbacks` 同一套三态口径，「没有」与「不知道」不能同值。

**两者的寻址方式也不同。** 音乐行自带包名（`assetbundleName`），而环境音行**只有 cue 名**——全部环境音 cue 住在**同一个共享音频包**里，所以 cue 名不是包名。为免误读，`bgms[]` 与 `siteSounds[]` 每项都带 `package` 字段直接给出该 cue 所在的包，共享包名另在顶层 `ambiencePackage` 给出一次。**把环境音 cue 当包名去找，会找到一个不存在的包，并把本来在手的音频误报成缺失。**

### 音频

音频包里**没有音频文件**，只有一个中间件容器格式的**音频档**（`.acb`），档内每段波形挂在一个 **cue** 名下。取出这个档不需要任何外部程序，所以它**总是**被写到 `audio/<档名>/<档名>.acb`；**解码**它才需要一个本仓既不内置也不分发的外部解码器（`vgmstream-cli`，`--vgmstream <路径或目录>`；不给就在 `PATH` 上找）。另有一个可选的 `--ffmpeg` 只用来附带写一份压缩副本（`.ogg`）。

因此音频的缺失语义是**三态而非两态**：

| `audio.status` | 含义 |
|---|---|
| `succeeded` | 找到解码器，波形与循环点已写出。 |
| `skipped` | **没找到解码器**。音频档已在盘上，`audio.error` 写明缺什么；这不是失败，整次提取照常完成。 |
| （`audio` 为 `null`） | 没有任何 cue 行可用（没给 master），`summary.missing.audio` 说明原因。 |

`audio/loop.json` 是顶层音频文档，与 `index.json` 的 `audio` 字段同一份内容：`{status, decoder, decoderPresent, transcoder, transcoderPresent, packages[]}`。`decoder` 与 `transcoder` 只给工具名，**不给它们在本机的路径**——这份文档与音频一起分发，写入路径等于把一台机器的目录结构带出去。`packages[]` 每项为 `{package, archive, archiveBytes, status, streams[]}`。

`streams[]` 每项为一段波形：

| 字段 | 语义 |
|---|---|
| `cue` | cue 名，即 master 行里用来点播这段声音的名字。 |
| `subsong` | 该波形在档内的编号；**一个 cue 可以有多段波形**（游戏在其中随机挑），此时文件名带上这个编号；单段时为 `null`。 |
| `wav` / `ogg` | 波形文件的相对路径；`ogg` 只在给了转码器时存在。 |
| `loop` | 该段是否带循环区间。 |
| `loopStartSeconds` / `loopEndSeconds` | 循环区间，**秒**。`loop` 为假时两者均为 `null`——**不从样点数反推、不用整段长度冒充**。 |
| `loopStartSamples` / `loopEndSamples` | 同一区间的原始样点数，按 `sampleRate` 换算即得上面两项。 |
| `sampleRate` / `channels` / `samples` / `durationSeconds` / `encoding` | 流的形状，取自档的元数据。 |

**循环点来自档自身的元数据，不是从样点里探测的。** 波形导出时**不展开循环**（整段只写一遍），所以消费方要自己在 `[loopStartSeconds, loopEndSeconds)` 上循环——WebAudio 的 `loopStart`/`loopEnd` 正是这个形状。

**一个 cue 名对应几段波形不是固定的**，反过来一段波形也可以同时挂着几个 cue 名。当前内容里现象相关的四个 cue 中有一个（流星那个）带 **6 段**波形。master 行点名而档里没有的 cue 会逐条登记进 `unsupported`，原因为「档里没有波形挂这个 cue 名」——**这是数据缺口，不是范围边界**。

只有 master 行点名的 cue 会被解码：音乐按现象的音乐行、环境音按该现象的环境音行与兜底行。同一个共享环境音包里还有大量其他 cue（站点环境音、UI 音等），它们不属于现象，因此不导出。

`summary` 给出 `phenomena`、`configs`、`profiles`、`ramps`、`overrides`、`effects`、`emitters`、`images`、`textureArrays`、`arrayLayers`、`models`、`meshes`、`timelines`、`audioStreams`、`icons`、`omitted`、`unsupported` 计数，加上 `missing`（缺口 → 原因）、`omitted`（读过并刻意不导出的组件）与 `unsupported`（逐条带 `phenomenon`）。计数口径是**盘上实有的文件数**：`images` 只数真的写出的 PNG，写不出的图算缺口而不算产出；`meshes` 数的是去重后的几何文件数，`models` 数的是模型资产的引用数，两者不相等是正常的。

当前 9 条 `unsupported` 的构成为：网格发射器指向引擎内置几何 8；模型资产内的动画片段 1。**未建模内容一律逐条保留，不静默丢弃。**

### `omitted`：读过并刻意不导出

`unsupported` 与 `omitted` 是**两回事**：前者是「本该有而没有」，后者是「读过、判过、故意不出」。混在一起会把真缺口淹掉——当前 192 条 `omitted` 全部来自效果 prefab 的节点，构成为 `CanvasRenderer` 122、`MeshFilter` 35、`MeshCollider` 35。

| 组件 | 为什么不导出 |
|---|---|
| `CanvasRenderer` | 它只画**图形组件**交给它的东西，而这些 prefab 里**没有任何图形组件**（那些节点上只有粒子系统或一个非图形脚本），所以它一件东西也不画。 |
| `MeshFilter` | 这 35 处所在的节点**没有网格渲染器**，所以不可见；它在这里的作用只是给同节点的碰撞体指网格。 |
| `MeshCollider` | 碰撞面而非几何：同上不可见，且它指的网格是**站点的导航面**、住在另一个包里（`mesh.fileId` 非 0）。与它相互作用的是粒子的碰撞模块。 |

每条 `omitted` 带 `{phenomenon, effect, node, component, reason}`，指网格的还带 `mesh: {fileId, pathId}`。**这个判断是窄的**：同一节点上一旦有网格渲染器，`MeshFilter` 就不再是 `omitted`，而是按「prefab 组件未建模」进 `unsupported`——可见几何不允许从这条规则里漏掉。

### 后处理里没有查表贴图（LUT）

现象的颜色分级是**参数式的**（`ColorAdjustments` / `SplitToning` / `WhiteBalance`），**不走查表贴图**。这条是查过的，不是没看：29 份后处理档案里没有任何查表组件（组件集合只有 `MysekaiFogVolume`、`MysekaiFlareParaVolume`、`MysekaiParticleBloomVolume`、`MysekaiDiffusionVolume`、`ColorAdjustments`，另有个别现象上的 `Bloom` / `SplitToning` / `WhiteBalance`），29 份档案里唯一的贴图型参数是 `dirtTexture`，而它**全部为空指针**；这些包所在的整个语料里也**没有任何 3D 贴图**。`summary.missing.lut` 恒有一条说明这件事——**缺席必须被写出来，否则消费方分不清「没有」与「漏了」**。

## `site/`

站点资产包。九个站点共用**一个 Unity 世界坐标系**，一站一个偏移；八个场景包之外，同一路径下还有室内套件、房间皮肤、场地物件、移动大炮与世界地图，共 **109** 个包，一个都不落在契约外。

**消费方须知（这一条决定产物能不能用）**：**每个 glb 都保持自己包的原点，站点的世界偏移只在放置表 `sites.json` 里，任何几何里都没有。** 九行站点里有三行（1F/2F/3F）用的是**同一个**场景包，只靠竖直偏移分开（0 / 500 / 1000），把偏移烤进网格会让这三层塌成一层，2F 与 3F **不可挽回地丢失**。落位由消费方施加：`world = sitePosition + grid × tileSize`，`tileSize = 0.25`。

目录布局：

```text
site/
  index.json                    域索引：语义、常量、场景表、室内拼装、各族清单、汇总
  sites.json                    放置表：九行站点 + 世界坐标 + 常量 + 等级与网格范围 + 脚步声表
  packages.json                 109 个包的普查：分类、对象清点、逐对象记账、产物计数
  scenes/<站点名>/
    <站点名>.json               该包全部数据：槽位、碰撞、导航、材质、组件、环境预设
    <站点名>.glb                该包几何，**一个 prefab 根一个 glTF scene**，默认 scene 是游戏摆的那个
    collision/<面名>-<摘要>.glb 碰撞面，一面一个文件
    navmesh/navmesh.bin         出货烘焙的瓦片原样字节
    navmesh/heightmesh-N.glb    烘焙里的高度网格（可行走面几何）
    textures/<贴图名>-<id>.png  该包贴图
  indoor/kit/                   室内套件（墙地网格与材质）
  indoor/modules/lv_NN/         每个扩建档的地板与墙 prefab
  indoor/navigation/            每个扩建档的可行走面
  skins/<皮肤名>/               23 套房间皮肤
  props/<物件名>/               61 个场地物件 + 1 套共享材质
  preview/ travel/ sitemap/ shell/   预览台、移动大炮、世界地图、站点系统外壳
```

`index.json` 里的一切文件路径都**相对该文件自身**；包内文档里的路径相对该文档所在目录。

当前内容规模：109 个包（场景 8、物件 61、皮肤 23、扩建档 5、地图 4、外壳 2、预览 2、套件 1、可行走面 1、共享材质 1、大炮 1）、183 个 glb、1809 个 glTF 网格、573304 顶点 / 440873 三角形、87 张碰撞面、5 份出货导航网格（78 块瓦片、1 份高度网格）、1256 份材质、1383 张贴图、1596 个粒子发射器、161 段动画曲线。**逐对象记账**：63459 个对象 = 63042 导出 + 417 按理由跳过 + 0 无法处理，109 个包全部 `accountedFor: true`。

### 坐标契约

| 量 | 值 | 出处 |
|---|---|---|
| `sitePosition` | master 三个 `int` **平凡转 float**，无缩放 | 反汇编读出：那条 `scvtf` 是两操作数向量形，没有 fbits 字段 |
| `tileSize` / `tileScale` | 0.25 | 二进制 rodata 直读 |
| `playerHeight` | 1.0 | 同上；与五份出货导航网格的 `agentHeight` **五次独立吻合** |
| `navmeshDataAreaHeight` | 2.5 | 同上；房间布局的高度恰是 10 格 × 0.25 = 2.5 |
| `fixtureTouchSizeY` | 0.125 | 同上 |
| 格坐标范围 | 每轴 -128..127，半程 31.75 单位 | 网格坐标是四个有符号字节打进一个 int32 |
| 竖直步长 | 500 | **不是层高**：一间屋子在这套尺度里高约 2.5 单位，500 是「两份同包实例互相看不见」的隔离步长；几何上没有楼板 |

九行里 X 与 Z 全是 200 的整数倍，水平最近的两站相距 200，扣掉两侧半程还剩 136.5，**两个站点不可能在水平面上重叠**。

**接 master 时按名字接**：`sites.json` 每行带 `siteType`（`home_site` / `first_floor` / … / `festival_garden`）与 `controller`。本快照里 `id = 枚举序号 + 1` 逐行成立，但那是**吻合不是契约**——按 +1 接一旦表里插行就整体错位。

### 场景 glb：一个包，一个文件，多个 scene

一个场景包**不止一个 prefab 根**：除了游戏摆的那个站点 prefab，同包里还有它拼装用的模型资产、按等级分的装饰组（家园站的 `rank1..rank5`），以及室内两个包各自的天空。所以一个包出**一个** glb，**每个根一个 glTF scene**，网格与材质在 scene 之间共享，`defaultScene` 是游戏摆的那个。「一个站点一个文件、只留站点 prefab」会丢掉按等级换的装饰与室内天空。

| 站点 | 顶点 | 三角形 | prefab 根 | 槽位 | 碰撞面 | 材质 |
|---|---|---|---|---|---|---|
| `grasslands` | 209917 | 149205 | 79 | 7 | 3 | 87 |
| `memorialplace` | 140348 | 97268 | 65 | 7 | 3 | 72 |
| `beach` | 57672 | 52325 | 82 | 6 | 3 | 61 |
| `festivalgarden` | 46722 | 36733 | 65 | 7 | 2 | 92 |
| `home` | 26762 | 19963 | 13 | 7 | 3 | 38 |
| `flowergarden` | 20320 | 18574 | 90 | 7 | 2 | 51 |
| `first_floor` · `my_room` | 287 | 480 | 2 | 8 / 5 | 1 | 1 |

**槽位不是七个固定槽**。八个包全有的只有 `navmesh_target` / `env` / `collider` 三个，外加「至少一个 `decoration*`」；`base` 在室内两个包里**不存在**，相机槽只有三个包有，`decoration (1)`（Unity 的重名后缀）只在 `grasslands` / `flowergarden` / `memorialplace` 出现。每个槽带 `role` 与 `known`：`known` 为假意味着这是本提取器没有语义的槽，**不是被归进了某个默认桶**。

**隐藏节点照原样保留并单列**。`inactiveNodes` 列出出货即隐藏的节点（例如家园站那台住房竞赛相机），glb 节点上也带 `extras.active = false`。产物是「作者写下的场景」，删掉不诚实；照画又会画出游戏永远不显示的东西——所以两边都给。

### 碰撞面语义：一面一个文件

一个站点的可行走面、挡相机面、挡墙面与脚步声换材质面是**四种不同的东西**，合成一个文件之后消费方就分不出来了。所以碰撞面全部**离开可见场景**、一面一个 glb，`role` 由名字给出：

| `role` | 名字后缀 | 六个户外包里 |
|---|---|---|
| `walkableGround` | `_nav_ground` | 6/6 |
| `footstepSurface` | `_footse` | 6/6 |
| `cameraBlocker` | `_nav_cam` | 只有 `grasslands` 与 `memorialplace` |
| `wallBlocker` | `_nav_wall` | 只有 `home` |

`role` 为 `null` 表示名字没说它是什么，**不是它没有作用**——`beach` 的海面 `sea01` 就是这样一张：它**同时是可见几何与碰撞体**，所以它在场景 glb 里也在 `collision/` 里，并带 `visible: true`。「碰撞面一定不可见」不是不变式，一个反例即足。

脚步声怎么选：`sites.json` 的 `footsteps` 是一张 **RGB 到 cue 的表**（8 行，走与跑各一个 cue）。**颜色住在哪张面上没有查**——`_footse` 是候选，但本次没有追到读它的消费方，所以这条以表的原样给出，不作断言。

室内两个包**一个 `MeshCollider` 都没有**（只有 `env/test_volume` 一个 20 单位的盒子）：墙地碰撞在套件与可行走面包里。

### 导航网格：出货与运行时是两个答案

**五个包出货烘焙**（`grasslands` 30 块瓦片、`beach` 16、`flowergarden` 16、`memorialplace` 12、`festivalgarden` 4），**三个不出货**（`home` 与室内两个）。这两种状态**不能互相冒充**：给一个不出货的站点塞一份空瓦片列表，读起来就成了「烘过，而且是空的」，正好相反。不出货的那三个在运行时按 `navmesh_target` 下的碰撞面现烘。

烘焙有**两档**：`grasslands` 用默认 agent（`agentTypeID` 0）加手工 cell/tile 并带高度网格；另四个共用一个自定义 agent id `-1372625422`、全自动参数、`m_SourceBounds` 一律 extent (49.76, 5, 49.76) —— **它们不是一批烘的**。五份的 `agentHeight` 全是 1.0，与 `playerHeight` 逐份吻合。

瓦片**原样搬运**：`navmesh.bin` 是全部瓦片的字节拼接，`tiles.index` 给每块的 `offset` / `bytes` / `hash`，`tiles.parsed` 恒为 `false` 并带理由。不解析是决定：格式是 Unity 自己烘的 Detour 数据，猜它等于造数据。**能给的几何是高度网格**：`grasslands` 那份带 415 顶点 / 832 三角形，导成 `navmesh/heightmesh-0.glb`，消费方不需要导航运行时就能画或采可行走面。烘焙件全部 `m_Position` 为零、`m_Rotation` 恒等（`siteLocal: true`），所以**它也必须按 `sitePosition` 平移**，和几何一样。

### 室内三站是模块套件，不是一站一个 glb

`first_floor` 与 `my_room` 的包只有 105 / 92 个对象：站点视图外壳、天气预设、一个测试体积、一片天空——**没有 `base` 槽，没有任何墙地几何**。房间在运行时由三个包拼出来，`index.json` 的 `indoor` 就是这份拼装口径：

| 部件 | 内容 |
|---|---|
| `indoor.kit` | 套件：18 个网格（floor/wall 各 large/medium/small + entrance + 带声地板）、11 份材质、15 个 `MeshCollider`；**碰撞用的就是可见的那几片网格** |
| `indoor.levels.NN.module` | 每个扩建档一对 `mdl_static_floor` + `mdl_static_wall`，网格全部外引套件。实测：档 01/02/03 都用 `small`，04 用 `medium`，05 用 `large` |
| `indoor.levels.NN.walkable` | 每档一个纯碰撞 prefab。实测：03/04/05 分别是 small/medium/large，**01 与 02 的 `MeshCollider` 指针是空的**（`pathId = 0`）——那两档的可行走面**没有出货**，产物按「空指针是作者写下的状态，不是查找失败」记下来，不假装有 |

所以「一站一个 glb」的产物形状会**静默丢掉三个室内站点**：它们的几何根本不在站点包里。房间大小从 `sites.json` 的 `levels[].layouts[]` 取：格数与单位数同时给出（房间档 1 是 10×10×10 格 = 2.5 单位高，档 5 是 24×10×20 格）。

### 材质：给族名与属性块，不给翻译

每份站点材质的 shader 都住在**本域不拥有的包**里（`mysekai/shader`），所以能诚实给出的是 shader 的**族名**、它第一个 subshader 的 tags，以及**整个作者写下的属性块**（floats / colors / 贴图槽 / 每槽的 scale-offset）。读法：地表 `Mysekai/Site/Ground`、水 `Mysekai/Water`、植被 `Mysekai/Site/Tree`、其余实体 `Mysekai/Site/FieldObject`；`festivalgarden` 的地表换成 `Mysekai/Site/Ground-Birthday`。

glb 里的 glTF 材质是**预览近似**，不是翻译，每个取值都来自作者数据：基色贴图取 `_MainTex`/`_BaseMap` 中第一个绑上的（按该槽的 scale-offset 采，走 `KHR_texture_transform`）、基色系数取 `_Color`/`_BaseColor`、`_UseAlphaClip` 为真则 `alphaMode: MASK` 且阈值取 `_AlphaClip`、否则 shader 的 `QUEUE` tag 为 `Transparent` 才 `BLEND`、`_Cull` 为 0 才双面。**权威记录是 JSON 里的属性块**，不是这份近似。贴图按相对路径引用而不内嵌，所以一张图在产物里只有一份。

贴图数组（`Texture2DArray`）逐层导出，单列在材质的 `textureArrays` 里而不混进 `textures`：它要用层坐标采样，混在一起消费方会把两者搞错。

### Timeline 是空插槽，不是「不支持」

站点外壳（`site/root` 与 `site/environment/common`）各带一个 `PlayableDirector`，**`m_PlayableAsset` 为空**。产物把它记成 `timelineSockets` 里的一条空插槽，并写出**谁来填**：运行时按现象从 `EnvironmentLoadData.PlayableAsset` 经 `SiteEnvironmentViewController._playableDirector` 赋值。写「不支持的时间轴」是错的记法——会让消费方去找一个本来就不存在的资产。另外八个 director **是绑好的**（`festivalgarden` 五个、三个物件包各一个），`bound: true` 并给出资产名。

### 家具不在站点里

八个场景包里**没有一个**类名含 `Fixture` 的 MonoBehaviour：家具是运行时实例化到站点视图下的，不烤进包。整个站点路径里**只有一个包**需要家具包——配送站的生日蛋糕——这是**例外，不许外推**。

注意依赖的两个来源：产物给的 `declaredDependencies` 是**包自己 bundle 里声明的**，与下发清单**不是一回事**。实测：配送站包内声明 12 条、清单声明 23 条，而那个家具包**只出现在清单一侧**；`flowergarden` 是 1 对 5、`grasslands` 1 对 4。要知道下发时必须同装哪一套请用清单，`declaredDependencies` 只回答「这个包自己指向谁」，`dependencySource` 字段在产物里把这件事写明。

### 每个包都被打开了，并且逐对象记账

`packages.json` 里每个包一条，带对象清点、类型直方图、脚本直方图与 `objects: {total, exported, skipped, unsupported, accountedFor}`。三种处置**必须加起来等于总数**（109 个包全部满足）——这就是「没有一个包留着未查」的可核形式，而不是一句声明。`skipped` 一定带理由（包自己的清单对象、脚本身份、包内 shader 变体、动画状态机）。**本提取器没有结构化读法的组件仍然导出**：它的序列化字段原样写出、指针换成它指的东西，解释与提取是两件事，不解释不构成丢数据的理由。

一族值得单说：**23 套房间皮肤确实带 `FixtureView` 与 `NavMeshModifier`**。「站点场景里零家具 MonoBehaviour」这句只对**八个场景包**成立——门与窗本身就是家具类资产。皮肤里的门开合动画按编译曲线解出（161 段），绑定的节点路径由 CRC-32 反查还原，认不出的哈希原样留下并标明。

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
8. 现象的站点覆盖按两级查找；`overrides` 里没有某站点意味着它用全局值，不是查找失败。
9. 后处理参数的 `overrideState` 为假时不要施加该值。
10. 现象配置的存储值按原样使用：云影采样取贴图尺寸的倒数，滚动速度是速度向量乘标量。
11. 贴图数组走 `textureArrays` 而不是 `textures`；层号按 `layerFormula` 取整（floor，不是四舍五入），且先看 `sampling.arrayMode` 再决定要不要采。
12. `renderMode` 为 `Mesh` 的发射器要画它的 `mesh`；`mesh` 为 `null` 表示指向的几何不在任何包里。
13. `omitted` 是读过并刻意不导出，`unsupported` 才是缺口；不要把两者混为一谈。
14. 音频的 `skipped` 表示缺外部解码器而非缺数据，音频档已在盘上；循环点按秒取 `[loopStartSeconds, loopEndSeconds)`，波形没有展开循环。
15. **站点 glb 是站点局部坐标**：世界偏移只在 `sites.json` 的 `sitePosition` 里，画之前自己加上。三行房间站共用一个包，烤进几何就永久丢掉 2F 与 3F。
16. 落位一律 `world = sitePosition + grid × 0.25`；`positionY` 是隔离步长不是层高。
17. 站点场景 glb 有多个 scene，要摆的是 `defaultScene`；其余 scene 是同包里的模型资产与按等级换的装饰。
18. 碰撞面按 `role` 用，不要按文件名猜；`role` 为 `null` 的面（如 `beach/sea01`）可能同时是可见几何。
19. 导航网格分「出货烘焙」与「运行时现烘」两态：`navmesh` 为空数组表示后者，不表示烘出来是空的。瓦片是不透明字节，能直接用的是 `heightmesh-N.glb`。
20. 室内三站按 `index.json` 的 `indoor` 拼：套件 + 每档模块 + 每档可行走面；01/02 档的可行走面本来就没出货。
21. 站点材质用族名与属性块自己近似；glb 里的材质是预览近似，不是游戏的着色。
22. 空的 `timelineSockets` 是运行时填的插槽，不是缺失的资产。
23. `declaredDependencies` 不是「必须同装的那一套」；那一套由下发清单给出（配送站 12 对 23）。
