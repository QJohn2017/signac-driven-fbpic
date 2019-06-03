#!/usr/bin/env bash

rm -rf .bundles/
rm fbpic-minimal-project.log
rm signac.rc
rm signac_statepoints.json
rm -rf workspace/

eval "$(conda shell.bash hook)"
conda activate signac-driven-fbpic

python3 src/init.py

