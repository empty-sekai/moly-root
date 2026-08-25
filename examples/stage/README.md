# Stage 演出预览

这是独立于 `examples/viewer/` 的演出 demo 骨架。它从提取流水线的产物根读取角色、轨树、clip 时间字段、clip target、attach point 与共享动作库，并按选中的 timeline 预览一段演出。

## 启动

演出文档目录没有额外索引文件，因此页面需要静态服务器提供目录清单。请在 `moly-root` 目录运行：

```text
python -m http.server 8000
```

打开：

```text
http://localhost:8000/examples/stage/index.html?base=../../local-data/pipeline-out
```

`base` 是相对于页面的 URL 路径，也可以传入可访问的绝对 URL。页面不写死本机绝对路径。
`base` 指向的必须是**提取流水线的输出根**：页面读的每一条路径都是流水线声明会写的路径，
不做「读不到就换个地方找」的回退。

## 数据与边界

- 演出产物按**宿主**分两族，页面分开读，不合并：
  - `cutscene-timeline/{tracks,clips,clip-targets}/` —— 剧情演出（宿主 `CutSceneView`）；
  - `fixture-timeline/{tracks,clips,clip-targets}/` —— 家具演出（宿主 `NPCFixtureTimelineView` / `PlayerFixtureTimelineView`）。
  下拉框按族分组，选中项的 clips 与 clip-targets 只从**它自己那一族**取。
- 角色从 `manifest.json` 选择，动作从 `motion-library.glb` 与 `motion-library.index.json` 读取。
- 演出动画包来自 `perf-animations/`（每包一份 `.glb` + 一份 `.index.json`）。
- 角色挂点来自 `fixture-interface/attach-points.json` 的 `loc_start###` 局部变换；区域来自 `fixture-interface/areas.json`。
- 相机文档来自 `camera/`。
- `fixture-models/` 缺失时，画面明确显示「家具几何未就绪」，不会用方块或其它几何冒充家具。
- 「自发型」只保留头顶 tweet HUD / 气泡；「玩家参与型」显示底部对话窗。UI 文本产物（`ui/talk.json`）由 UI 域产出，未就绪时如实标注。
- `selfcheck.js` 是独立取证入口：它重新读取原始产物并在页面内显示 c1-c10，不读取 `stage.js` 的中间状态。

## 可用参数

| 参数 | 作用 |
| --- | --- |
| `base=<url>` | 数据根目录（提取流水线输出根），默认 `../../local-data` |
| `unit=<unit>` | 选择 `manifest.json` 中的角色，例如 `unit=101` |
| `performance=<key>` | 预选某条演出条目（`族::文档::timeline`，也接受包名子串） |
