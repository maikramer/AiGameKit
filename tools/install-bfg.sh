#!/usr/bin/env bash
# Instala o JAR do BFG Repo Cleaner junto a tools/bfg (requer Java no PATH).
# Histórico: após criar um mirror, ver tools/bfg-strip-mirror.sh (defeito 2M, não 5M).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${BFG_VERSION:-1.14.0}"
JAR="$HERE/bfg-${VERSION}.jar"
URL="https://repo1.maven.org/maven2/com/madgag/bfg/${VERSION}/bfg-${VERSION}.jar"
if [[ -f "$JAR" ]]; then
  echo "Já existe: $JAR"
  exit 0
fi
echo "A transferir $URL ..."
curl -fsSL -o "$JAR" "$URL"
chmod a+r "$JAR"
echo "OK: $JAR"
java -jar "$JAR" --version 2>/dev/null || true
echo "Wrapper: $HERE/bfg"
