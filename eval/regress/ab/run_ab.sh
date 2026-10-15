#!/bin/bash
# usage: run_ab.sh <out_dir> <concurrency> <variant order...>   (variant read per mission from /app/.data/prompt_variant)
OUT=$1; CONC=$2; shift 2
cd /opt/seleric/Seleric_Agent
for V in "$@"; do
  docker exec seleric_agent-recovery-1 sh -c "echo $V > /app/.data/prompt_variant"; sleep 4
  date +%T > $OUT/$V.start
  python3 eval/regress/run_regression.py ${ITEMS:-eval/regress/ab/items.json} $OUT/$V $CONC > $OUT/$V.log 2>&1
  docker exec seleric_agent-recovery-1 cat /app/.data/prompt_variant > $OUT/$V.variant_after 2>&1
  date +%T > $OUT/$V.end
done
docker exec seleric_agent-recovery-1 sh -c "echo base > /app/.data/prompt_variant"
echo done > $OUT/DONE
