.PHONY: check-no-deep-imports
## Fail if any example imports engine internals via ../../src/ relative paths.
## Examples must import from the public 'vibegame' barrel only; reaching into
## engine source couples games to internal module layout. node_modules/ and
## dist/ (build outputs) are excluded.
check-no-deep-imports:
	@matches=$$(grep -rn "\.\./\.\./\.\./src/" examples/ \
		--exclude-dir=node_modules --exclude-dir=dist 2>/dev/null); \
	if [ -n "$$matches" ]; then \
		echo "ERROR: examples must not deep-import from ../../src/ (use 'vibegame' barrel):"; \
		echo "$$matches"; \
		exit 1; \
	else \
		echo "OK: no deep relative src/ imports in examples/"; \
	fi
