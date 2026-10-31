# Source material preservation

Mesh objects can carry an explicit `surface` (`SurfaceMaterial`) with metallic,
roughness, emissive, normal-scale, sidedness, alpha and wrapping parameters.
`color` and `opacity` remain the base color factors. These fields do not set mass,
friction, contact or motion properties.

The image slots share the prepared mesh UV coordinates:

| Field | Interpretation | Browser color space |
| --- | --- | --- |
| `texture_path` | Base color, with optional alpha | sRGB |
| `normal_texture_path` | Tangent-space normal | Linear data |
| `orm_texture_path` | Red occlusion, green roughness, blue metallic | Linear data |
| `emissive_texture_path` | Emission color | sRGB |

Maps are bounded local PNG assets. Source importers must convert embedded images
and texture-coordinate transforms before registering an asset; declaring a path
does not make unsupported glTF features work. Different UV sets or sampler
settings across source texture slots need conversion or an explicit rejection,
not silent substitution. Nonuniform vertex colors still need preservation during
visual artifact conversion.

Asset libraries verify map hashes alongside mesh hashes. Frozen worlds copy all
four maps into their own content-addressed asset directory, so source removal
does not break loading/export. Changed map bytes invalidate a saved world's
integrity checks. Targeted surface edits retain existing dynamics and require
normal world revalidation because appearance affects policy observations.

Within a displayed world, repeated OBJ instances share parsed geometry,
materials and decoded maps. Position, size, rotation and selection stay per
object. Texture sharing respects color space and wrapping, and different
material factors keep separate materials. Changing worlds aborts pending loads
and releases the scene-owned resources. The viewer still starts at most four
object loads at once; this does not yet batch OBJ draw calls or supply LODs.

Mesh placement supports `mesh_quarter_turns` (0–3) around Z without rewriting
source files. `size` describes the full extents after that turn. Prepared assets
and assembly instances set this field while rotating their physical parts and
joint/cavity frames together. Browser and native XML apply the same source
centering, scale and rotation; analytical checks cover off-center source meshes
and composed body poses. Native contact/render validation remains pending.

The browser uses the PBR map channels. MJCF export retains material layers and
coefficients, with shared texture/material definitions. The current runtime uses
MuJoCo's classic renderer; it approximates highlights and does not reproduce all
PBR maps, alpha masking, emission or sampler settings. Frozen-world assumptions
state this difference. MuJoCo also provides a separate [Filament rendering
API](https://mujoco.readthedocs.io/en/3.17.0/python.html#filament-rendering), which
is not yet integrated into the policy camera or replay path.

Verification so far is lightweight: schema validation, library/world persistence,
source removal, hash tampering, scoped edits, XML material sharing, channel/color
space selection and texture cleanup. A small real-Three test loads 64 repeated
fixtures with one OBJ request and three map requests, checks independent bounds
and selection IDs, and verifies disposal on replacement or late completion.
This is CPU-side evidence, not measured GPU memory or viewport frame rate.
No new browser screenshot, native material
compilation or PBR policy-camera parity is established by these checks. The
warehouse visual acceptance gate remains open.
