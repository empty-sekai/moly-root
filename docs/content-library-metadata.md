# 内容馆使用的家具展示数据

`mysekai-fixtures.json` 仍然是运行时使用的家具主表切片。为使内容浏览器显示游戏原有名称，而不是把资源包名伪装成名称，切片额外保留两个可空字段：

| 输出字段 | 原始主表字段 | 语义 |
| --- | --- | --- |
| `name` | `mysekaiFixtures.name` | 原始名称，保留语言和原文 |
| `description` | `mysekaiFixtures.flavorText` | 原始说明，保留换行和标记 |

字段不存在或为 `null` 时输出 `null`；空字符串保留为空字符串，不从 `assetbundleName` 猜测名称。非字符串且非空的展示字段会在导出时明确报错。消费者应在缺少名称时显示可理解的回退标签，而不是把回退值写回源数据。

这两个字段是版本 1 的向后兼容扩展。家具 ID、资源包关联、动作类型、摆放方式、尺寸和统计含义均不改变。展示信息不参与实例身份、导航、交互可用性或 Timeline 选择。

## 重新生成

使用与现有资源相同快照的完整主表目录，调用现有导出入口：

```python
from fixtures.master_slice import export_fixture_master_slice

export_fixture_master_slice(
    "path/to/matching-master-directory",
    "path/to/staging/mysekai-fixtures.json",
    region="cn",
    game_version="6.0.0",
)
```

先写入暂存目录，核对新旧输出中所有既有运行字段、ID 集合和统计一致，再备份并替换正式文件。不要只因为两份主表的行数相等就认定快照一致。

## 家具缩略图

`ui.fixture_thumbnail.export_fixture_thumbnails` 从调用方提供的原始缩略图资源包提取像素，并使用主表中的资源名和颜色编号精确关联。内容馆的普通家具与系统家具可以通过现有入口批量导出：

```python
import json
from pathlib import Path
from ui.fixture_thumbnail import export_fixture_thumbnails

master_path = Path("path/to/mysekai-fixtures.json")
rows = json.loads(master_path.read_text(encoding="utf-8"))["fixtures"]
ids = [row["id"] for row in rows if row["fixtureType"] in ("normal", "system")]
export_fixture_thumbnails(
    "path/to/decrypted/mysekai__thumbnail__fixture",
    master_path,
    "path/to/staging/fixture-thumbnails",
    fixture_ids=ids,
)
```

此入口不猜测地板、墙面、生长植物等其他类型的缩略图路由。缺少精确匹配时应保留可检查的错误或缺口，不能用其他物品的图片代替。替换清单前还应确认已有颜色版本未被遗漏，已有 PNG 的哈希未发生意外变化。

## 回归检查

```text
python -m pytest tests/test_fixture_master_slice.py -q
python -m pytest -q --disable-warnings
```

测试覆盖中文名称、原始说明、空值、空字符串、错误类型，以及展示字段不改变运行时身份、尺寸、动作和顺序。运行程序还应检查字体覆盖：家具名称增加的字形未必存在于旧的字体子集中。
