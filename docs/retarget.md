# 动作库重定向

[English](retarget.en.md)

包清单（AssetBundleInfoNew）由用户自行提供，本仓不内置、不分发任何游戏数据。

本文描述如何把共享动作库 glTF 应用到源角色之外的兼容人形模型。使用者需要已有 glTF 加载器和动画系统。

## 绑定名称

动画通道按 glTF 节点名称，而不是节点索引绑定。必需的人形骨骼名称为：

```text
Hips Spine Spine1 Head
LeftUpLeg RightUpLeg LeftLeg RightLeg LeftFoot RightFoot
LeftArm RightArm LeftForeArm RightForeArm LeftHand RightHand
LeftHandThumb1 LeftHandThumb2 LeftHandIndex1 LeftHandIndex2
LeftHandMiddle1 LeftHandMiddle2 LeftHandRing1 LeftHandRing2
LeftHandPinky1 LeftHandPinky2 RightHandThumb1 RightHandThumb2
RightHandIndex1 RightHandIndex2 RightHandMiddle1 RightHandMiddle2
RightHandRing1 RightHandRing2 RightHandPinky1 RightHandPinky2
```

共 36 个命名骨骼。源骨架存在时，动作还会包含六个可选辅助旋转轨道：`LeftArm_twist`、`LeftForeArm_twist`、`LeftHand_twist`、`RightArm_twist`、`RightForeArm_twist`、`RightHand_twist`。手指名称使用公开拼写 `LeftPinky`，除非绑定表显式建立别名，否则不要替换为其他引擎别名。

## 通道语义

必需骨骼通道都是相对于节点静止变换的局部 glTF `rotation` 四元数，顺序为 `[x, y, z, w]`。`Hips` 总是带一条平移通道，表示以米为单位的局部偏移，不是 root motion，也不是目标模型原有的髋部高度。不要把它当作绝对世界变换。

大多数动作还在部分关节上带**平移自由度（TDoF）通道**：源数据在下列骨骼上动画局部平移（完整局部平移，含静止分量）：`Spine`、`Spine1`、`Head`、`LeftLeg`、`RightLeg`、`LeftFoot`、`RightFoot`、`LeftArm`、`RightArm`、`LeftHand`、`RightHand`（每条动作只出现其中一部分）。这些通道承载引擎的关节平移动画——例如交叉手臂时臂根前移。丢弃它们会让手臂类姿势穿模，且与烘焙好的髋部旋转不一致，所以同骨架回放时**必须原样应用**。

六个 `_twist` 通道是辅助节点上的普通局部旋转。目标没有辅助节点时可以跳过它们，主肢体旋转仍可使用。公共资产没有动画 `scale` 通道；保留目标节点原有缩放。动作库使用右手、Y 向上的 glTF 坐标，位置单位为米。

## 髋部平移缩放

局部旋转可以直接复用；髋部平移必须按身高比例缩放：

```text
targetHipsOffset = sourceHipsOffset * targetHeight / sourceHeight
```

优先对源和目标都使用从最低着地脚到头顶的测量高度。结果应用到目标 `Hips` 的局部平移，不要应用到每根骨骼或模型根节点。

非 `Hips` 的平移通道（TDoF）是该关节的**完整局部平移**（静止值 + 动画偏移）。套到身材不同的目标时不要照抄绝对值：先减去源骨架该骨的静止平移得到偏移量，按对应肢段长度比（近似可用身高比）缩放，再加到目标骨自己的静止平移上：

```text
targetLocalT = targetRest + (channelValue - sourceRest) * targetSegment / sourceSegment
```

这是比例修正而非完整重定向求解器；比例差异较大时，精确脚部和手部接触仍需 IK 或专用重定向流程。

## three.js 最小示例

```js
const sourceClip = library.animations.find((clip) => clip.name === "mov_cm_adult_idle_L");
const tracks = sourceClip.tracks.map((track) => {
  const nodeName = track.name.slice(0, track.name.indexOf("."));
  if (!targetNodes.has(nodeName)) return null;
  if (nodeName !== "Hips" || !track.name.endsWith(".position")) return track.clone();
  const values = track.values.slice();
  for (let i = 0; i < values.length; i += 3) values[i + 1] *= targetHeight / sourceHeight;
  return new THREE.VectorKeyframeTrack(track.name, track.times.slice(), values);
}).filter(Boolean);
const targetClip = new THREE.AnimationClip(sourceClip.name, sourceClip.duration, tracks);
```

生产环境应检查目标节点重名，并对缺失必需骨骼拒绝绑定或标记动作不可用。不要按位置顺序猜测，也不要静默绑定到其他节点。
