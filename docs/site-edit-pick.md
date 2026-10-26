# Site editor picking surface

The site editor raycasts the scene colliders tagged `RaycastTargetGroup`.
Export them from the scene bundle and its matching client APK:

```sh
moly export site-edit-pick \
  --apk client.apk \
  --bundle decrypted/mysekai__site__field__home \
  --out assets/site/edit-pick/home.json
```

`--apk` can be repeated for split APKs. The APK must supply
`assets/bin/Data/globalgamemanagers`; its `TagManager.tags` determines the numeric
tag. Use the APK from the same client version as the bundle. The exporter records
the source bundle hash, TagManager file hash and object identifiers; these prove
the bytes supplied but do not infer whether two independently supplied inputs
belong to the same release.

The version 1 sidecar retains each tagged MeshCollider's enabled, trigger and
convex flags, GameObject layer, root-to-leaf active flags and local TRS, authored
vertex positions and triangle winding. Coordinates stay Unity left-handed,
Y-up; no scene placement, reflection, visual geometry or navigation mesh is
substituted. Triangle indices include each submesh's base vertex.

Version 1 requires MeshCollider triangle geometry and local pointers in the
collider's serialized file. Missing tags, unresolved pointers, other collider
types, unsupported topology or invalid geometry fail before replacing an
existing output. A disabled collider or inactive ancestor remains represented
with its source flag; the runtime decides whether it can be hit.

Add the resulting `site/edit-pick/<scene>.json` to the resource manifest and use
the existing resource publication workflow. This command only writes the
explicit output file; it does not change a source snapshot or upload resources.
