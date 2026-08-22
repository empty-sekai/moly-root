# 呈现层消费指引

[English](presentation.en.md)

本文是[数据契约](data-contract.md)的呈现层延伸，面向在浏览器中消费角色 `.glb`、`*.rig.json` 和 `emoticons.json` 的开发者。数据字段仍按数据契约解释；本文只规定坐标、挂点、朝向、渲染状态、深度和绘制顺序。

示例使用 three.js。示例中的 `itemRoot` 是头顶件根节点，`item.nodes` 是 JSON 中的节点数据，`character` 是已经加载的角色骨架。

## 1. 坐标换手

角色 `.glb` 是 glTF 右手系、Y 向上、米制；其世界 x 已相对源数据取反。头顶件 JSON 的局部变换仍是源坐标系中的原值，因此不能把角色 glb 的变换规则直接套到所有头顶件上。

`particle` 条目消费局部数据时，对每个局部位置和局部四元数做一次共轭：

```text
position([x, y, z]) = [-x, y, z]
rotation([qx, qy, qz, qw]) = [qx, -qy, -qz, qw]
emissionSample.position.x = -emissionSample.position.x
emissionSample.direction.x = -emissionSample.direction.x
```

这一步作用于粒子条目的局部节点数据和发射样本；缩放不变。`sprite` 条目是相机帧内容，不做共轭，直接使用其条目数据。

three.js 最小落地示例：

```js
function transformParticleLocal(nodeData, object3d) {
  const [x, y, z] = nodeData.position;
  const [qx, qy, qz, qw] = nodeData.rotation;
  object3d.position.set(-x, y, z);
  object3d.quaternion.set(qx, -qy, -qz, qw);
}

function transformParticleSample(sample) {
  return {
    ...sample,
    position: [-sample.position[0], sample.position[1], sample.position[2]],
    direction: [-sample.direction[0], sample.direction[1], sample.direction[2]],
  };
}

function applyItemTransform(item, nodeData, object3d) {
  if (item.viewKind === "particle") transformParticleLocal(nodeData, object3d);
  else {
    object3d.position.fromArray(nodeData.position);
    object3d.quaternion.fromArray(nodeData.rotation);
  }
}
```

不要对同一条粒子数据再次反射；不要对 sprite 的相机帧数据反射。

## 2. 挂点

角色骨架中的四个挂点节点名在全部 31 个角色里一致：`HeadRoot`、`Head`、`Spine`、`Hips`。

挂点分派规则如下：

```text
sprite  -> HeadRoot，局部位置清零
particle + view.anchor == Face             -> Head
particle + view.anchor == Spine            -> Spine
particle + 其它或缺省 view.anchor          -> Hips
```

`particle` 条目的局部位置要保留；现有数据中的这些局部位置全为零，但消费者仍应读取字段而不是自行抬高。挂点分派独立于 `keepPosition`；`keepPosition` 只门控旋转，见下一节。

three.js 示例：

```js
const particleAnchor = {
  Face: "Head",
  Spine: "Spine",
};

function attachItem(item, itemRoot, character) {
  const anchorName = item.viewKind === "sprite"
    ? "HeadRoot"
    : (particleAnchor[item.view?.anchor] ?? "Hips");
  const anchor = character.getObjectByName(anchorName);
  if (!anchor) throw new Error(`Missing attachment node: ${anchorName}`);

  anchor.add(itemRoot);
  if (item.viewKind === "sprite") itemRoot.position.set(0, 0, 0);
  // Particle local position is read and retained; keepPosition does not clear it.
  return anchor;
}
```

对粒子节点的局部位置先按上一节做坐标换手，再作为挂点下的局部位置；对 sprite 根节点始终写入零局部位置。

## 3. 朝向

### Sprite

每帧把 sprite 的**世界**朝向写成“件位置看向相机”的等价形式。three.js 的物体前向约定与此相反，所以用于朝向的水平/三维方向取：

```text
forward = normalize(camera.position - itemWorldPosition)
```

