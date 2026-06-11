"""Render headless de esqueleto (armature GLB) via bpy — QA visual de rigging.

Cada osso vira um octaedro esticado head→tail (armatures não renderizam no
Cycles). Duas vistas: frente e 3/4. Uso:

    python scripts/render_skeleton.py skeleton.glb /tmp/render_skel
"""

import math
import sys

import bpy
from mathutils import Matrix, Vector

glb_path, out_prefix = sys.argv[-2], sys.argv[-1]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb_path)

bones = []
for obj in bpy.context.scene.objects:
    if obj.type == "ARMATURE":
        for b in obj.data.bones:
            head = obj.matrix_world @ b.head_local
            tail = obj.matrix_world @ b.tail_local
            bones.append((b.name, Vector(head), Vector(tail)))

if not bones:
    print("ERRO: nenhum armature no GLB")
    sys.exit(1)
print(f"{len(bones)} ossos")

# remover meshes importadas — só os ossos interessam no render
for obj in list(bpy.context.scene.objects):
    if obj.type == "MESH":
        bpy.data.objects.remove(obj, do_unlink=True)

mat = bpy.data.materials.new("bone")
mat.use_nodes = True
mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.95, 0.75, 0.2, 1.0)

for name, head, tail in bones:
    vec = tail - head
    length = max(vec.length, 1e-5)
    bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=0.012, radius2=0.002, depth=1.0)
    o = bpy.context.active_object
    o.name = f"bone_{name}"
    o.data.materials.append(mat)
    quat = vec.to_track_quat("Z", "Y")
    o.matrix_world = Matrix.Translation(head + vec / 2) @ quat.to_matrix().to_4x4() @ Matrix.Diagonal((1, 1, length, 1))

mins = [1e9] * 3
maxs = [-1e9] * 3
for _, h, t in bones:
    for p in (h, t):
        for i in range(3):
            mins[i] = min(mins[i], p[i])
            maxs[i] = max(maxs[i], p[i])
center = [(a + b) / 2 for a, b in zip(mins, maxs, strict=True)]
size = max(max(b - a for a, b in zip(mins, maxs, strict=True)), 1e-3)

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 8
scene.cycles.device = "CPU"
scene.render.resolution_x = 512
scene.render.resolution_y = 640
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.12, 0.12, 0.14, 1)
scene.world = world

sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", type="SUN"))
sun.data.energy = 4.0
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam)
scene.camera = cam

views = {"front": math.radians(0), "34": math.radians(40)}
for tag, yaw in views.items():
    dist = size * 2.0
    cam.location = (
        center[0] + dist * math.sin(yaw),
        center[1] - dist * math.cos(yaw),
        center[2] + size * 0.15,
    )
    direction = Vector(center) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{out_prefix}_{tag}.png"
    bpy.ops.render.render(write_still=True)
    print("rendered", scene.render.filepath)
