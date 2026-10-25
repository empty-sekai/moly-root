"""Small per-content display documents, with a bounded two-level index.

The complete source registry stays in editorial storage. A player reads the
root, one routing bucket and the selected script's display/overlay documents.
Each script includes its linked tweets so a session fixes both together.
"""
from collections import defaultdict

from core.content import canonical_bytes, content_hash, document_hash

MAX_SHARD_BYTES = 2 * 1024 * 1024


def build_product(source, overlays=()):
    if source.get("schema") != "moly-text-source/1":
        raise ValueError("unsupported text source schema")
    scripts = {row["key"]: row for row in source["scripts"]}
    if len(scripts) != len(source["scripts"]):
        raise ValueError("duplicate text script identity")
    all_lines = {line["id"]: line for row in scripts.values() for line in row["lines"]}
    if len(all_lines) != sum(len(row["lines"]) for row in scripts.values()):
        raise ValueError("duplicate text line identity")
    locales = defaultdict(dict)
    for overlay in overlays:
        if (overlay.get("schema") != "moly-text-overlay/1"
                or overlay.get("sourceRegion") != source["region"]
                or overlay.get("sourceRevision") != source["revision"]):
            raise ValueError("overlay does not belong to this source revision")
        for entry in overlay["entries"]:
            line = all_lines.get(entry["id"])
            if line is None or entry["sourceHash"] != line["sourceHash"]:
                raise ValueError("overlay refers to a missing or changed source line")
            if entry["id"] in locales[overlay["locale"]]:
                raise ValueError("duplicate overlay line for the same locale")
            locales[overlay["locale"]][entry["id"]] = entry
    files, buckets = {}, defaultdict(dict)
    translations = {locale: defaultdict(dict) for locale in locales}

    def store(document, prefix="text"):
        data = canonical_bytes(document)
        if len(data) > MAX_SHARD_BYTES:
            raise ValueError("text shard exceeds the runtime document limit")
        digest = content_hash(data)
        path = f"{prefix}/objects/{digest}.json"
        files[path] = data
        return {"path": f"objects/{digest}.json", "sha256": digest, "bytes": len(data)}

    for key, script in sorted(scripts.items()):
        tweet_keys = {c["tweetKey"] for c in script["contents"] if c.get("tweetKey")}
        if tweet_keys - scripts.keys():
            raise ValueError("missing linked tweet source")
        selected = [script] + [scripts[k] for k in sorted(tweet_keys) if k != key]
        display = {"schema": "moly-text-source/1", "region": source["region"],
                   "scripts": [{"key": row["key"], "lines": [
                       {field: line[field] for field in ("id", "kind", "step", "text", "sourceHash")}
                       for line in row["lines"]]} for row in selected]}
        display["revision"] = document_hash(display)
        route = {"source": store(display)}
        bucket = content_hash(key.encode("utf-8"))[:2]
        ids = {line["id"] for row in selected for line in row["lines"]}
        for locale, entries in sorted(locales.items()):
            # Donor/global revision provenance stays in the source registry
            # and root. A change to another conversation must not churn this
            # immutable per-script payload.
            selected_entries = [{field: entries[identity][field]
                                 for field in ("id", "sourceHash", "text", "source")}
                                for identity in sorted(ids & entries.keys())]
            if not selected_entries:
                continue
            doc = {"schema": "moly-text-overlay/1", "sourceRegion": source["region"],
                   "sourceRevision": display["revision"], "locale": locale, "entries": selected_entries}
            doc["revision"] = document_hash(doc)
            prefix = f"translations/{source['region']}/{locale}"
            ref = store(doc, prefix)
            translations[locale][bucket][key] = {**ref, "path": ref["path"].removeprefix(prefix + "/")}
        buckets[bucket][key] = route
    root = {"schema": "moly-text-runtime/1", "region": source["region"],
            "sourceRevision": source["revision"], "routing": "sha256-utf8-prefix-2",
            "buckets": {}}
    for bucket, routes in sorted(buckets.items()):
        root["buckets"][bucket] = store({"schema": "moly-text-routes/1", "scripts": routes})
    root["revision"] = document_hash(root)
    files["text/index.json"] = canonical_bytes(root)
    for locale, translation_buckets in sorted(translations.items()):
        prefix = f"translations/{source['region']}/{locale}"
        publication = {"schema": "moly-text-publication/1", "region": source["region"],
                       "locale": locale, "routing": "sha256-utf8-prefix-2", "buckets": {}}
        for bucket, routes in sorted(translation_buckets.items()):
            ref = store({"schema": "moly-text-translation-routes/1", "scripts": routes}, prefix)
            publication["buckets"][bucket] = {**ref, "path": ref["path"].removeprefix(prefix + "/")}
        publication["revision"] = document_hash(publication)
        data = canonical_bytes(publication)
        files[f"{prefix}/revisions/{publication['revision']}.json"] = data
        files[f"{prefix}/latest.json"] = data
    return files
