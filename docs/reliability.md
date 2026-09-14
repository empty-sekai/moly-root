# 下载、缓存与分包的完整性

## 下载与清单身份

bundle 和 APK 共用下载状态机。只有完整响应才会原子替换目标文件；失败保留原来的完整文件。实际收到的字节数必须符合 HTTP 长度与范围声明。`fileSize` 的网络表示语义尚未确定，因此不把它与解密后的字节数混比。

读取正文前检查全部 `Content-Length` 和 `Transfer-Encoding`。采用严格的单值长度策略：重复长度头即使相同也拒绝，逗号列表也拒绝。传输编码只接受 HTTP 客户端确实解码的单项 `chunked`；其他编码、组合编码和 TE/CL 并存均拒绝。异常进入 `InvalidDownload` 清理与完整重试路径，不覆盖旧的完整目标。

`.part.json` 记录请求 URL、对象元数据、最终 URL、强 ETag、整体长度和已保存前缀的 SHA-256。只有记录与实际前缀一致，且存在强 ETag 或用户提供的完整摘要时，才能继续下载。强 ETag 通过 `If-Range` 发送。旧版没有身份记录的 `.part` 会重新下载；完整 `200` 响应会重启。错误范围、对象变化或 hash 不符会清理临时状态。`416` 只有在完整大小与用户提供的摘要均匹配时才能放行，否则重新完整下载。每个目标使用进程锁，阻止并发写入相同临时文件。

没有可信源摘要时，HTTP 长度、范围和 ETag 校验不能替代来源真实性证明；建议调用方提供 SHA-256。未知长度的完整关闭连接响应遵循 HTTP 的结束语义，无法证明服务端本来打算发送多少字节。

Manifest 会在下载前检查重复名称、本地名称、大小写和临时文件键碰撞。斜杠与双下划线别名只有全部元数据一致时才合并，包括显式缓存名和依赖；冲突定义报错。JSON 对象里的重复键也不能覆盖不同定义。

## Master 缓存与关联

缓存目录按规范化来源和可选快照标识的 SHA-256 隔离。缓存文件是带来源、表名、快照、原始 JSON 文本和 SHA-256 的完整记录，通过临时文件原子替换。旧版根目录中的 `<表名>.json` 缓存不参与新来源的命中。

默认冻结已缓存的表，同来源重复运行不自动联网更新。Python API 可显式选择快照或刷新：

```python
from core.master import Master

frozen = Master(source_url, cache_dir=cache_dir, snapshot="input-version")
refreshed = Master(source_url, cache_dir=cache_dir, snapshot="input-version", refresh=True)
```

CLI 使用默认快照；不同版本应使用含版本的来源 URL，或另一缓存目录。缓存不能给可变远端创造跨表事务；需要一致快照时，来源本身应固定到版本或提交。缓存损坏明确报错，显式刷新可以重新获取。

`Master.provenance`、`pull` 返回值与 `extraction-report.json` 的 `masterInputs` 记录实际读取表的来源、快照、hash 和本地读取、缓存命中、远程获取、缺失或失败状态。

单角色对话筛选区分已确认独立、已确认排除和未解析。缺失的条件或分组不会成为“不依赖家具”的证明。`excluded` 统计已确认排除；`unresolvedCount`、`unresolved` 和 `unresolvedTalks` 单独记录未解析数量、原因、talk ID 和原始关联键。

## 分包与发布

源、输出及变换 overlay 与输出不得重叠；检查会解析链接别名，且发生在输出写入前。输入路径先规范化和去重，再分组；根内 `models/../image.png` 变成 `image.png`，真正越界报错。

现存 blob 只有实际大小、hash、解码后大小与 hash 都正确才复用。损坏对象会重新编码并原子替换；每次发布前独立 verifier 会重新核对本次引用的磁盘内容。

POSIX 发布文件（blob、包清单、活动和历史 catalog）使用显式 `0644` 权限，临时文件在原子替换前设置权限。复用旧 blob 时也会恢复发布权限。`core.atomic` 的通用写入默认保留既有普通文件的权限位，新文件默认 `0600`；master 缓存和下载状态保持私密。显式 `mode` 覆盖创建和更新策略，不复制旧文件的所有权、ACL 或特殊权限位。目录的访问权限由部署目录配置决定。

分组包清单使用 `packages/<完整清单 SHA-256>.json`。一次构建先完成并校验全部包的版本、路径所有权、依赖和 blob，再写入 `catalogs/<catalog SHA-256>.json`，最后原子替换入口 `asset-packs.json`。旧 catalog 引用的包清单始终保留；读取、编码或写入失败不会改写旧包清单。入口切换完成后发生异常时，新入口已经指向完整的新版本。

```sh
python -m pack.groups --src extracted --out packages --version release-version
python -m pack.verify --out packages
python -m pack.gc --out packages --json
```

`pack.verify --out` 自动识别 grouped catalog，并验证所有保留的历史 catalog；独立 manifest 仍可通过 `--manifest` 检查。schema 随安装包提供，`--schema` 仅用于覆盖。未被当前或历史 catalog 引用的 blob 单独列为 GC 候选。`pack.gc --out` 以所有保留代的引用并集计算候选，**只报告，不删除**。`--old/--new` 是两个独立 manifest 的差集，不可用来回收共享的 grouped blob store。

