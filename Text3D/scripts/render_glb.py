"""Render headless de GLB via bpy (Cycles CPU) — QA visual de geração.

Duas vistas por mesh: 3/4 alto e rasante (a rasante expõe placas/pedestais
fundidos que métricas numéricas não apanham).

Uso:
    python scripts/render_glb.py modelo.glb /tmp/render_modelo
    # → /tmp/render_modelo_34.png e /tmp/render_modelo_low.png
"""

import math
import sys

import bpy
from mathutils import Vector

glb_path, out_prefix = sys.argv[-2], sys.argv[-1]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb_path)

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
mat = bpy.data.materials.new("white")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.9, 0.9, 0.9, 1.0)
bsdf.inputs["Roughness"].default_value = 0.8
for o in meshes:
    o.data.materials.clear()
    o.data.materials.append(mat)

mins = [1e9] * 3
maxs = [-1e9] * 3
for o in meshes:
    for corner in o.bound_box:
        wc = o.matrix_world @ Vector(corner)
        for i in range(3):
            mins[i] = min(mins[i], wc[i])
            maxs[i] = max(maxs[i], wc[i])
center = [(a + b) / 2 for a, b in zip(mins, maxs, strict=True)]
size = max(b - a for a, b in zip(mins, maxs, strict=True))

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 16
scene.cycles.device = "CPU"
scene.render.resolution_x = 640
scene.render.resolution_y = 480
scene.render.film_transparent = False
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.15, 0.15, 0.15, 1)
scene.world = world

sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", type="SUN"))
sun.data.energy = 3.0
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun)

cam_data = bpy.data.cameras.new("cam")
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam

views = {
    "34": (math.radians(70), math.radians(35)),  # 3/4 alto
    "low": (math.radians(88), math.radians(0)),  # rasante — mostra placas
}
for tag, (pitch, yaw) in views.items():
    dist = size * 2.2
    cam.location = (
        center[0] + dist * math.sin(pitch) * math.sin(yaw),
        center[1] - dist * math.sin(pitch) * math.cos(yaw),
        center[2] + dist * math.cos(pitch),
    )
    direction = Vector(center) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{out_prefix}_{tag}.png"
    bpy.ops.render.render(write_still=True)
    print("rendered", scene.render.filepath)