等价的四元数是把件的约定前向轴转到 `forward`。如果网格的前向轴取 `+Z`，公式为：

```text
spriteWorldRotation = quatFromUnitVectors(+Z, forward)
```

three.js 示例（父节点存在时把世界四元数换回局部四元数）：

```js
const worldPosition = new THREE.Vector3();
const parentWorldQuaternion = new THREE.Quaternion();
const forward = new THREE.Vector3();
const itemWorldQuaternion = new THREE.Quaternion();

function faceCamera(spriteRoot, camera) {
  spriteRoot.getWorldPosition(worldPosition);
  forward.copy(camera.position).sub(worldPosition).normalize();
  itemWorldQuaternion.setFromUnitVectors(
    new THREE.Vector3(0, 0, 1),
    forward,
  );
  spriteRoot.parent.getWorldQuaternion(parentWorldQuaternion);
  spriteRoot.quaternion.copy(
    parentWorldQuaternion.invert().multiply(itemWorldQuaternion),
  );
}
```

### Particle

只有 `view.keepPosition == true` 的粒子条目每帧写旋转。先在水平面 `xz` 上计算从 Hips 挂点前向到相机方向的带符号夹角：

```text
toCamera = normalizeXZ(camera.position - HipsAnchor.worldPosition)
hipsForward = normalizeXZ(HipsAnchor.forward)
thetaSource = signedAngleXZ(hipsForward, toCamera)
theta = -thetaSource
particle.localRotation = Euler(theta, 0, 0)
```

`theta` 的负号是坐标换手后的取反。`keepPosition == false` 时**什么都不写**：保留预制旋转，并继续继承骨骼旋转。`keepPosition` 门控的是旋转，不是位置。

下面的 `signedAngleXZ` 使用 `atan2(crossY, dot)`，并固定同一套水平面符号：

```js
function normalizeXZ(vector) {
  vector.y = 0;
  return vector.normalize();
}

function signedAngleXZ(from, to) {
  const crossY = from.z * to.x - from.x * to.z;
  const dot = from.x * to.x + from.z * to.z;
  return Math.atan2(crossY, dot);
}

const hipsWorldPosition = new THREE.Vector3();
const hipsForward = new THREE.Vector3();
const toCamera = new THREE.Vector3();

function updateParticleRotation(item, particleRoot, hipsAnchor, camera) {
  if (item.view?.keepPosition !== true) return;

  hipsAnchor.getWorldPosition(hipsWorldPosition);
  hipsAnchor.getWorldDirection(hipsForward);
  toCamera.copy(camera.position).sub(hipsWorldPosition);
  normalizeXZ(hipsForward);
  normalizeXZ(toCamera);
  const theta = -signedAngleXZ(hipsForward, toCamera);
  particleRoot.rotation.set(theta, 0, 0);
}
```

## 4. 按着色器族应用渲染状态

材质带有 `shader` 字段。先按完整 shader 名称选择族，再解释该族的字段；不允许把一个族的属性名当作另一个族的 pass 状态。

### Unity BlendMode 到 WebGL 因子

`Mysekai/Effect/UberUnlit` 的混合因子来自 `_BlendSrc` 和 `_BlendDst`。枚举值对照如下：

| Unity BlendMode | 值 | WebGL 因子 |
|---|---:|---|
| Zero | 0 | `gl.ZERO` |
| One | 1 | `gl.ONE` |
| DstColor | 2 | `gl.DST_COLOR` |
| SrcColor | 3 | `gl.SRC_COLOR` |
| OneMinusDstColor | 4 | `gl.ONE_MINUS_DST_COLOR` |
| SrcAlpha | 5 | `gl.SRC_ALPHA` |
| OneMinusSrcAlpha | 6 | `gl.ONE_MINUS_SRC_ALPHA` |
| DstAlpha | 7 | `gl.DST_ALPHA` |
| OneMinusDstAlpha | 8 | `gl.ONE_MINUS_DST_ALPHA` |
| SrcAlphaSaturate | 9 | `gl.SRC_ALPHA_SATURATE` |
| OneMinusSrcColor | 10 | `gl.ONE_MINUS_SRC_COLOR` |

