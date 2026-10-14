#!/bin/bash
cd /opt/seleric/Seleric_Agent
python3 eval/regress/run_regression.py eval/regress/replay11.json /opt/seleric/Seleric_Agent/eval/regress/results/gapclose-20261014/replay 1 only=1,2,3,4,5 > /opt/seleric/Seleric_Agent/eval/regress/results/gapclose-20261014/replay.log 2>&1
python3 eval/regress/run_regression.py eval/regress/test_agent6.json /opt/seleric/Seleric_Agent/eval/regress/results/gapclose-20261014/six 1 only=3,5,6 > /opt/seleric/Seleric_Agent/eval/regress/results/gapclose-20261014/six.log 2>&1
python3 eval/regress/run_regression.py eval/regress/regression.json /opt/seleric/Seleric_Agent/eval/regress/results/gapclose-20261014/golden 1 only=20,22,30 > /opt/seleric/Seleric_Agent/eval/regress/results/gapclose-20261014/golden.log 2>&1
echo done > /opt/seleric/Seleric_Agent/eval/regress/results/gapclose-20261014/DONE
