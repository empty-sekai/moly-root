"""Preserve typed PPtr locations before JSON erases tuple/list distinction.

UI hand decoders return a tuple only for Reader.pptr; numeric vectors and
arrays return lists. Native typetrees encode a PPtr as the named field pair.
This must run on decoded fields, never on already serialized JSON values.
"""


def collect_pointer_fields(value, *, decoded_pptr_tuples):
    paths = []

    def escape(key):
        return str(key).replace("~", "~0").replace("/", "~1")

    def visit(item, path):
        if isinstance(item, tuple) and decoded_pptr_tuples:
            if len(item) != 2 or not all(isinstance(v, int) for v in item):
                raise ValueError(f"unexpected non-PPtr tuple in decoded UI fields: {path}")
            paths.append(path)
        elif isinstance(item, dict):
            if set(item) == {"m_FileID", "m_PathID"}:
                paths.append(path)
            else:
                for key, child in item.items():
                    visit(child, f"{path}/{escape(key)}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, f"{path}/{index}")

    visit(value, "")
    return paths