three.js 因子映射和深度比较示例：

```js
const unityBlendToThree = {
  0: THREE.ZeroFactor,
  1: THREE.OneFactor,
  2: THREE.DstColorFactor,
  3: THREE.SrcColorFactor,
  4: THREE.OneMinusDstColorFactor,
  5: THREE.SrcAlphaFactor,
  6: THREE.OneMinusSrcAlphaFactor,
  7: THREE.DstAlphaFactor,
  8: THREE.OneMinusDstAlphaFactor,
  9: THREE.SrcAlphaSaturateFactor,
  10: THREE.OneMinusSrcColorFactor,
};

const unityZTestToThree = {
  1: THREE.NeverDepth,
  2: THREE.LessDepth,
  3: THREE.EqualDepth,
  4: THREE.LessEqualDepth,
  5: THREE.GreaterDepth,
  6: THREE.NotEqualDepth,
  7: THREE.GreaterEqualDepth,
};

function applyZTest(material, zTest) {
  if (zTest === 0 || zTest === 8) {
    material.depthTest = false;
    return;
  }
  material.depthTest = true;
  material.depthFunc = unityZTestToThree[zTest] ?? THREE.LessEqualDepth;
}
```

### `Mysekai/Effect/UberUnlit`（particle）

- 混合：使用 `_BlendSrc` / `_BlendDst` 的枚举值查表；`_SrcBlend` / `_DstBlend` 是残留字段，不生效。
- 深度测试：`_ZTest` 为 `0` 或 `8` 时关闭；其它已知比较值按上表映射。
- 写深度：使用 `_ZWrite`；现有值恒为 `0`，因此不写深度。
- 背面：`_Cull == 0` 为双面；`_Cull == 2` 为背面剔除，即 three.js 的 `FrontSide`。
- 目标颜色掩码只写 RGB，不写目标 alpha。需要自定义 pass 的颜色写掩码为 `(true, true, true, false)`，不要让粒子修改目标 alpha。

```js
function configureUberUnlit(material, floats) {
  material.transparent = true;
  material.blending = THREE.CustomBlending;
  material.blendSrc = unityBlendToThree[floats._BlendSrc] ?? THREE.OneFactor;
  material.blendDst = unityBlendToThree[floats._BlendDst] ?? THREE.ZeroFactor;
  material.blendEquation = THREE.AddEquation;
  material.depthWrite = Boolean(floats._ZWrite);
  material.side = floats._Cull === 0
    ? THREE.DoubleSide
    : THREE.FrontSide; // _Cull == 2: cull back faces
  applyZTest(material, floats._ZTest);
  // A custom WebGL pass must use gl.colorMask(true, true, true, false).
}
```

### `Mysekai/Emoticon/Sprite`（sprite）

- 混合固定为 `One` / `OneMinusSrcAlpha`。
- 片元颜色先做 `rgb *= a` 的预乘；three.js 里只需材质打开 `premultipliedAlpha: true`（渲染器的同名参数是画布合成选项，保持默认即可）。
- 背面固定双面。
- `_ZWrite` 现有值恒为 `1`，因此写深度。
- `_ZTest` 按上面的规则处理。

```js
function configureSprite(material, floats) {
  material.transparent = true;
  material.premultipliedAlpha = true;
  material.blending = THREE.CustomBlending;
  material.blendSrc = THREE.OneFactor;
  material.blendDst = THREE.OneMinusSrcAlphaFactor;
  material.blendEquation = THREE.AddEquation;
  material.side = THREE.DoubleSide;
  material.depthWrite = true;
  applyZTest(material, floats._ZTest);
}

const renderer = new THREE.WebGLRenderer({
  alpha: true,
  premultipliedAlpha: true,
});
```

`_ZWrite`、`_ZTest` 等同名但不驱动 pass 的残留字段不能被猜测使用；族规则优先于属性名。

