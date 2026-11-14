cd /opt/seleric/Seleric_Agent_Core
PY=.venv/bin/python; [ -x $PY ] || PY=/opt/seleric/Seleric_Agent/.venv/bin/python
O=/tmp/claude-1000/-opt-seleric-Seleric-Agent/c46ae96b-4cac-4e23-9796-9b045da9e5bf/scratchpad/out
echo "### golden_axis"; timeout 900 $PY scripts/golden_axis_v3.py > $O/core_golden_axis.txt 2>&1; echo "exit $?" >> $O/core_golden_axis.txt
echo "### axis_certify"; timeout 1500 $PY scripts/axis_certify_v3.py --out $O/core_axis_certify.json > $O/core_axis_certify.txt 2>&1; echo "exit $?" >> $O/core_axis_certify.txt
echo "### ddl_drift"; timeout 900 python3 scripts/serve_ddl_drift_v3.py --mage /home/tervigon/work/mage_v3 --json $O/core_ddl_drift.json --md $O/core_ddl_drift.md > $O/core_ddl_drift.txt 2>&1; echo "exit $?" >> $O/core_ddl_drift.txt
echo "### semantic_sql"; timeout 900 $PY scripts/semantic_sql_check_v3.py > $O/core_semantic_sql.txt 2>&1; echo "exit $?" >> $O/core_semantic_sql.txt
echo "### decisions"; timeout 900 python3 scripts/check_decisions_v3.py --mage /home/tervigon/work/mage_v3 > $O/core_decisions.txt 2>&1; echo "exit $?" >> $O/core_decisions.txt
echo ALLDONE
