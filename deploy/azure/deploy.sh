#!/bin/sh
# Creates or updates the Azure side of the hosted service from main.bicep.
# Run it signed in with the Azure CLI (az login) on the Azure for Students
# subscription. Every value it needs is an environment variable so nothing
# secret is on the command line:
#
#   TRACEN_RG           resource group name (default tracen)
#   TRACEN_LOCATION     region (default eastus)
#   TRACEN_APP_ORIGIN   the client's origin, e.g. https://app.example.com
#   TRACEN_API_HOST     the API's host name, e.g. api.example.com (optional)
#   TRACEN_API_IMAGE    the service image, e.g. ghcr.io/andy-dam/tracen-replay:latest
#   TRACEN_PG_PASSWORD  PostgreSQL administrator password
#   TRACEN_WORKER_IPS   comma-separated public addresses of the worker instances (optional)
#   TRACEN_TRUSTED_PROXIES  the ingress networks, once known (optional)
#
# The first run creates everything; later runs change only what differs.
set -eu
rg="${TRACEN_RG:-tracen}"
location="${TRACEN_LOCATION:-eastus}"
: "${TRACEN_APP_ORIGIN:?the client's origin}"
: "${TRACEN_API_IMAGE:?the service image}"
: "${TRACEN_PG_PASSWORD:?the PostgreSQL password}"
workers="[]"
if [ -n "${TRACEN_WORKER_IPS:-}" ]; then
	workers="[$(echo "$TRACEN_WORKER_IPS" | sed 's/[^,]*/"&"/g')]"
fi

az group create --name "$rg" --location "$location" --output none
az deployment group create \
	--resource-group "$rg" \
	--template-file "$(dirname "$0")/main.bicep" \
	--parameters appOrigin="$TRACEN_APP_ORIGIN" apiHost="${TRACEN_API_HOST:-}" apiImage="$TRACEN_API_IMAGE" \
		postgresPassword="$TRACEN_PG_PASSWORD" workerAddresses="$workers" trustedProxies="${TRACEN_TRUSTED_PROXIES:-}" \
	--query properties.outputs --output yaml

# The whole credit is this project's; the alert is the early warning that
# a month is running hot (docs/deployment.md, section 9).
az consumption budget create --budget-name tracen-monthly --amount 8 --time-grain Monthly \
	--start-date "$(date -u +%Y-%m-01)" --end-date "$(date -u -d '+2 years' +%Y-%m-01 2>/dev/null || date -u -v+2y +%Y-%m-01)" \
	--category Cost --resource-group "$rg" --output none 2>/dev/null || true

cat <<EOF

Next:
  1. The client's deployment token, for the GitHub secret AZURE_STATIC_WEB_APPS_API_TOKEN:
       az staticwebapp secrets list --name "${TRACEN_RG:-tracen}-web" --resource-group "$rg" --query properties.apiKey -o tsv
  2. The worker's storage connection string, for the Oracle instance (deploy/oracle):
       az storage account show-connection-string --name <storageAccount above> --resource-group "$rg" -o tsv
  3. The API's peer address in its logs, for TRACEN_TRUSTED_PROXIES on the next run.
EOF
