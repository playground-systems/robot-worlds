"""Exact, bounded source joint contact exclusions shared by static/native checks."""

MAX_JOINT_COLLISION_EXCLUSIONS = 40_000


def joint_collision_exclusions(objects, *, max_pairs=MAX_JOINT_COLLISION_EXCLUSIONS):
    """Return object pairs spanning only each excluded joint's two rigid groups.

    Components have separate native bodies, even when welded together. A joint's
    parent group includes welded ancestors/siblings; its child group stops at
    every other joint or freely moving root. Appearance-only components need no
    exclusion. Unrelated world, robot, workpiece and other-joint contacts remain.
    """
    excluded_joints = [obj for obj in objects if obj.joint and not obj.joint.collide_parent]
    if not excluded_joints:
        return []
    if isinstance(max_pairs, bool) or not isinstance(max_pairs, int) or max_pairs < 1:
        raise ValueError("Joint collision exclusion budget must be a positive integer")
    by_id = {obj.id: obj for obj in objects}
    if len(by_id) != len(objects):
        raise ValueError("Joint collision exclusions require distinct object IDs")
    roots = {}

    def rigid_root(identifier):
        trail, seen = [], set()
        while identifier not in roots:
            if identifier in seen:
                raise ValueError("Cyclic rigid assembly in joint collision exclusion")
            seen.add(identifier)
            trail.append(identifier)
            obj = by_id[identifier]
            if obj.joint or obj.movable or obj.parent_id is None:
                roots[identifier] = identifier
                break
            if obj.parent_id not in by_id:
                raise ValueError("Joint collision exclusion has an unknown parent: " + obj.parent_id)
            identifier = obj.parent_id
        root = roots[identifier]
        for member in trail:
            roots[member] = root
        return root

    groups = {}
    for obj in objects:
        root = rigid_root(obj.id)
        if obj.collision:
            groups.setdefault(root, set()).add(obj.id)
    pairs = set()
    for obj in excluded_joints:
        if obj.parent_id is None or obj.parent_id not in by_id:
            raise ValueError("collide_parent=False requires an explicit scene parent: " + obj.id)
        parent_root = rigid_root(obj.parent_id)
        if parent_root == obj.id:
            raise ValueError("Cyclic joint parent in collision exclusion: " + obj.id)
        parent, child = groups.get(parent_root, set()), groups.get(obj.id, set())
        if len(pairs) + len(parent) * len(child) > max_pairs:
            raise ValueError(f"Joint collision exclusions exceed the {max_pairs} body-pair budget; no contacts were discarded")
        pairs.update(tuple(sorted((first, second))) for first in parent for second in child)
    return sorted(pairs)
