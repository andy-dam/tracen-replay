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
# Git Bash on Windows rewrites an argument that starts with a slash into a
# Windows path, which turns the scope into nonsense; the two variables stop
# that. The assignment is checked afterwards, because without it the sign-in
# works and the deploy still fails with "No subscriptions found".
scope="/subscriptions/$subscription/resourceGroups/$rg"
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' az role assignment create --assignee-object-id "$objectId" \
	--assignee-principal-type ServicePrincipal --role Contributor --scope "$scope" --output none
if [ -z "$(MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' az role assignment list --assignee "$appId" --scope "$scope" --query "[0].id" -o tsv)" ]; then
	echo "the deploy identity has no role on $scope; the role assignment did not take" >&2
	exit 1
fi
if ! az ad app federated-credential list --id "$appId" --query "[?name=='github-main']" -o tsv | grep -q github-main; then
	az ad app federated-credential create --id "$appId" --parameters "{
		\"name\": \"github-main\",
		\"issuer\": \"https://token.actions.githubusercontent.com\",
		\"subject\": \"repo:$repo:ref:refs/heads/main\",
		\"audiences\": [\"api://AzureADTokenExchange\"]
	}" --output none
fi

# GitHub now names the repository in the token's subject with its immutable
# ids as well (repo:OWNER@OWNER_ID/NAME@REPO_ID:ref:...); Azure matches the
# subject exactly, so that form gets its own credential. The ids come from
# the gh CLI, or from TRACEN_REPO_IDS="OWNER_ID REPO_ID".
ids="${TRACEN_REPO_IDS:-$(gh api "repos/$repo" --jq '"\(.owner.id) \(.id)"' 2>/dev/null || true)}"
if [ -n "$ids" ]; then
	ownerId="${ids% *}"
	repoId="${ids#* }"
	subject="repo:${repo%%/*}@${ownerId}/${repo#*/}@${repoId}:ref:refs/heads/main"
	if ! az ad app federated-credential list --id "$appId" --query "[?name=='github-main-ids']" -o tsv | grep -q github-main-ids; then
		az ad app federated-credential create --id "$appId" --parameters "{
			\"name\": \"github-main-ids\",
			\"issuer\": \"https://token.actions.githubusercontent.com\",
			\"subject\": \"$subject\",
			\"audiences\": [\"api://AzureADTokenExchange\"]
		}" --output none
	fi
	echo "federated subjects: repo:$repo:ref:refs/heads/main and $subject"
else
	echo "could not read the repository's ids (gh not signed in?); set TRACEN_REPO_IDS=\"OWNER_ID REPO_ID\" and run again" >&2
fi

cat <<EOF

Set these on the repository (Settings > Secrets and variables > Actions), or with gh:

  gh secret set AZURE_CLIENT_ID --repo $repo --body "$appId"
  gh secret set AZURE_TENANT_ID --repo $repo --body "$tenant"
  gh secret set AZURE_SUBSCRIPTION_ID --repo $repo --body "$subscription"
  gh variable set TRACEN_RG --repo $repo --body "$rg"

From then on every merge to main builds the images and points the app at the new one.
EOF
