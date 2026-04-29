"""GLB writer — builds glTF 2.0 binary files via pygltflib."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from pygltflib import (
    ARRAY_BUFFER,
    ELEMENT_ARRAY_BUFFER,
    FLOAT,
    GLTF2,
    SCALAR,
    UNSIGNED_INT,
    UNSIGNED_SHORT,
    VEC2,
    VEC3,
    Accessor,
    Attributes,
    Buffer,
    BufferView,
    Mesh,
    Node,
    Primitive,
    Scene,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray


class GlbWriter:
    """Build a glTF 2.0 GLB file programmatically.

    Wraps :class:`pygltflib.GLTF2` with helpers for buffer view and
    accessor creation.  Initialise, populate, then call :meth:`save`.
    """

    _UINT16_MAX: int = 65535

    def __init__(self) -> None:
        """Create a minimal GLTF2 with a default scene and empty buffer."""
        self.gltf = GLTF2()

        # One default scene with a single empty node
        scene = Scene()
        node = Node()
        scene.nodes = [0]
        self.gltf.scenes = [scene]
        self.gltf.nodes = [node]
        self.gltf.scene = 0

        # Start with one empty buffer
        self.gltf.buffers = [Buffer()]
        self.gltf.buffers[0].uri = None  # embedded in GLB

        # Binary blob accumulator and running byte offset
        self._blob_data = bytearray()
        self._blob_offset = 0

        # Initialise container lists so _add_buffer_view / _add_accessor work
        self.gltf.bufferViews = []
        self.gltf.accessors = []
        self.gltf.meshes = []

    # ------------------------------------------------------------------
    # Buffer / accessor helpers
    # ------------------------------------------------------------------

    def _add_buffer_view(self, blob_bytes: bytes, target: int | None = None) -> int:
        """Append *blob_bytes* to the GLB buffer and create a BufferView.

        Args:
            blob_bytes: Raw binary data to embed.
            target: Optional buffer target (e.g. ``ARRAY_BUFFER``, ``ELEMENT_ARRAY_BUFFER``).

        Returns:
            Index of the new :class:`BufferView` in ``gltf.bufferViews``.
        """
        offset = self._blob_offset
        self._blob_data.extend(blob_bytes)
        self._blob_offset += len(blob_bytes)

        bv = BufferView(
            buffer=0,
            byteOffset=offset,
            byteLength=len(blob_bytes),
        )
        if target is not None:
            bv.target = target

        idx = len(self.gltf.bufferViews)
        self.gltf.bufferViews.append(bv)
        return idx

    def _add_accessor(
        self,
        buffer_view_idx: int,
        component_type: int,
        count: int,
        type_: str,
        min_val: list[float] | None = None,
        max_val: list[float] | None = None,
    ) -> int:
        """Create an Accessor attached to an existing BufferView.

        Args:
            buffer_view_idx: Index into ``gltf.bufferViews``.
            component_type: glTF component type constant (e.g. ``5126`` for FLOAT).
            count: Number of elements.
            type_: Accessor type string (e.g. ``"SCALAR"``, ``"VEC3"``).
            min_val: Optional per-element min values.
            max_val: Optional per-element max values.

        Returns:
            Index of the new :class:`Accessor` in ``gltf.accessors``.
        """
        acc = Accessor(
            bufferView=buffer_view_idx,
            byteOffset=0,
            componentType=component_type,
            count=count,
            type=type_,
        )
        if min_val is not None:
            acc.min = min_val
        if max_val is not None:
            acc.max = max_val

        idx = len(self.gltf.accessors)
        self.gltf.accessors.append(acc)
        return idx

    # ------------------------------------------------------------------
    # Mesh creation
    # ------------------------------------------------------------------

    def add_mesh(
        self,
        vertices: NDArray[np.float32],
        faces: NDArray[np.int32],
        normals: NDArray[np.float32] | None = None,
        uvs: NDArray[np.float32] | None = None,
        material_idx: int | None = None,
    ) -> int:
        """Add a triangle mesh and return its mesh index.

        Creates POSITION and index accessors, optional NORMAL / TEXCOORD_0
        accessors, a Mesh with one Primitive, and a Node referencing it.

        Args:
            vertices: Vertex positions ``(N, 3)`` float32.
            faces: Triangle indices ``(F, 3)`` int32.
            normals: Optional per-vertex normals ``(N, 3)`` float32.
            uvs: Optional per-vertex UV coordinates ``(N, 2)`` float32.
            material_idx: Optional material index for the primitive.

        Returns:
            Index of the new mesh in ``gltf.meshes``.
        """
        num_verts = vertices.shape[0]

        verts_f32 = np.ascontiguousarray(vertices, dtype=np.float32)
        bv_pos = self._add_buffer_view(verts_f32.tobytes(), target=ARRAY_BUFFER)
        pos_min = vertices.min(axis=0).tolist()
        pos_max = vertices.max(axis=0).tolist()
        acc_pos = self._add_accessor(bv_pos, FLOAT, num_verts, VEC3, min_val=pos_min, max_val=pos_max)

        indices = np.ascontiguousarray(faces, dtype=np.int32).ravel()
        if indices.max() <= self._UINT16_MAX:
            idx_dtype = np.uint16
            idx_component = UNSIGNED_SHORT
        else:
            idx_dtype = np.uint32
            idx_component = UNSIGNED_INT
        indices_typed = np.ascontiguousarray(indices, dtype=idx_dtype)
        bv_idx = self._add_buffer_view(indices_typed.tobytes(), target=ELEMENT_ARRAY_BUFFER)
        acc_idx = self._add_accessor(bv_idx, idx_component, len(indices_typed), SCALAR)

        acc_normal: int | None = None
        if normals is not None:
            nrm_f32 = np.ascontiguousarray(normals, dtype=np.float32)
            bv_nrm = self._add_buffer_view(nrm_f32.tobytes(), target=ARRAY_BUFFER)
            acc_normal = self._add_accessor(bv_nrm, FLOAT, normals.shape[0], VEC3)

        acc_uv: int | None = None
        if uvs is not None:
            uv_f32 = np.ascontiguousarray(uvs, dtype=np.float32)
            bv_uv = self._add_buffer_view(uv_f32.tobytes(), target=ARRAY_BUFFER)
            acc_uv = self._add_accessor(bv_uv, FLOAT, uvs.shape[0], VEC2)

        attrs = Attributes(POSITION=acc_pos)
        if acc_normal is not None:
            attrs.NORMAL = acc_normal
        if acc_uv is not None:
            attrs.TEXCOORD_0 = acc_uv

        prim_kwargs: dict = {"attributes": attrs, "indices": acc_idx}
        if material_idx is not None:
            prim_kwargs["material"] = material_idx

        prim = Primitive(**prim_kwargs)
        mesh = Mesh(primitives=[prim])
        mesh_idx = len(self.gltf.meshes)
        self.gltf.meshes.append(mesh)

        node = Node(mesh=mesh_idx)
        node_idx = len(self.gltf.nodes)
        self.gltf.nodes.append(node)
        scene_nodes = self.gltf.scenes[0].nodes
        if scene_nodes is None:
            scene_nodes = []
        scene_nodes.append(node_idx)
        self.gltf.scenes[0].nodes = scene_nodes

        return mesh_idx

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Write the GLB to *path*.

        Args:
            path: Destination file path (should end in ``.glb``).
        """
        if self._blob_data:
            self.gltf.set_binary_blob(bytes(self._blob_data))
            self.gltf.buffers[0].byteLength = len(self._blob_data)
        self.gltf.save(str(path))
