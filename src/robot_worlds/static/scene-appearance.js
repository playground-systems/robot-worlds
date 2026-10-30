/** Estimated display finishes only; never infer friction, mass or collision state. */
const profiles = Object.freeze({
  matte: Object.freeze({ id: 'matte', metalness: .04, roughness: .72 }),
  architectural: Object.freeze({ id: 'architectural', metalness: 0, roughness: .9 }),
  metal: Object.freeze({ id: 'metal', metalness: .72, roughness: .34 }),
  painted: Object.freeze({ id: 'painted', metalness: .22, roughness: .43 }),
  polymer: Object.freeze({ id: 'polymer', metalness: 0, roughness: .5 }),
  rubber: Object.freeze({ id: 'rubber', metalness: 0, roughness: .86 }),
  cardboard: Object.freeze({ id: 'cardboard', metalness: 0, roughness: .97 }),
  wood: Object.freeze({ id: 'wood', metalness: 0, roughness: .78 }),
});
export const APPEARANCE_NOTICE = 'Surface finishes and studio lighting are visual estimates; physical properties are unchanged.';
export const APPEARANCE_PROFILE_COUNT = Object.keys(profiles).length;

export function estimatedAppearance(object) {
  const name = `${object.id || ''} ${object.label || ''}`.toLowerCase().replace(/[_-]/g, ' ');
  let id = 'matte';
  if (/\b(rubber|tire|tyre|gasket|conveyor belt)\b/.test(name)) id = 'rubber';
  else if (/\b(cardboard|carton|corrugated)\b/.test(name)) id = 'cardboard';
  else if (/\b(wood|wooden|timber|plywood)\b/.test(name)) id = 'wood';
  else if (/\b(plastic|polymer|acrylic|polycarbonate|tote|bin)\b/.test(name)) id = 'polymer';
  else if (/\b(wall|ceiling|concrete|masonry)\b/.test(name)) id = 'architectural';
  else if (/\b(painted|powder coated)\b/.test(name)) id = 'painted';
  else if (/\b(steel|stainless|aluminum|aluminium|metal|brass|copper)\b/.test(name)) {
    const color = object.color || [.5, .5, .5], max = Math.max(...color), min = Math.min(...color);
    id = /\b(painted|coated)\b/.test(name) || (max > 0 && (max-min)/max > .4) ? 'painted' : 'metal';
  } else if (/\b(rack|upright|rail|gantry)\b/.test(name)) id = 'painted';
  return { ...profiles[id], opacity: 1, estimated: true, basis: 'Name-based display heuristic; no physical material contract' };
}

/** Tiny neutral studio reflection map. No geometry, external texture or random seed. */
export function studioEnvironmentPixels() {
  const width = 64, height = 32, data = new Uint8Array(width*height*4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const u = x/width, v = y/(height-1), sky = .12 + .25*(1-v);
    const wrap = centre => Math.min(Math.abs(u-centre), 1-Math.abs(u-centre));
    const softbox = (centre, altitude, spread) => Math.exp(-((wrap(centre)/spread)**2 + ((v-altitude)/.2)**2));
    const key = softbox(.18, .3, .08)*.48, fill = softbox(.72, .4, .17)*.2;
    const index = (y*width+x)*4;
    for (let c = 0; c < 3; c++) data[index+c] = Math.round(255*Math.min(1, sky + key*[1, .97, .9][c] + fill*[.88, .94, 1][c]));
    data[index+3] = 255;
  }
  return { width, height, data };
}