### 不认识的 shader

不认识的 shader 退化为中性材质并发出告警。不要根据字段名猜混合、深度或剔除状态：

```js
function neutralMaterial(shaderName) {
  console.warn(`Unknown shader family: ${shaderName}`);
  return new THREE.MeshBasicMaterial({
    transparent: true,
    blending: THREE.NormalBlending,
    depthTest: true,
    depthWrite: false,
    side: THREE.FrontSide,
  });
}
```

## 5. 深度偏移 `_ZOffset`

`_ZOffset` 是线性眼深度（米）上的加法，不是 polygon offset。它只改 clip-space 的 `z`，不改 `x`、`y` 或 `w`：

```text
viewPos = modelViewMatrix * vec4(position, 1)
d = -viewPos.z + _ZOffset
clip.z = P[2][2] * (-d) + P[3][2]
```

生效阈值按 shader 族分开：

```text
UberUnlit: abs(_ZOffset) > 0.004
Sprite:    _ZOffset > 0.004
```

Sprite 的判断没有 `abs`；现有 sprite 数据中的非零值全为负，因此这些值全部不生效。负值表示把深度拉向相机，使头顶件穿过角色的遮挡；这是唯一把件浮到角色前面的机制。

three.js 自定义顶点阶段示例：

```glsl
vec4 viewPos = modelViewMatrix * vec4(position, 1.0);
vec4 clip = projectionMatrix * viewPos;

bool active = isUberUnlit
  ? abs(uZOffset) > 0.004
  : uZOffset > 0.004;
if (active) {
  float d = -viewPos.z + uZOffset;
  clip.z = projectionMatrix[2][2] * (-d) + projectionMatrix[3][2];
}
gl_Position = clip;
```

在 JavaScript 中只把阈值判断交给族选择，不能改用 `polygonOffset` 或改变 `clip.x`、`clip.y`、`clip.w`：

```js
function zOffsetActive(shaderFamily, zOffset) {
  return shaderFamily === "uberUnlit"
    ? Math.abs(zOffset) > 0.004
    : zOffset > 0.004;
}
```

## 6. 绘制层次

绘制顺序是：

```text
角色不透明绘制（写深度）
    -> 全部头顶件的透明绘制
       -> 头顶件之间按离相机远到近
```

头顶件之间按件的眼深度排序。对世界位置 `p`，先变换到相机空间，再取：

```text
eyeDepth(p) = -(camera.matrixWorldInverse * vec4(p, 1)).z
```

眼深度较大的件更远，先画；排序伪代码为：

```text
items.sort((a, b) => eyeDepth(b) - eyeDepth(a))
```

three.js 示例：

```js
const worldPosition = new THREE.Vector3();
const viewPosition = new THREE.Vector3();

function eyeDepth(object3d, camera) {
  object3d.getWorldPosition(worldPosition);
  return -viewPosition.copy(worldPosition)
    .applyMatrix4(camera.matrixWorldInverse).z;
}

function orderOverheadItems(items, camera) {
  return items.slice().sort(
    (a, b) => eyeDepth(b.root, camera) - eyeDepth(a.root, camera),
  );
}

function assignRenderOrder(characterRoot, orderedItems) {
  characterRoot.renderOrder = 0;
  for (let i = 0; i < orderedItems.length; i += 1) {
    orderedItems[i].root.renderOrder = 1000 + i;
  }
}
```

角色材质必须写深度并先绘制。sprite 材质写深度，所以它绘制后会遮住更远的粒子件；粒子件按自身材质族的深度测试读取该深度。不要用任何逐件排序字段替代上述顺序：`sortingOrder` 的现有值全为 `0`，只有个别条目内部用 `0/1` 分两层，这些值不是全局绘制层协议。

## 7. 粒子取值编码

