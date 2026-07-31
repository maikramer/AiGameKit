"""Shim de compatibilidade — reparação de mesh vive em ``aigamekit_shared.mesh_repair``.

Mantém os imports existentes (`part3d.utils.mesh_bpy.fix_mesh` é referenciado
pelo patch injectado no Space HF via ``pipeline._BPY_MESH_UTILS_TAIL``, pelo
``face_split`` e pelo decode do X-Part).
"""

from __future__ import annotations

from aigamekit_shared.mesh_repair import cap_boundary_holes, fix_mesh

__all__ = ["cap_boundary_holes", "fix_mesh"]
