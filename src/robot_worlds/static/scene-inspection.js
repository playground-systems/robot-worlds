/** Geometry-only view helpers. Never mutate the world or simulator state. */
const dot = (a, b) => a.reduce((sum, value, i) => sum + value * b[i], 0);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const unit = value => { const length = Math.hypot(...value); if (!length) throw new Error('View direction must be nonzero'); return value.map(x => x / length); };

export function boundsCorners({ min, max }) {
  return [0, 1].flatMap(x => [0, 1].flatMap(y => [0, 1].map(z => [x ? max[0] : min[0], y ? max[1] : min[1], z ? max[2] : min[2]])));
}

export function objectBounds(object) {
  const p = object.position || [0, 0, 0], size = object.size || [.2, .2, .2];
  let [w, x, y, z] = object.quaternion || [Math.cos((object.yaw || 0)/2), 0, 0, Math.sin((object.yaw || 0)/2)];
  const length = Math.hypot(w, x, y, z) || 1; w /= length; x /= length; y /= length; z /= length;
  const rotation = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)], [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)], [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]];
  const extent = rotation.map(row => row.reduce((sum, value, i) => sum + Math.abs(value)*size[i]/2, 0));
  return { min: p.map((value, i) => value-extent[i]), max: p.map((value, i) => value+extent[i]) };
}

export function sceneBounds(spec) {
  const [width, depth, height] = spec.bounds || [6, 6, 3];
  const bounds = { min: [-width/2, -depth/2, 0], max: [width/2, depth/2, height] };
  for (const object of spec.objects || []) {
    const box = objectBounds(object);
    for (let i = 0; i < 3; i++) { bounds.min[i] = Math.min(bounds.min[i], box.min[i]); bounds.max[i] = Math.max(bounds.max[i], box.max[i]); }
  }
  return bounds;
}

/** Fit every corner inside both frustum axes, including depth and screen aspect. */
export function perspectiveFit(bounds, aspect, fovDegrees = 42, direction = [.8, -.9, .85], padding = 1.18) {
  if (!(aspect > 0) || !Number.isFinite(aspect) || !(fovDegrees > 0 && fovDegrees < 179)) throw new Error('Invalid perspective viewport');
  const target = bounds.min.map((value, i) => (value+bounds.max[i])/2), backward = unit(direction);
  const right = unit(cross(Math.abs(backward[2]) > .9999 ? [0, 1, 0] : [0, 0, 1], backward));
  const up = unit(cross(backward, right));
  const tanV = Math.tan(fovDegrees*Math.PI/360), tanH = tanV*aspect;
  const corners = boundsCorners(bounds).map(point => point.map((value, i) => value-target[i]));
  const radius = Math.max(.1, ...corners.map(point => Math.hypot(...point)));
  const distance = Math.max(radius*.1, ...corners.map(point => dot(point, backward) + padding*Math.max(Math.abs(dot(point, right))/tanH, Math.abs(dot(point, up))/tanV)));
  const depths = corners.map(point => distance-dot(point, backward));
  const maxDistance = Math.max(distance*5, radius*10);
  return { target, position: target.map((value, i) => value+backward[i]*distance),
    near: Math.max(.001, Math.min(.1, Math.min(...depths)*.1)), far: Math.max(maxDistance, ...depths)+radius*3,
    distance, maxDistance, right, up, backward };
}

/** A source appearance may be hidden only after its entire ancestry is fixed. */
function hasFixedAncestry(object, objects) {
  if (!object.parent_id || !Array.isArray(objects)) return false;
  const byId = new Map(), duplicateIds = new Set();
  for (const member of objects) {
    if (byId.has(member.id)) duplicateIds.add(member.id);
    byId.set(member.id, member);
  }
  const seen = new Set([object.id]);
  let current = object;
  while (current.parent_id) {
    if (seen.has(current.parent_id) || duplicateIds.has(current.parent_id)) return false;
    seen.add(current.parent_id);
    current = byId.get(current.parent_id);
    if (!current || current.movable || current.joint) return false;
  }
  return true;
}

/** Conservative visual inference, not semantic truth or a collision modification. */
export function isInspectionShell(object, bounds = [6, 6, 3], objects = undefined) {
  if (object.movable || object.joint) return false;
  const [x, y, z] = object.size || [0, 0, 0], position = object.position || [0, 0, 0];
  const name = `${object.id || ''} ${object.label || ''}`.toLowerCase();
  const roofShape = z <= .6 && x >= 2 && y >= 2 && position[2] >= 1.5;
  const namedRoof = /(?:^|[\s_-])(roof|ceiling)(?:$|[\s_\d-])/.test(name);
  // Imported source ceilings are visual children of their physical mesh. Keep
  // this exception limited to broad, thin named overhead appearance surfaces;
  // source wall meshes and ordinary props remain visible as spatial context.
  if (object.kind === 'mesh') return object.collision === false && roofShape && namedRoof
    && hasFixedAncestry(object, objects);
  if (object.parent_id || object.kind !== 'box') return false;
  const wallShape = Math.min(x, y) <= .6 && Math.max(x, y) >= 1.5 && z >= 1.4 && Math.max(x, y) >= 4*Math.min(x, y);
  const boundary = x < y ? Math.abs(Math.abs(position[0])-bounds[0]/2) <= Math.max(x/2, .6) : Math.abs(Math.abs(position[1])-bounds[1]/2) <= Math.max(y/2, .6);
  const namedWall = /(?:^|[\s_-])(wall|partition|facade)(?:$|[\s_\d-])/.test(name);
  const overheadBeam = /(?:^|[\s_-])(beam|joist)(?:$|[\s_\d-])/.test(name)
    && position[2] >= Math.max(2.2, bounds[2]*.65) && z <= .6
    && Math.max(x, y) >= Math.max(bounds[0], bounds[1])*.35;
  return (wallShape && (boundary || namedWall)) || (roofShape && namedRoof) || overheadBeam;
}

/** Start enclosed scenes with their interior visible; the user can restore walls. */
export function initialCutaway(spec) {
  const shells = (spec.objects || []).filter(object => object.visible !== false && isInspectionShell(object, spec.bounds, spec.objects));
  return shells.length >= 3 || shells.some(object => (object.size?.[0] || 0) >= 2
    && (object.size?.[1] || 0) >= 2 && (object.size?.[2] || 0) <= .6);
}

/** Retain far boundary walls as context while removing the enclosure in front. */
export function hideInspectionShell(object, bounds, cameraPosition) {
  const [x, y, z] = object.size, p = object.position;
  if (z <= .6) return true; // roof/overhead beam already classified as a shell
  const axis = x < y ? 0 : 1;
  if (Math.abs(p[axis]) < bounds[axis]*.2) return true; // internal partition
  return p[axis]*cameraPosition[axis] > 0;
}
