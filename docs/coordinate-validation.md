# Coordinate validation

`tools/validate_coordinate_contract.py` is read-only and delegates to the shared
`core.coordinate_contract` module. By default it validates only explicitly
supplied artifacts and GLBs named by a fixture index; the report names that
scope and its unverified boundaries.

```text
python tools/validate_coordinate_contract.py --index PATH/fixture-models/index.json --attach PATH/fixture-attach/attach-points.json
python tools/validate_coordinate_contract.py --snapshot PATH/COMPLETE-RUNTIME
```

The validator checks the canonical marker on the GLB asset and each scene root,
index/package declarations, attach-point schema, collision-table copies, and
HouseView/NavMeshObstacle subcontracts. Wherever sourceTransform/sourceGeometry
evidence exists it checks position reflection, quaternion conjugation and
unchanged scale/extents numerically. Relabelling authored numbers without
converting them fails validation. No file is rewritten or automatically tagged.

The `--snapshot` gate requires the fixture index and attach document, then checks
every publishable GLB (including actors, sites and motion libraries), producing
a sorted path/SHA-256 inventory. Hidden/evidence/backup paths are excluded, not
ordinary asset families. Missing legacy metadata fails closed; it is not a
license to relabel copied files. `prepare_runtime` writes a source-level
coordinate marker only after this gate succeeds, and saves the full read-only
receipt as `coordinate-validation.json`.

It does not prove Unity convex cooking, native navigation parity, or runtime
placement semantics. Source FixtureView.SetPosition/ForceSetRotation overwrite
the view root's authored T/R; exporters preserve the prefab evidence while the
runtime performs that placement override. Validating source-node conversions
cannot substitute for a runtime test comparing the drawn locator and actor.

Validate source bundles with nonzero authored root offsets as well as ordinary
placements. Full-snapshot regeneration and browser end-to-end checks remain
separate gates.

## Reviewed legacy producer migration

`tools/migrate_canonical_metadata.py` is a separate explicit migration, not part
of the validator. It only admits reviewed already-reflected character, actor,
avatar, harvest-tool and phenomena producer/path pairs. It pins every original
file SHA-256 before writing a disjoint overlay, verifies producer source and
spatial AST fingerprints embedded in the tool, and validates existing actor
routing hashes. The guards also work in source archives without Git history.
Receipts identify the reviewed producer set by `producerFingerprintSha256`;
they do not record machine paths. Characters additionally require their original explicit
`coordinates: unity x-axis reflected` evidence. Old fixture/custom-fixture/site
outputs are excluded and must be independently re-exported.

Each migrated file's receipt records source/output SHA, unchanged binary-chunk
SHA, and equality of the complete original JSON after removing only newly added
asset/root tags. `--verify-existing-overlay` recomputes that proof from original
and output bytes and writes structured machine-check fields. This establishes
metadata-only migration from the reviewed producer; it does **not** claim fresh
Unity mesh or animation numerical re-extraction. Exact old hash references in
JSON are updated transitively, while original path aliases stay stable.

Overlays never update `.gz` siblings: composition must regenerate or omit stale
compressed files. Derived portraits need the original model hash and a receipt
link retained alongside the new hash, rather than presenting the metadata change
as a new render.

Native sidecars use per-field tags where the original payload remains raw:
NavMesh placement, bounds and opaque tiles, site primitive collider dimensions,
and cloth's verbatim `clothParams`/structural checks are source-space evidence.
Height-mesh GLBs, cloth vertex bind poses and cloth collision geometry are
canonical. In particular, raw cloth `gravityDirection` is an explicitly recorded
legacy boundary; this metadata work does not alter cloth solver behavior.
