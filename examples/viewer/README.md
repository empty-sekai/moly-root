# Character viewer example

这是一个浏览器原生的 ES module 示例，用于检查角色模型、动作片段、面部图集、布料模拟和头顶件效果。示例只提供程序代码和文档；真实角色模型、纹理、JSON 提取产物和其它游戏数据不随仓库分发。

## 启动

请通过静态 HTTP 服务器打开示例。直接双击 `index.html` 或使用 `file://` 地址时，浏览器可能阻止 ES module、import map、JSON 和 glTF 请求，因而页面只能显示空壳或加载失败。

在仓库根目录运行任意静态服务器即可，例如：

```text
python -m http.server 8000
```

然后打开：

```text
http://localhost:8000/examples/viewer/index.html
```

不要把本地提取目录提交到仓库，也不要把真实纹理或模型复制到示例目录。

## 数据放置

把提取产物目录（emoticons/、*.glb、*.rig.json、manifest.json、alone-actions.json、facial-tables.json）放到本目录或用 `?base=` 指向它

默认情况下，页面从当前目录读取以下文件：

- `manifest.json`：角色清单（`moly extract` 自动生成，列出实际提取到的角色）；缺少时使用内置单位列表。
- `sd_<unit>.glb`：角色 glTF 模型和动画。
- `sd_<unit>.rig.json`：角色挂点、面部默认值和可选布料契约。
- `facial-tables.json`：眼睛、嘴巴和默认面部逻辑表。
- `alone-actions.json`：动作、面部和头顶件的表演编排；缺少时表演控制不可用。
- `motion-library.glb` 与 `motion-library.index.json`：共享动作库（`moly extract` 的默认布局，剪辑按人形骨名绑定，全部角色复用一份）。当角色 glb 自带内嵌剪辑时可省略。
- `emoticons/emoticons.json`：头顶件条目注册表；同目录还需要条目引用的 PNG 文件。

也可以把这些产物放在单独目录，再通过 URL 参数指定根目录：

```text
http://localhost:8000/examples/viewer/index.html?base=../../local-data
```

`../../local-data` 正是 `moly extract` 的默认输出目录，所以默认提取后这条地址即可直接使用。`base` 是相对于当前页面 URL 的目录或可访问的绝对 URL。页面会把它与上述文件名拼接，因此目录内的相对引用也必须能由同一个 HTTP 服务器访问。若路径含特殊字符，请按 URL 规则编码。

示例不猜测缺失的真实数据。缺失的 `manifest.json`、rig sidecar、表演编排或头顶件数据会在状态栏和自检面板中报告；面部表和部分材质字段可以使用代码中明确标记的中性默认值。

## 表演场景与动作库

主入口是**表演场景**列表（左栏）：每个场景是编排数据里的一小段脚本——通常一到几个动作步，配上成串的眼型、口型与头顶件步骤。点击一个场景即完整播放一遍（三条通道同步），播完自然停止，不会切换到别的场景。

**自动演出**（默认开）只负责空闲时段：没有场景在播时，编排按所选策略自动选取下一个场景。「按原始策略」遵循源数据的时间门与概率（多数时间待机是原作节奏）；「轮播」依次播放每个场景，便于逐个检查。手动点播期间自动演出不插手，播完后照常接续。

**还原范围**：当前还原的是**单人（独处）表演场景**——编排数据中角色自演的部分。与家具互动的表演（触发条件与资产都在家具域）**暂未支持**，属路线图中家具资产包的一部分。

右栏另有**眼型/口型标签选择器**（表情表的行）：选择即应用，眨眼与说话状态机随即使用所选行的开合格；它与动作库同属检查工具，选择时停用自动演出。

**动作库**是次要的检查工具（右栏折叠区）：直接播放单个动作族或单段。动作名不含表情或头顶件配对（配对只存在于编排数据里），所以脸保持默认；点播时自动演出停用（按钮状态可见），需要时手动重开。

## 查询参数

| 参数 | 作用 |
| --- | --- |
| `base=<url>` | 设置数据根目录。用于把提取产物放在示例目录之外。 |
| `play=1` | 载入后以动作预览模式自动播放第一个可用动作族（编排挂起）。 |
| `play=<text>` | 载入后以动作预览模式播放名称包含该文本的动作族。 |
| `freeze=<seconds>` | 载入后把动画推进到指定秒数并暂停，便于比较固定画面。 |
| `perf=0` | 以动作预览模式启动（表演编排默认开启）。 |
| `perfmode=faithful` | 按编排中的概率和时间门选择场景。 |
| `perfmode=cycle` | 依次轮播场景，方便检查每个场景。 |
| `emote=0` | 不让表演编排自动驱动头顶件，只保留手动控制。 |
| `emoteitem=<name>` | 载入角色后直接播放指定头顶件。 |
| `emoteface=0` | 关闭头顶件的相机朝向更新，保留其世界朝向。 |
| `debug=0..3` | 选择着色诊断模式。 |
| `sabotage=<names>` | 注入自检故障。可用名称包括 `stencil`、`nan`、`gamma`、`shader`、`anim` 和 `patch`，多个名称用逗号分隔。 |
| `clothdebug=1` | 每隔一段时间输出布料链的位移诊断。 |

