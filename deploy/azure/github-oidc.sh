#!/bin/sh
# Lets the release workflow deploy on its own: an app registration that
# GitHub Actions signs in as (no password: a federated credential trusts
# tokens from this repository's main branch), with Contributor on the
# resource group. Run it once, signed in with the Azure CLI, then set the
# repository secrets and variables it prints (the values it prints are
# identifiers, not secrets, but the secret slots are where the workflow
# reads them).
#
#   TRACEN_RG      resource group (default tracen)
#   TRACEN_REPO    GitHub repository (default andy-dam/tracen-replay)
set -eu
rg="${TRACEN_RG:-tracen}"
repo="${TRACEN_REPO:-andy-dam/tracen-replay}"
name="$rg-github-deploy"

subscription=$(az account show --query id -o tsv)
tenant=$(az account show --query tenantId -o tsv)
appId=$(az ad app list --display-name "$name" --query "[0].appId" -o tsv)
if [ -z "$appId" ]; then
	appId=$(az ad app create --display-name "$name" --query appId -o tsv)
fi
if ! az ad sp show --id "$appId" --output none 2>/dev/null; then
	az ad sp create --id "$appId" --output none
fi
objectId=$(az ad sp show --id "$appId" --query id -o tsv)
az role assignment create --assignee-object-id "$objectId" --assignee-principal-type ServicePrincipal \
	--role Contributor --scope "/subscriptions/$subscription/resourceGroups/$rg" --output none 2>/dev/null || true
if ! az ad app federated-credential list --id "$appId" --query "[?name=='github-main']" -o tsv | grep -q github-main; then
	az ad app federated-credential create --id "$appId" --parameters "{
		\"name\": \"github-main\",
		\"issuer\": \"https://token.actions.githubusercontent.com\",
		\"subject\": \"repo:$repo:ref:refs/heads/main\",
		\"audiences\": [\"api://AzureADTokenExchange\"]
	}" --output none
fi

cat <<EOF

Set these on the repository (Settings > Secrets and variables > Actions), or with gh:

  gh secret set AZURE_CLIENT_ID --repo $repo --body "$appId"
  gh secret set AZURE_TENANT_ID --repo $repo --body "$tenant"
  gh secret set AZURE_SUBSCRIPTION_ID --repo $repo --body "$subscription"
  gh variable set TRACEN_RG --repo $repo --body "$rg"

From then on every merge to main builds the images and points the app at the new one.
EOF
