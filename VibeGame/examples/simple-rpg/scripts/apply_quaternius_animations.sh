#!/usr/bin/env bash
# Aplica animações CC0 do Quaternius (via retarget) aos modelos humanoides do simple-rpg.
#
# Humanoides (retarget Quaternius): recebem os 12 clips do perfil quaternius.
# Não-humanoides (scorpion): mantêm animação procedural, só renomeiam tracks.
#
# Uso:  bash VibeGame/examples/simple-rpg/scripts/apply_quaternius_animations.sh
# Pré:  animator3d no PATH (ou Animator3D/.venv/bin/animator3d), pack Quaternius em cache
#       (auto-download se faltar).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MESHES_DIR="$(cd "$SCRIPT_DIR/../public/assets/meshes" && pwd)"

# Resolver animator3d: PATH > venv do Animator3D.
if command -v animator3d >/dev/null 2>&1; then
  ANIMATOR3D="animator3d"
elif [ -x "$SCRIPT_DIR/../../../../Animator3D/.venv/bin/animator3d" ]; then
  ANIMATOR3D="$SCRIPT_DIR/../../../../Animator3D/.venv/bin/animator3d"
else
  echo "ERRO: animator3d não encontrado no PATH nem em Animator3D/.venv/bin/" >&2
  exit 1
fi

# Clips a retargetizar (nomes limpos; subconjunto do perfil quaternius).
CLIPS="idle,walk,run,sprint,jump,attack,punch,hit,death,roar,roll,interact"

# Humanoides (retarget completo).
HUMANOIDS=(
  hero npc_merchant goblin slime wolf bandit shade
  witch_boss sand_worm bogling bog_warden_boss boss_ogre
)

# Não-humanoides (só rename de tracks existentes para nomes limpos).
NON_HUMANOIDS=(scorpion)

# Mapeamento de rename para os não-humanoides (clips procedurais Animator3D_* -> limpos).
# Scorpion tem Walk/Run/Jump/Attack/Roar (+ BreatheIdle); NÃO tem Fall.
RENAME_MAP="Animator3D_BreatheIdle:idle,Animator3D_Walk:walk,Animator3D_Run:run,Animator3D_Jump:jump,Animator3D_Attack:attack,Animator3D_Roar:roar"

echo "════════════════════════════════════════════════════════════"
echo "  Quaternius retarget — simple-rpg"
echo "  animator3d: $ANIMATOR3D"
echo "  meshes:     $MESHES_DIR"
echo "  humanoides: ${#HUMANOIDS[@]}  | não-humanoides (rename): ${#NON_HUMANOIDS[@]}"
echo "════════════════════════════════════════════════════════════"

# --- Humanoides: retarget Quaternius sobre o _rigged.glb (sem animação) ---
for id in "${HUMANOIDS[@]}"; do
  src="$MESHES_DIR/${id}_rigged.glb"
  dst="$MESHES_DIR/${id}_rigged_animated.glb"
  if [ ! -f "$src" ]; then
    echo "  [SKIP] $id — $src não encontrado"
    continue
  fi
  # Backup do animated atual (se existir e não for já um backup).
  if [ -f "$dst" ] && [ ! -f "${dst}.bak" ]; then
    cp "$dst" "${dst}.bak"
  fi
  echo "▶ $id (retarget Quaternius)"
  $ANIMATOR3D retarget-batch "$src" "$dst" \
    --profile quaternius --clips "$CLIPS" 2>&1 | grep -E "✓|✗|retarget-batch|Error|não mapeados" || true
  echo ""
done

# --- Não-humanoides: rename de tracks procedurais ---
# Exporta para /tmp (exportar no mesmo dir que o input pode falhar) e copia no fim.
for id in "${NON_HUMANOIDS[@]}"; do
  src="$MESHES_DIR/${id}_rigged_animated.glb"
  if [ ! -f "$src" ]; then
    echo "  [SKIP] $id (rename) — $src não encontrado"
    continue
  fi
  tmp="/tmp/${id}_renamed.glb"
  rm -f "$tmp"
  echo "▶ $id (rename tracks)"
  log="/tmp/${id}_rename.log"
  $ANIMATOR3D rename-clips "$src" "$tmp" --map "$RENAME_MAP" > "$log" 2>&1
  rc=$?
  if [ $rc -eq 0 ] && [ -f "$tmp" ]; then
    cp "$tmp" "$src"
    rm -f "$tmp"
    echo "  OK (renames: $(grep -c '✓' "$log" || echo 0))"
  else
    echo "  [ERRO] rc=$rc — ver $log"
    tail -5 "$log"
  fi
  echo ""
done

echo "════════════════════════════════════════════════════════════"
echo "  Concluído. Backups dos humanoides em *.bak."
echo "════════════════════════════════════════════════════════════"
