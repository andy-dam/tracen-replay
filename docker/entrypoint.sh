#!/bin/sh
# The service listens on every interface in the container, on the port named
# by PORT, and keeps its database, uploads and job output on the data volume.
# Arguments given to the container are passed on, so extra flags such as
# `-workers 2` still reach the service.
set -e
exec /usr/local/bin/tracen \
	-addr "0.0.0.0:${PORT:-8765}" \
	-data "${TRACEN_DATA:-/data}" \
	-python /usr/local/bin/python \
	-workdir /opt/tracen/analyzer \
	-model-dir /opt/tracen/models \
	"$@"
