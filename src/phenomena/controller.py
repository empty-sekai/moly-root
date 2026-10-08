"""Serialized environment controller ownership, independent of phenomenon labels."""
import math
from .timeline import source_identity, _script_class

ENVIRONMENT_COMMON_PACKAGE = "mysekai__site__environment__common"


def extract_environment_controller(store):
    package = store.package(ENVIRONMENT_COMMON_PACKAGE, record_missing=False)
    if package is None:
        return None, [{"reason": "environment controller source package unavailable",
                       "package": ENVIRONMENT_COMMON_PACKAGE}]
    store.load_dependencies([ENVIRONMENT_COMMON_PACKAGE])
    candidates, gaps = [], []
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind != "MonoBehaviour" or _script_class(record, path_id, store.follow) != "SiteEnvironmentViewController":
                continue
            tree = record.tree(path_id)
            target = store.follow(record, tree.get("_playableDirector"))
            if target is None or target[0].kinds.get(target[1]) != "PlayableDirector":
                gaps.append({"source": source_identity(record, path_id),
                             "reason": "environment controller director PPtr unresolved"})
                continue
            director_record, director_id = target
            director = director_record.tree(director_id)
            required = ("m_Enabled", "m_InitialState", "m_WrapMode", "m_DirectorUpdateMode", "m_InitialTime")
            if any(key not in director for key in required):
                gaps.append({"source": source_identity(*target),
                             "reason": "environment director settings incomplete"})
                continue
            candidates.append({
                "schemaVersion": 1,
                "source": source_identity(record, path_id),
                "director": {
                    "source": source_identity(*target),
                    "enabled": bool(director["m_Enabled"]),
                    "initialState": director["m_InitialState"],
                    "wrapMode": director["m_WrapMode"],
                    "updateMode": director["m_DirectorUpdateMode"],
                    "initialTime": float(director["m_InitialTime"]),
                    "sceneBindings": director.get("m_SceneBindings") or [],
                    "exposedReferences": (director.get("m_ExposedReferences") or {}).get("m_References") or [],
                },
            })
    if len(candidates) != 1 or gaps:
        gaps.append({"reason": "environment controller identity is not uniquely resolved",
                     "candidates": len(candidates), "package": ENVIRONMENT_COMMON_PACKAGE})
        return None, gaps
    return candidates[0], []
