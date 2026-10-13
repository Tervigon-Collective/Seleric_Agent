#!/bin/bash
# usage: ch.sh "SQL"
PASS=$(docker exec seleric-mcp-cube-v2-1 printenv CUBEJS_DB_PASS)
HOST=$(docker exec seleric-mcp-cube-v2-1 printenv CUBEJS_DB_HOST)
curl -s "http://$HOST:8123/" --user "cube_serve:$PASS" --data-binary "$1 FORMAT TSVWithNames"
