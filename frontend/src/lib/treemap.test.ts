import assert from 'node:assert/strict';
import { test } from 'node:test';
import { treemapLayout, type TreemapInput } from './treemap.ts';

function stand(section: string, name: string, cans: { name: string; value: number }[]): TreemapInput {
  const children = cans.map((c) => ({
    name: c.name,
    value: c.value,
    section,
    stand: name,
  }));
  return {
    name,
    section,
    stand: name,
    value: children.reduce((s, c) => s + c.value, 0),
    children,
  };
}

function section(name: string, stands: TreemapInput[]): TreemapInput {
  return {
    name,
    section: name,
    value: stands.reduce((s, d) => s + d.value, 0),
    children: stands,
  };
}

// Mirrors the demo burn treemap: Casals dominates, Demo is a thin column,
// Website is a short cell under it.
function demoTree(): TreemapInput {
  return {
    name: 'root',
    value: 0,
    children: [
      section('Casals', [stand('Casals', 'conductor', [
        { name: 'casals-backend', value: 299 },
        { name: 'casals-store', value: 83 },
        { name: 'casals-frontend', value: 12 },
      ])]),
      section('Demo', [stand('Demo', 'Motoko', [
        { name: 'motoko-backend', value: 8 },
        { name: 'motoko-frontend', value: 4 },
        { name: 'motoko-baton', value: 2 },
      ])]),
      section('Website', [stand('Website', 'Website', [
        { name: 'website', value: 1 },
      ])]),
    ],
  };
}

test('a short section still fills with its canister', () => {
  const rects = treemapLayout(demoTree(), 760, 360);
  const frame = rects.find((r) => r.depth === 1 && r.name === 'Website');
  const tile = rects.find((r) => r.depth === 3 && r.name === 'website');
  assert.ok(frame, 'website section frame');
  assert.ok(tile, 'website canister tile');
  assert.ok(frame.w > 0 && frame.h > 0);
  assert.ok(tile.h >= frame.h * 0.55, `tile h ${tile.h} vs frame h ${frame.h}`);
  assert.ok(tile.w >= frame.w * 0.55, `tile w ${tile.w} vs frame w ${frame.w}`);
  assert.ok(tile.x >= frame.x && tile.y >= frame.y);
  assert.ok(tile.x + tile.w <= frame.x + frame.w + 0.01);
  assert.ok(tile.y + tile.h <= frame.y + frame.h + 0.01);
});

test('a large section keeps a header gutter above its tiles', () => {
  const rects = treemapLayout(demoTree(), 760, 360);
  const frame = rects.find((r) => r.depth === 1 && r.name === 'Casals');
  const tile = rects.find((r) => r.depth === 3 && r.name === 'casals-backend');
  assert.ok(frame && tile);
  assert.ok(tile.y >= frame.y + 10, `tile y ${tile.y} frame y ${frame.y}`);
});
