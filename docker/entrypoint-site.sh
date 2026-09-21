#!/bin/sh
# The site image: the service without an analyzer. It needs -shared-queue
# (the analyses run in tracen worker processes), which the deployment passes
# with its other arguments; they are all handed on.
set -e
exec /usr/local/bin/tracen \
	-addr "0.0.0.0:${PORT:-8765}" \
	-data "${TRACEN_DATA:-/data}" \
	-analyzer-version-file /opt/tracen/analyzer-version.json \
	"$@"
