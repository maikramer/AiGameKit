#!/usr/bin/env bash
# Espera uma janela de GPU utilizável (sem engines de peer E com VRAM livre)
# e corre o comando dado. Uso: espera_gpu.sh <timeout_min> <cmd...>
timeout_min=$1; shift
deadline=$(( $(date +%s) + timeout_min * 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  busy=$(ps -eo args | grep 'viber run' | grep -v grep | wc -l)
  read -r used total util <<< "$(nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits | head -1 | tr ',' ' ')"
  free_mb=$(( total - used ))
  if [ "$busy" -eq 0 ] && [ "${util:-100}" -lt 5 ] && [ "${free_mb:-0}" -ge 2600 ]; then
    echo "janela livre (util=${util}%, VRAM livre=${free_mb} MB) — $(date +%H:%M:%S)"
    exec "$@"
  fi
  sleep 20
done
echo "SEM janela livre em ${timeout_min} min (última: util=${util}%, livre=${free_mb} MB)" >&2
exit 75