粒子的 min-max 曲线四模式和渐变双轴规则已完整写在[数据契约的粒子取值编码](data-contract.md#particles)中。这里不重复定义；实现时必须按该节的模式标签、关键帧时间轴和颜色/透明度独立时间轴消费。

three.js 消费侧只需要把统一取值器接到粒子更新循环：

```js
function updateParticle(particle, life01, random01) {
  particle.size = evaluateParticleValue(
    particle.definition.system.start.size,
    life01,
    random01,
  );
  particle.color = evaluateParticleColor(
    particle.definition.system.colorOverLifetime,
    life01,
    random01,
  );
}
```

`evaluateParticleValue` 和 `evaluateParticleColor` 应直接实现数据契约的编码，不要把缺省字段当成另一种模式。

## 8. 已知近似与未定语义

发射形状的局部方向与出生分布**已定**，按引擎原生语义逐形状实现：

- `Sphere`：体积内各向同性；方向 = 位置的径向单位向量，无特权轴。
- `Circle`：位置与方向都在形状局部 XY 平面内，方向为径向外向——**不是 +Z 法线**；`arc` 限制角域。
- `Cone`：基圆在局部 XY 平面（z=0），主轴 +Z，方向倾角 = `angle`（`angle=0` 时精确 `(0,0,1)`）。
- `SingleSidedEdge`：位置沿局部 X 轴均匀分布于 `±radius`（`radius` 是半长），方向恒为局部 +Y。
- `BoxEdge`：位置在单位立方体 `[-0.5,0.5]^3` 的十二条棱上（实际尺寸由 `scale` 给出），方向恒为局部 +Z，与落在哪条棱无关。

随后套形状自身变换：`pos' = R·(S·p) + t`、`dir' = normalize(R·(S·n))`——**缩放先于旋转**，`rotation` 的 Euler 合成序为 Z-X-Y（three.js 记法 `'YXZ'`）。`radiusThickness` 把采样限制在环带内（1=全域，0=只有外缘）。零缩放轴可能把方向缩成零向量：此时保持零初速，不要发明方向。完整参考实现见 `examples/viewer/emoticon.js` 的 `emitFrom()`。

以下两项仍是消费侧的明确边界：

1. sprite 紧包网格未导出。使用覆盖完整矩形的平面，不要假设有紧包顶点。
2. 粒子自旋方向的符号映射未定。不要把角速度的正负擅自解释成某个旋转方向。

three.js 示例：

```js
function makeSpriteGeometry() {
  // The exported presentation uses a full rectangle, not a tight mesh.
  return new THREE.PlaneGeometry(1, 1);
}

function applyParticleSpin(baseAngle, spinSign) {
  if (spinSign !== 1 && spinSign !== -1) return baseAngle;
  return baseAngle * spinSign;
}
```

在没有显式自旋符号配置时，应保留中性结果并记录告警，而不是把近似冒充确定语义。

## 9. 消费侧自检清单

在把呈现结果交给浏览器渲染器前，至少检查：

1. 坐标换手是否只应用在 `particle` 条目；`sprite` 条目是否未做共轭。
2. sprite 的片元预乘和 three.js 的 `premultipliedAlpha: true` 是否都开启。
3. 挂点是否按 `sprite -> HeadRoot`、`Face -> Head`、`Spine -> Spine`、其它/缺省 -> `Hips` 分派。
4. `keepPosition` 是否只控制粒子旋转；局部位置是否仍按条目数据写入。
5. 角色是否先画且写深度；透明头顶件是否全部后画，并按远到近排序。

```js
function checkPresentation(item, state) {
  console.assert(state.coordinateConversion === (item.viewKind === "particle"));
  console.assert(state.premultipliedAlpha === (item.viewKind === "sprite"));
  console.assert(state.anchorAssigned === true);
  console.assert(state.positionWritten === true);
  console.assert(state.keepPositionControlsRotationOnly === true);
  console.assert(state.characterDepthPassBeforeItems === true);
  console.assert(state.transparentItemsAfterCharacter === true);
}
```

这些检查失败时，先修正消费规则，再调整发射率、材质颜色或相机参数。
