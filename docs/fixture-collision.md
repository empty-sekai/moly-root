# Fixture collision export

`fixtures.meshes` embeds a versioned collision graph alongside each visual GLB.
The document and each scene root carry `extras.fixtureCollision`; original
transform nodes carry `extras.sourceCollision`. Collision geometry is never
attached to a render node merely to make it available.

Both records name `coordinateContract: "moly-rh-y-up-reflect-x-v1"` and
`units: "source-unity-unit"`. Positions, shape centers and mesh vertices reflect
source X. Triangles reverse winding. Node-local TRS is the same canonical TRS as
the visual hierarchy. Runtime consumers apply the node's live world transform
once; they must not mirror collision geometry again. Collider extents and sizes
remain positive source magnitudes. UV origin is independently tagged and is not
a spatial-basis choice.

## Records

`fixtureCollision.geometry` is a deduplicated array keyed by `geometryId`.
Each entry contains exact source `(file, pathId)`, canonical `positions`, and
`triangles`. It contains the source input mesh, **not a Unity-cooked convex hull**.
A `MeshCollider` names this table through `geometryId`. A collider-only mesh is
included even when no renderer references it.

`sourceCollision` records:

- `activeSelf` and `activeInHierarchy`, independent of renderer visibility;
- `authoredLayer` and `fixtureNavLayer` (the original FixtureView recursively
  assigns `MysekaiNavMeshBuildTarget` during setup);
- serialized `colliders`, with identity, type, enabled/trigger controls, shape
  values, convex/cooking controls, collision masks, and exact mesh pointer;
- `modifiers`, including enabled, affected agent IDs, ignore-from-build and area;
- `navMeshAgents` and `hasNavMeshAgent` separately;
- `navMeshObstacles`, including shape, extents, center and carving controls.

Unknown serialized values stay `null`. Unreadable/unresolved mesh references
produce both a collider gap and a document gap. Consumers must not turn such a
gap into an empty obstacle or substitute the visual mesh silently.

## Runtime-derived fallback

The exporter follows the CN 6.0.0 `FixtureView.AttachComponents` fallback:

1. Recursively assign the fixture navigation layer.
2. A view name containing case-sensitive `fence` skips the collider fallback.
3. In source hierarchy child order, select the first active descendant Renderer
   whose GameObject name equals the FixtureView GameObject name.
4. Preserve any MeshCollider already on that renderer GameObject, even if it is
   disabled. Otherwise record a derived convex MeshCollider using its shared
   mesh, plus the default NavMeshModifier.

Derived records have `origin: "fixture-view-fallback"`, no invented component
ID, and the source renderer ID. The game does not set cooking options in this
branch, so those remain unknown instead of copying another collider's value.

## Source evidence and limits

The implementation was checked against these original method bodies:

- CN 6.0.0 `sekai/Mysekai/NavMeshField.c`, initialization around lines 1578–1599:
  `CollectObjects.Volume`, the named navigation layer, and `PhysicsColliders`.
- CN 6.0.0 `sekai/Mysekai/FixtureView.c`, `AttachComponents` RVA `0x5A35298`,
  around lines 4352–4497, and its renderer-name predicate RVA `0x5A39DA0`.
- JP 6.7.0 `NavMeshComponents.dll/UnityEngine/AI/NavMeshSurface.c`:
  `CollectSources` forwards NavMeshModifier agent/area/ignore controls and
  separately removes sources with a same-GameObject NavMeshAgent/NavMeshObstacle.

The record deliberately declares `nativeParity: false`. Unity convex cooking,
native primitive tessellation and exact `CollectSources` boundary behavior are
not reproduced by merely transporting their inputs. The distinct
`FixtureController` branch that creates a missing FixtureView is also not
materialized from a prefab-only graph. A runtime implementing any approximation
must identify it separately from these exported source inputs.
