# Continuous integration and deployment

Continuous integration already exists: every push and pull request runs the
tests. Continuous deployment is what this document plans: once a change lands
on `main`, a pipeline builds an image from that exact commit and moves the
running service onto it, with nobody logging into a server.

## What runs today

Two workflows: `ci.yml` (below) on every push to `main` and `develop` and
every pull request, and `release.yml` on every push to `main`, which
builds the worker and service images for amd64 and arm64, pushes them to
GHCR tagged by commit and as `latest`, and, when the repository secrets
and variables exist, deploys the API (a `containerapp update` to the
commit's image, and a `containerapp job update` so the analysis job runs
the same analyzer) and the client (the Static Web Apps action on the built
bundle). The Oracle worker pulls `latest` once a day on its own
(`deploy/oracle/cloud-init.yaml`).

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every push
to `main` and on every pull request:

| Job | What it does |
| --- | --- |
| Go service | `go vet` and `go test` on Linux and Windows; the Linux run uses the race detector |
| Browser client | `npm ci` and `npm run build`, which also type-checks the client |
| Python worker contract | installs `./analyzer[vision]` and runs the analyzer suite; tests needing local models or recordings skip themselves |

A red job blocks nothing by itself. Requiring these checks before a pull
request can merge is a branch protection setting on the repository, not
something the workflow file decides.

Nothing deploys yet. The application runs from a local build, and the
analyzer runs from the working tree, so an analyzer commit takes effect on
the next analysis without a restart. In a deployment that stops being true:
the analyzer is inside the image, so a deploy is the only way to change it.

## Branches

Two long-lived branches, since 2026-09-20:

- **`main`** is what is deployed. It is protected on GitHub: changes arrive
  only by pull request, every CI job must pass on the pull request's head
  (the six checks above), history is linear, nobody force-pushes or
  deletes it, and the rules bind administrators too. No review approval
  is required, so one person can merge their own pull request once CI is
  green.
- **`develop`** is where work lands day to day, directly or from short
  feature branches. CI runs on every push to it. When `develop` is ready
  to ship, a pull request from `develop` to `main` is the deploy.

A hotfix goes to `develop` first and rides the next pull request, unless
`main` is on fire, in which case a branch off `main` is fixed, merged by
pull request, and merged back into `develop`.

## The shape of a deployment

Three things move together:

- **An image.** Built by [`Dockerfile`](../Dockerfile) from one commit, holding
  the service, the client, the analyzer, ffmpeg and the OCR models.
- **A registry.** Azure Container Registry stores the image. Images are named
  by the commit they were built from, never `latest`.
- **A running revision.** Azure Container Apps holds a revision per image. A
  deploy creates a new revision; the old one stays until the new one is
  healthy.

## What happens when a change reaches main

| Step | Where | What happens |
| --- | --- | --- |
| 1. Merge | GitHub | The commit lands on `main` and triggers the deploy workflow |
| 2. Test | GitHub runner | The CI jobs run again on the merged tree |
| 3. Build | GitHub runner | `docker build` produces one image and tags it with the commit SHA |
| 4. Push | Container Registry | The image is uploaded under that tag |
| 5. Deploy | Azure | `az containerapp update --image <registry>/tracen:<sha>` points the app at the new image |
| 6. Start | Container Apps | A new revision starts and its health probes are checked |
| 7. Switch | Container Apps | Traffic moves to the new revision; the old one scales to zero |
| 8. Retire | Container Apps | The old revision stays deactivated for a while so traffic can be moved back |

Steps 5 to 8 are what "updating the deployed state" means: nothing is copied
onto a machine, and no process is restarted in place. A new container starts
from the new image, and the platform moves traffic once it answers its health
probes. If the new revision never becomes healthy, traffic stays where it is
and the deploy simply fails.

## Why the tag is the commit

Tagging every image with its commit SHA makes the deployed state a fact you
can look up: the revision names an image, the image names a commit, and the
commit names the analyzer's behaviour. It also makes rollback ordinary,
because the previous image still exists under its own tag. A moving tag like
`latest` loses all of that, and leaves two machines able to disagree about
what `latest` meant.

## Signing in without storing a password

The workflow signs in to Azure with OpenID Connect: GitHub hands Azure a
short-lived token proving which repository, branch and workflow is asking, and
Azure trades it for an access token. The trust is configured once, as a
federated credential on a managed identity that may push to the registry and
update the container app. No password or publish profile is stored in the
repository, and the credential is limited to `main` of this repository.

A sketch of the deploy workflow, which is not in the repository yet:

```yaml
name: Deploy
on:
  push:
    branches: [main]

permissions:
  contents: read
  id-token: write        # required for the OIDC token

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v4
      - uses: azure/login@v2
        with:
          client-id: ${{ vars.AZURE_CLIENT_ID }}
          tenant-id: ${{ vars.AZURE_TENANT_ID }}
          subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
      - run: az acr login --name ${{ vars.REGISTRY_NAME }}
      - run: docker build -t ${{ vars.REGISTRY_NAME }}.azurecr.io/tracen:${{ github.sha }} .
      - run: docker push ${{ vars.REGISTRY_NAME }}.azurecr.io/tracen:${{ github.sha }}
      - run: |
          az containerapp update \
            --name tracen --resource-group ${{ vars.RESOURCE_GROUP }} \
            --image ${{ vars.REGISTRY_NAME }}.azurecr.io/tracen:${{ github.sha }}
```

## Schema changes

The service applies its own migrations: `internal/store` keeps a numbered list
and runs the ones a database has not seen when it opens the file
(`store.Open`). A deploy therefore migrates by starting, and a schema change
needs no separate step.

Two consequences are worth knowing before writing one. Migrations only ever go
forward, so a rollback to an older image runs against the newer schema.
And during the switch the old and the new revision both run, so a migration
must leave the old code working: add a table or a column in one release, and
only stop reading the old one in a later release.

## Rolling back

Two ways, for two situations:

- **Move the traffic back.** The previous revision still exists, so pointing
  traffic at it is immediate and needs no build. This is the emergency stop.
- **Revert the commit.** `git revert` on `main` sends a new commit through the
  same pipeline and produces a new image. This is the one that leaves the
  repository and the deployment agreeing again, and it is how a rollback
  should finish.

## Environments

One environment is enough to start: `main` deploys to production. The next
step, when changes get riskier, is a second container app fed by the same
pipeline, with production behind a GitHub environment that requires an
approval click before step 5 runs. Both read the same workflow file, so the
difference is configuration rather than a second pipeline.

## Telling whether it worked

- The workflow run itself: a red step means the deploy never happened.
- The revision list in Azure: the newest revision should be running and hold
  100% of the traffic.
- `/healthz` answers when the service is up; `/readyz` lists the checks it
  makes for python, ffmpeg, the model directory, the analyzer and the OCR
  device (see [local-app.md](local-app.md)) and answers 503 when one fails.
  The image's own health check asks `/readyz`; a platform's liveness probe
  belongs on `/healthz`, its readiness and startup probes on `/readyz`.
- Every report records the analyzer's fingerprint, so reports produced before
  and after a deploy are distinguishable.

## What it costs

This repository is public, so GitHub-hosted runners cost nothing; a private
repository would spend from a monthly allowance, and its Windows runners would
spend at twice the rate of Linux ones. The registry holds one image per
deployed commit, which is the part worth pruning occasionally. The deploy
itself costs nothing beyond the container it starts.

See [deployment.md](deployment.md) for what the application still needs before
it can run as a hosted service, and [container.md](container.md) for building
and running the image locally.
