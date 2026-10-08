import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { localFile, sourceRoster } from './export.mjs';
import { frameHeadPortrait } from '../../examples/viewer/portrait.js';
import * as THREE from '../../examples/viewer/three.module.min.js';

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'moly-head-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.writeFile(path.join(root, 'manifest.json'), JSON.stringify({ units: [
    { unit: '101', glb: 'sd_101.glb', rig: 'sd_101.rig.json' },
    { unit: '127', glb: 'sd_127.glb', rig: 'sd_127.rig.json' },
  ] }));
  await fs.writeFile(path.join(root, 'mysekai-fixtures.json'), JSON.stringify({ region: 'cn', gameVersion: '1.0' }));
  for (const code of [101, 127]) {
    await fs.writeFile(path.join(root, `sd_${code}.glb`), 'synthetic GLB');
    await fs.writeFile(path.join(root, `sd_${code}.rig.json`), JSON.stringify({ unitId: code === 101 ? null : 27 }));
  }
  return root;
}

test('roster preserves distinct variants and requires correct regional provenance', async t => {
  const root = await fixture(t);
  const all = await sourceRoster(root, 'cn');
  assert.deepEqual(all.rows.map(row => row.unit), [1, 27]);
  assert.equal(all.rosterCount, 2);
  assert.deepEqual((await sourceRoster(root, 'cn', '1.0', '27')).rows.map(row => row.unit), [27]);
  await assert.rejects(sourceRoster(root, 'jp'), /region/);
  await assert.rejects(sourceRoster(root, 'cn', '2.0'), /version/);
  await assert.rejects(sourceRoster(root, 'cn', undefined, '21'), /unit IDs/);
  await assert.rejects(sourceRoster(root, 'cn', undefined, '1,1'), /unique/);
});

test('source paths cannot escape their declared root', async t => {
  const root = await fixture(t);
  await assert.rejects(localFile(root, '../other.glb'), /relative/);
  await assert.rejects(localFile(root, '/other.glb'), /relative/);
  await assert.rejects(localFile(root, 'nested\\other.glb'), /relative/);
  assert.equal(await localFile(root, 'sd_101.glb'), await fs.realpath(path.join(root, 'sd_101.glb')));
});

test('duplicate rig identities fail instead of replacing another character', async t => {
  const root = await fixture(t);
  await fs.writeFile(path.join(root, 'sd_127.rig.json'), JSON.stringify({ unitId: 1 }));
  await assert.rejects(sourceRoster(root, 'cn'), /duplicate rig unit/);
});

test('head selection removes body and mixed triangles before camera framing', () => {
  const root = new THREE.Group();
  const shoulder = new THREE.Bone(); shoulder.name = 'Shoulder';
  const head = new THREE.Bone(); head.name = 'Head';
  const hair = new THREE.Bone(); hair.name = 'Hair';
  root.add(shoulder); shoulder.add(head); head.add(hair);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([
    -0.5, 1, 0, 0.5, 1, 0, 0, 2, 0, // skull
    -1, -1, 0, 1, -1, 0, 0, 0, 0, // body
    -0.2, -3, 0, 0.2, -3, 0, 0, 1, 0, // long hair
  ], 3));
  geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute([
    1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    2, 0, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0,
  ], 4));
  geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(Array.from({ length: 9 }, () => [1, 0, 0, 0]).flat(), 4));
  geometry.setIndex([0, 1, 2, 3, 4, 5, 0, 1, 5, 6, 7, 8]);
  const mesh = new THREE.SkinnedMesh(geometry, new THREE.MeshBasicMaterial());
  root.add(mesh); mesh.bind(new THREE.Skeleton([shoulder, head, hair]));
  const prop = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial()); root.add(prop);
  const camera = new THREE.PerspectiveCamera();
  const controls = { target: new THREE.Vector3(), update() {} };
  const result = frameHeadPortrait({ root, camera, controls });
  assert.deepEqual(Array.from(mesh.geometry.index.array), [0, 1, 2, 6, 7, 8]);
  assert.equal(prop.visible, false);
  assert.equal(result.meshes[0].keptTriangles, 2);
  assert.equal(result.geometryBounds.min[1], -3);
  assert.equal(result.bounds.min[1], 1); // long hair does not shrink the face framing
  assert.equal(camera.fov, 35);
  assert.equal(controls.enabled, false);
});
