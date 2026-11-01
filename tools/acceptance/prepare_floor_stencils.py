"""Create reusable architectural lettering, not object-asset replacements.

The output manifest can be appended to any generation prompt as explicitly
supplied local resources. Layout and placement are left to the generator.
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MESH = """v -0.5 -0.5 -0.5
v 0.5 -0.5 -0.5
v 0.5 0.5 -0.5
v -0.5 0.5 -0.5
v -0.5 -0.5 0.5
v 0.5 -0.5 0.5
v 0.5 0.5 0.5
v -0.5 0.5 0.5
vt 0 0
vt 1 0
vt 1 1
vt 0 1
f 1 3 2
f 1 4 3
f 5/1 6/2 7/3
f 5/1 7/3 8/4
f 1 2 6
f 1 6 5
f 2 3 7
f 2 7 6
f 3 4 8
f 3 8 7
f 4 1 5
f 4 5 8
"""


def prepare(output, labels):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    mesh = output/'stencil.obj'
    mesh.write_text(MESH)
    notice = output/'ATTRIBUTION.md'
    notice.write_text('# Architectural floor lettering\n\n'
                      'Original Robot Worlds layout graphics, MIT licensed under the repository LICENSE.\n'
                      'Rendered with Pillow default Aileron font (SIL Open Font License).\n'
                      'These are supplied architectural markings, not online-sourced object models.\n'
                      'No collision, equipment behavior or lighting is implied.\n')
    textures = {}
    for index, label in enumerate(labels):
        image = Image.new('RGB', (1024, 192), (38, 43, 48))
        draw = ImageDraw.Draw(image)
        size = 112
        while draw.textlength(label, font=ImageFont.load_default(size=size)) > 950:
            size -= 2
        draw.text((512, 96), label, font=ImageFont.load_default(size=size), anchor='mm', fill=(240, 242, 240))
        path = output/f'stencil-{index}.png'
        image.save(path)
        textures[label] = str(path)
    record = {'kind': 'supplied architectural graphics', 'mesh_path': str(mesh),
              'source_notice': str(notice), 'texture_paths': textures,
              'instructions': 'Use kind=mesh, collision=false, movable=false, color=[1,1,1], size about [3,0.55,0.002]. '
                              'Place above the floor finish without overlap or z-fighting; orient with yaw. '
                              'Top-face lettering reads from negative Y at yaw=0. Use only at meaningful work areas; '
                              'these are nonphysical markings, never supports or rubric targets.',
              'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}}
    (output/'manifest.json').write_text(json.dumps(record, indent=2)+'\n')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('labels', nargs='+')
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, args.labels), indent=2))