例如，下面的地址使用外部数据、自动播放并固定在 0.75 秒：

```text
http://localhost:8000/examples/viewer/index.html?base=../../local-data&play=1&freeze=0.75
```

## JavaScript 模块

| 模块 | 职责 |
| --- | --- |
| `viewer.js` | 页面入口和编排层。创建场景、加载 JSON/glTF、组装材质与控制器、连接 UI、处理查询参数、更新布料和头顶件，并运行自检。 |
| `shading.js` | 角色 toon `ShaderMaterial`、身体遮罩、面部分支、光照参数、眉毛 stencil overlay 和渲染状态更新。纹理按线性采样，渲染器不做 tone mapping。 |
| `facial.js` | 眼睛和嘴巴图集索引、UV 偏移、默认表情、眨眼状态机以及说话口型状态机。索引按一开始、四列和下限一的规则处理。 |
| `cloth.js` | 90 Hz Verlet 布料模拟、距离和根距离约束、角度限制、球体/胶囊/平面碰撞、有限值保护、传送重置以及 glTF 节点索引绑定。 |
| `segments.js` | 按 `_S`、`_L`、`_E`、`_O` 后缀分组动作片段，并实现 Start、Loop、End、OneShot 播放和 0.5 秒交叉淡入。 |
| `performance.js` | 读取 `alone-actions.json`，选择互斥场景，处理 `randomBranch`、`timeGated`、尾段步骤、标称时间轴，以及动作、面部和头顶件三条通道。 |
| `emoticon.js` | 加载 `emoticons.json` 和 PNG，播放 sprite 片段，模拟粒子发射器，采样曲线与颜色，并应用 shader 族对应的深度、混合和朝向规则。 |
| `selfcheck.js` | 在页面内检查 stencil、颜色空间、shader 编译、数据 sidecar、面部贴片绘制、动作推进、段衔接、布料约束和面部状态；也实现 `sabotage` 参数。 |
| `GLTFLoader.js` | Three.js glTF/glb 加载器。它是随 Three.js r160 分发的浏览器模块。 |
| `OrbitControls.js` | Three.js 相机轨道控制器，提供拖拽旋转、缩放和阻尼。 |
| `BufferGeometryUtils.js` | Three.js 几何工具模块，由 glTF 加载器使用。 |
| `three.module.min.js` | Three.js r160 的压缩 ES module 运行时。许可证见仓库根目录的 `THIRD_PARTY_NOTICES.md`。 |

## 契约对应关系

示例不是数据格式的另一份定义。字段含义以以下公开文档为准：

- [`docs/data-contract.md`](../../docs/data-contract.md) 说明 glTF、rig sidecar、面部表、动作编排、头顶件、粒子模式、材质字段和缺失数据语义。
- [`docs/presentation.md`](../../docs/presentation.md) 是呈现层延伸，说明角色与头顶件的坐标换手、挂点选择、sprite 朝向、粒子朝向、shader 族渲染状态、深度偏移和绘制顺序。
- 英文读者可对应查看 [`docs/data-contract.en.md`](../../docs/data-contract.en.md) 和 [`docs/presentation.en.md`](../../docs/presentation.en.md)。

`viewer.js` 负责把这些契约接到 Three.js 场景；`shading.js` 主要对应呈现层的角色渲染状态；`emoticon.js` 主要对应呈现层的头顶件坐标、挂点、朝向和粒子渲染状态；`facial.js`、`cloth.js`、`segments.js` 和 `performance.js` 分别消费 rig、面部、布料、动作索引和表演编排字段。若示例代码与契约文档之间出现歧义，应以契约文档对字段的定义为准，并将运行时缺失显示为诊断状态。

## 不包含的内容

本目录不分发任何真实角色 glTF、PNG、rig JSON、manifest、表演编排、面部表或头顶件数据。使用者须从自己的合法数据来源准备这些文件，并通过本地静态服务器或 `base` 参数提供给页面。仓库中的示例代码和文档可以独立阅读，但没有数据时不能渲染真实角色。

## 兼容性提示

需要支持 ES modules、import maps、WebGL2 和 stencil buffer 的现代浏览器。浏览器控制台和右侧“诊断自检”面板会显示加载失败、缺少 sidecar、shader 编译和运行时检查结果。自检中的 `skip` 表示缺少可选输入或尚未执行对应动作，不等同于数据已经通过检查。
