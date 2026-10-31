/** Shape-specific batching and profiles, shared by layout inspection and tests. */
export function geometryKey(object) {
  return object.kind === 'revolved'
    ? `revolved:${JSON.stringify(object.revolved)}` : object.kind || 'box';
}

export function closedProfile(profile) {
  const points = profile.map(point => [...point]);
  const area = points.reduce((sum, p, i) => {
    const next = points[(i + 1) % points.length];
    return sum + p[0] * next[1] - p[1] * next[0];
  }, 0);
  if (area < 0) points.reverse();
  return [...points, [...points[0]]];
}
