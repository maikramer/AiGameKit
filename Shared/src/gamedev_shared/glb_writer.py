"""GLB writer — builds glTF 2.0 binary files via pygltflib."""

from __future__ import annotations

from pathlib import Path

from pygltflib import (
    GLTF2,
    Accessor,
    Buffer,
    BufferView,
    Node,
    Scene,
)


class GlbWriter:
    """Build a glTF 2.0 GLB file programmatically.

    Wraps :class:`pygltflib.GLTF2` with helpers for buffer view and
    accessor creation.  Initialise, populate, then call :meth:`save`.
    """

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

        # Start with one empty buffer — append_to_buffer grows it
        self.gltf.buffers = [Buffer()]
        self.gltf.buffers[0].uri = None  # embedded in GLB

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
        self.gltf.append_to_buffer(blob_bytes)

        byte_offset = self.gltf.bufferViews[-1].byteOffset  # type: ignore[union-attr]
        bv = BufferView(
            buffer=0,
            byteOffset=byte_offset,
            byteLength=len(blob_bytes),
        )
        if target is not None:
            bv.target = target

        idx = len(self.gltf.bufferViews) if self.gltf.bufferViews else 0
        if self.gltf.bufferViews is None:
            self.gltf.bufferViews = []
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

        idx = len(self.gltf.accessors) if self.gltf.accessors else 0
        if self.gltf.accessors is None:
            self.gltf.accessors = []
        self.gltf.accessors.append(acc)
        return idx

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Write the GLB to *path*.

        Args:
            path: Destination file path (should end in ``.glb``).
        """
        self.gltf.save(str(path))
