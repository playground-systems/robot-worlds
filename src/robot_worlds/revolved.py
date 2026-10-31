"""Closed radial profiles become hollow surfaces and convex physical wedges.

Profiles use normalized (radius, height) coordinates. Triangulation preserves
concavity instead of replacing a bowl's empty interior with one convex hull.
"""
from __future__ import annotations

import math

MAX_SCENE_COLLIDERS = 20_000


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _on_segment(a, b, p):
    return abs(cross(a, b, p)) < 1e-10 and all(min(a[i], b[i])-1e-10 <= p[i] <= max(a[i], b[i])+1e-10 for i in (0, 1))


def _intersects(a, b, c, d):
    return (cross(a, b, c)*cross(a, b, d) < 0 and cross(c, d, a)*cross(c, d, b) < 0) or any(
        (_on_segment(a, b, c), _on_segment(a, b, d), _on_segment(c, d, a), _on_segment(c, d, b)))


def triangulate(profile):
    """Validate a simple profile and return CCW triangles, including concave ones."""
    points = [tuple(p) for p in profile]
    if not 3 <= len(points) <= 12 or any(len(p) != 2 for p in points):
        raise ValueError("revolved profile needs 3–12 radius/height pairs")
    if any(not all(math.isfinite(v) for v in p) or not (0 <= p[0] <= .5 and -.5 <= p[1] <= .5) for p in points):
        raise ValueError("profile radii must be in [0,0.5] and heights in [-0.5,0.5]")
    if any(math.dist(points[i], points[(i+1) % len(points)]) < 1e-8 for i in range(len(points))):
        raise ValueError("profile has duplicate consecutive points; closure is implicit")
    for i in range(len(points)):
        for j in range(i+1, len(points)):
            if j == i+1 or (i == 0 and j == len(points)-1):
                continue
            if _intersects(points[i], points[(i+1) % len(points)], points[j], points[(j+1) % len(points)]):
                raise ValueError("profile must not intersect or touch itself")
    if abs(max(p[0] for p in points)-.5) > 1e-8 or abs(min(p[1] for p in points)+.5) > 1e-8 or abs(max(p[1] for p in points)-.5) > 1e-8:
        raise ValueError("profile must span radius 0.5 and height -0.5 to 0.5 so size matches physical bounds")
    area = sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(points, points[1:]+points[:1])) / 2
    if abs(area) < 1e-6:
        raise ValueError("profile must enclose nonzero material area")
    if area < 0:
        points.reverse()
    # Removing collinear vertices preserves the material polygon exactly.
    points = [p for i, p in enumerate(points) if abs(cross(points[i-1], p, points[(i+1) % len(points)])) > 1e-10]
    triangles = []
    while len(points) > 3:
        for i, point in enumerate(points):
            a, b, c = points[i-1], point, points[(i+1) % len(points)]
            if cross(a, b, c) <= 1e-10:
                continue
            if any(all(cross(x, y, p) >= -1e-10 for x, y in ((a, b), (b, c), (c, a)))
                   for p in points if p not in (a, b, c)):
                continue
            triangles.append((a, b, c))
            points.pop(i)
            break
        else:
            raise ValueError("profile could not be triangulated without filling its cavity")
    triangles.append(tuple(points))
    return triangles


def collision_count(obj):
    if obj.kind != "revolved":
        return 1
    return obj.revolved.segments * len(triangulate(obj.revolved.profile))


def intersects_box(shape, box, epsilon=1e-5):
    """Conservative intersection with an AABB, preserving the profile's cavity.

    Radial bounds include the inward chord of each collision facet. Using another
    shape's AABB can over-report overlap, but cannot excuse a real penetration.
    """
    delta = [box.position[i]-shape.position[i] for i in range(3)]
    half = [s/2 for s in box.size]
    rmin = math.hypot(*(max(abs(delta[i])-half[i], 0) for i in (0, 1)))
    rmax = math.hypot(*(abs(delta[i])+half[i] for i in (0, 1)))
    low, high = delta[2]-half[2], delta[2]+half[2]
    for triangle in triangulate(shape.revolved.profile):
        points = [(r*shape.size[0], z*shape.size[2]) for r, z in triangle]
        bottom, top = max(low, min(p[1] for p in points)), min(high, max(p[1] for p in points))
        if top-bottom <= epsilon:
            continue
        radii = [r for r, z in points if bottom <= z <= top]
        for a, b in zip(points, points[1:]+points[:1]):
            if abs(b[1]-a[1]) <= 1e-12:
                continue
            for height in (bottom, top):
                t = (height-a[1])/(b[1]-a[1])
                if 0 <= t <= 1:
                    radii.append(a[0] + t*(b[0]-a[0]))
        inner = min(radii) * math.cos(math.pi/shape.revolved.segments) if radii else 0
        if radii and min(max(radii), rmax)-max(inner, rmin) > epsilon:
            return True
    return False


def wedges(profile, segments, size):
    """Each triangle swept through one angular sector is a convex polyhedron."""
    triangles = triangulate(profile)
    for a, b, c in triangles:
        volume = cross(a, b, c)/2 * (a[0]+b[0]+c[0])/3 * math.sin(2*math.pi/segments) * size[0]**2 * size[2]
        for sector in range(segments):
            points = []
            for angle in (2*math.pi*sector/segments, 2*math.pi*(sector+1)/segments):
                for r, z in (a, b, c):
                    point = (r*size[0]*math.cos(angle), r*size[1]*math.sin(angle), z*size[2])
                    if point not in points:
                        points.append(point)
            yield points, volume