活动发布根可以叫 `catalogs`。`verify_catalog(..., root=...)` 始终使用显式根；CLI `--out` 和 GC 将该根传给校验器。没有显式根时，仅对实际内容地址匹配的 `catalogs/<SHA-256>.json` 历史条目向上定位一层，活动 `asset-packs.json` 使用自己的父目录。

原子替换保证普通文件系统上的进程可见性；该流程不承诺断电后目录元数据的持久化，也不能把对象存储中的多次上传变成事务。远程发布需要先上传不可变包清单和 blob，再切换 catalog。

## 动作索引与 viewer

viewer 按索引版本读取循环语义：v1（含无版本的旧索引）读取 `loop`，v2 读取 `sourceLoopTime`。仅布尔值是已解析的循环标记；缺失或无效值保持未知，`legacySuffixLoop` 不会覆盖源元数据。共享动作库的元数据优先于角色条目中残留的旧循环标记。

单段点播遇到未知循环标记时显示“缺少循环信息”，不启动该片段，也不改变已经播放的动作。族播放的 S→L→E 是独立的显式策略，L 段继续循环；这不会改变索引保存的源循环标记。

`test_motion_index_consumer.py` 将生产者元数据送入实际 viewer 适配函数和随仓分发的 Three.js `AnimationMixer`，覆盖 v1/v2、true/false/未知、单段与族播放及 S→L→E。`test_atomic_permissions.py` 在 POSIX 上检查文件权限，并在可切换 UID 的环境中验证另一个服务 UID 能读取实际发布对象、不能读取私有 JSON。

## 家具配色

家具配色导出先按 `assetbundleName` 汇总所有家具行的 `textureId` 并集（包含默认配色 1），然后每个包只读取一次。输入正反序和重复配色不会改变导出覆盖；源纹理确实不存在时，仍输出 `main: null` 或 `emission: null`，不合成替代图像。

## 安装与验证

wheel 和 sdist 显式携带三份 TOML、两份 MJS 和 manifest schema。默认 gzip 编解码使用标准库；Brotli 需要 Python `brotli` 或 Node.js，MJS 回退脚本随包分发。glTF 变换额外需要 `@gltf-transform/core`、`@gltf-transform/extensions`、`@gltf-transform/functions` 和 `meshoptimizer`，按 `transforms.toml` 选择版本，并用 `MOLY_QUANT_DIR` 指定依赖目录。音频解码程序仍由调用方提供。

完整安装验证在源码树外构建 sdist/wheel、创建全新虚拟环境、安装 wheel 及依赖，并运行 CLI help、合成独立分包、两代 grouped 分包、独立 verifier 和 GC 检查：

```sh
python -m pip install build
python tests/check_distribution.py --work-dir ../distribution-check
python -m pytest -q
```

工作目录必须尚不存在。安装验证需要访问配置的 Python 包索引，不需要游戏网络端点或真实资产。故障注入测试使用回环 HTTP、合成 JSON 和合成字节；它们不代替真实提取语料或下游视觉验收。

GitHub Actions 在 Linux 和 Windows 对 PR 的 head SHA 运行全量 pytest 和干净分发安装。Linux 另以不同 UID 检查实际发布文件可读性。分发构建使用该提交的完整源码快照，不读取工作区中的未跟踪数据。每个平台上传包含提交/树 SHA、环境版本、命令、退出码、完整日志、JUnit、源码快照与分发包的 artifact，保留 30 天。相同入口可在干净 checkout 中本地运行：

```sh
python tests/run_validation.py --work-dir ../validation-evidence
```

需要预先安装项目依赖、`pytest`、`build` 和 Node.js。非 root 的 POSIX 环境可显式加 `--service-user-check`，使用可免密执行的 sudo 运行跨 UID 测试。

| 审计项 | 回归覆盖 |
| --- | --- |
| ROOT-01 / ROOT-02 | 完整及截短响应、合法/错误续传、ETag 变化、hash 失败、416 恢复、APK 与并发锁 |
| ROOT-03 | 等长损坏、截短 blob、删除旧清单后重建、磁盘写入损坏与复用 |
| ROOT-04 | 读取、压缩、blob、包清单、历史 catalog、入口切换前后故障；旧代与 GC 引用 |
| ROOT-05 | 回环双来源、冻结/刷新、快照隔离、缓存损坏、缓存原子写入与输入来源报告 |
| ROOT-06 | 完整 sdist/wheel 资源检查、干净安装、源码树外 CLI 与分包 |
| ROOT-07 | 重复定义、相同别名合并、大小写/临时文件键碰撞、JSON 重复键 |
| ROOT-08 / ROOT-09 | 根内父级引用、GLB 相对 URI、越界、源/输出/overlay 重叠及链接别名 |
| ROOT-10 | 缺 condition、缺类型、缺 group、悬空 unit group，及已有家具/非家具对照 |
| PR1-R1 | 生产者到 viewer 的循环语义、真实 Three.js mixer、未知元数据及旧版兼容 |
| PR1-R2 | 发布/私有模式、新建/更新/复用文件、原子替换前设置权限、跨 UID 实际读取 |
| PR1-R3 | 活动根命名为 release/catalogs、历史条目、显式根、CLI 与 GC 端到端 |
| PR1-R4 | 重复/列表长度头、传输编码与 TE/CL 冲突、正常 chunked、旧目标保护及干净重试 |
| PR1-R5 | 同包配色并集、正反序一致、重复/重叠需求、真实 PNG 和缺失源纹理的 null 语义 |
