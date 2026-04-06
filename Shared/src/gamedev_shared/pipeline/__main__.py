"""Allow running pipeline modules directly: python -m gamedev_shared.pipeline.<module>"""
import sys

modules = sys.argv[1] if len(sys.argv) > 1 else ""
if modules in ("glb_metadata", "manifest", "validate", "cache"):
    mod = __import__(f"gamedev_shared.pipeline.{modules}", fromlist=["main"])
    mod.main()
else:
    print("Usage: python -m gamedev_shared.pipeline.<module>")
    print("Modules: glb_metadata, manifest, validate, cache")
