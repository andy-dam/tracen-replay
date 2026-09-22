// The Azure side of the hosted service, in one resource group: the storage
// account (originals, kept copies, analysis files, frames, and the analysis
// queue), the PostgreSQL server, the Container Apps environment with the
// API and the analysis job the queue starts, and, with a domain, the Static
// Web App for the client. A worker on Oracle (deploy/oracle) can take from
// the same queue for free; the job is what runs analyses without it, fenced
// by the monthly budget.
//
// Deploy with deploy/azure/deploy.sh; see docs/deployment.md, section 6.

targetScope = 'resourceGroup'

@description('Prefix for every resource name; letters and digits only, at most 12 characters (storage account names are short).')
@minLength(3)
@maxLength(12)
param name string = 'tracen'

@description('Region for everything; East US has the cheapest Blob storage.')
param location string = resourceGroup().location

@description('The client origin when the client is served from its own domain, for example https://app.example.com; empty serves the client from the API itself at its Azure hostname, which needs no domain.')
param appOrigin string = ''

@description('Host name the API answers for, for example api.example.com; the container app default host is always allowed too.')
param apiHost string = ''

@description('Name of the container app that serves the application; it is the first part of the Azure hostname.')
param appName string = 'tracen-replay'

@description('The whole application image (the Dockerfile app stage), which the analysis job runs, for example ghcr.io/andy-dam/tracen-replay:<sha>.')
param apiImage string

@description('The image the website runs: the small site stage of the same build (ghcr.io/andy-dam/tracen-replay:site-<sha>), which starts in seconds after scaling to nothing. The whole application image works too.')
param siteImage string = apiImage

@description('PostgreSQL administrator login.')
param postgresAdmin string = 'tracen'

@secure()
@description('PostgreSQL administrator password.')
param postgresPassword string

@description('Public IP addresses allowed to reach PostgreSQL besides Azure services: the Oracle worker instance.')
param workerAddresses array = []

@description('Networks of the ingress in front of the API, whose X-Forwarded-For names the client (-trusted-proxy): the private ranges the Container Apps ingress lives in. The client is the rightmost address outside them, which a client cannot forge.')
param trustedProxies string = '10.0.0.0/8,100.64.0.0/10,172.16.0.0/12,192.168.0.0/16'

@description('Analyses everyone together may start in 24 hours and in 30 days. The month is the fence on the credit: a career on the job takes about seven hours and costs about $3 beyond the free grant, which covers about two a month.')
param dailyTotal int = 2
param monthlyTotal int = 3

@description('Analyses one account may start in 24 hours.')
param dailyPerUser int = 2

@description('OCR worker processes of one analysis on the job (4 vCPU, 8 GiB). A reader holds about half a gigabyte, so three leave the main process room; lower it if the job runs out of memory.')
param jobWorkers int = 3

var storageName = toLower('${name}${uniqueString(resourceGroup().id)}')
var containers = ['originals', 'kept', 'jobs', 'frames']

// ---- storage: files and the queue ----

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take(storageName, 24)
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

// Where the client is served from: its own domain, or the API's own Azure
// hostname (the app's name under the environment's default domain).
var apiDefaultHost = '${appName}.${environment.properties.defaultDomain}'
var clientOrigin = appOrigin == '' ? 'https://${apiDefaultHost}' : appOrigin

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    // The browser puts uploads straight into originals/ with a signed URL.
    cors: {
      corsRules: [
        {
          allowedOrigins: [clientOrigin]
          allowedMethods: ['GET', 'HEAD', 'OPTIONS', 'PUT']
          allowedHeaders: ['*']
          exposedHeaders: ['*']
          maxAgeInSeconds: 3600
        }
      ]
    }
    deleteRetentionPolicy: { enabled: true, days: 14 }
  }
}

resource blobContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for c in containers: {
  parent: blobService
  name: c
  properties: { publicAccess: 'None' }
}]

// The analysis job's scratch space. An analysis writes 12 to 15 GB of frames
// and crops before it prunes them, and a Container Apps replica has 8 GiB
// of disk of its own at most: a full career ran out of it ("No space left on
// device") five hours in. A file share has no such bound, is billed for what
// is on it while it is there, and outlives an execution, which is what lets
// a paused analysis be resumed by the next one. A job's files are deleted
// when it ends, or when it is given up while paused.
resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource scratchShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'scratch'
  properties: {
    accessTier: 'TransactionOptimized'
    shareQuota: 100
  }
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource analysesQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queueService
  name: 'analyses'
}

// Originals go cold a day after they arrive and are deleted after 90 days
// (a re-analysis within those days reads the cold blob); frames are a cache.
resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'originals-cold-then-gone'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: ['blockBlob'], prefixMatch: ['originals/'] }
            actions: {
              baseBlob: {
                tierToCold: { daysAfterModificationGreaterThan: 1 }
                delete: { daysAfterModificationGreaterThan: 90 }
              }
            }
          }
        }
        {
          name: 'frames-cache'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: ['blockBlob'], prefixMatch: ['frames/'] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 30 } } }
          }
        }
      ]
    }
  }
}

// ---- the database ----

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: '${name}-pg'
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: postgresAdmin
    administratorLoginPassword: postgresPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: 'tracen'
}

// Azure services (the container app) and the worker addresses only.
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource allowWorkers 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = [for (address, i) in workerAddresses: {
  parent: postgres
  name: 'worker-${i}'
  properties: { startIpAddress: address, endIpAddress: address }
}]

// ---- the API ----

// The environment is created by deploy.sh with the CLI: an environment a
// template creates comes out as the "express" kind in this region, which
// cannot run jobs, and only the CLI's --environment-mode WorkloadProfiles
// makes a standard one (the ARM schema has no such property yet).
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: '${name}-apps'
}

// The share as the environment knows it; a volume of the job names it. A
// share is mounted with the account key: Container Apps has no identity
// sign-in for file shares.
resource scratchStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: 'scratch'
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: scratchShare.name
      accessMode: 'ReadWrite'
    }
  }
}

// The API signs in to storage as this identity: it reads and writes blobs
// and the queue, and signs URLs on the account's behalf (Blob Delegator).
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${name}-api-identity'
  location: location
}

var roles = {
  blobContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  blobDelegator: 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'
  queueContributor: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
}

resource roleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in items(roles): {
  name: guid(storage.id, identity.id, role.value)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.value)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

var databaseURL = 'postgres://${postgresAdmin}:${uriComponent(postgresPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/tracen?sslmode=require'

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      ingress: {
        external: true
        targetPort: 8765
        transport: 'http'
        allowInsecure: false
      }
      secrets: [
        { name: 'database-url', value: databaseURL }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: siteImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'PORT', value: '8765' }
            { name: 'TRACEN_DATA', value: '/tmp/tracen' }
            { name: 'TRACEN_STORAGE_ACCOUNT', value: storage.name }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'TRACEN_DATABASE_URL', secretRef: 'database-url' }
            // A paused analysis holds 12 to 15 GB on the scratch share. A
            // variable, not an argument: an image from before the setting
            // ignores it, and would not start with an argument it does not know.
            { name: 'TRACEN_PAUSED_LIFETIME', value: '24h' }
          ]
          args: concat([
            '-database', 'postgres'
            '-object-store', 'azure'
            '-shared-queue', 'azure'
            '-allowed-host', apiDefaultHost
            '-max-recordings', '10'
            '-max-recording-gb', '20'
            '-max-storage-gb', '400'
            '-daily-per-user', string(dailyPerUser)
            '-daily-total', string(dailyTotal)
            '-monthly-total', string(monthlyTotal)
            '-max-analysis', '10h'
            '-recording-retention', '0'
            '-original-lifetime', '2160h'
            '-frame-cache-gb', '1'
          ], apiHost == '' ? [] : ['-allowed-host', apiHost],
            appOrigin == '' ? [] : ['-api-only', '-allowed-origin', appOrigin, '-cookie-samesite', 'lax'],
            trustedProxies == '' ? [] : ['-trusted-proxy', trustedProxies])
          // The app scales to nothing, so a first visitor waits for a start.
          // Without a startup probe the first readiness check ran before the
          // service was listening, failed, and the next one was a whole
          // period away: thirty seconds of a forty-four second wait. The
          // startup probe asks every second, and readiness follows at once.
          probes: [
            { type: 'Startup', httpGet: { path: '/healthz', port: 8765 }, periodSeconds: 1, timeoutSeconds: 1, failureThreshold: 90 }
            { type: 'Liveness', httpGet: { path: '/healthz', port: 8765 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/readyz', port: 8765 }, periodSeconds: 10, timeoutSeconds: 10, failureThreshold: 3 }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: '50' } } }
        ]
      }
    }
  }
}

// ---- the analysis job: one execution per waiting message, 4 vCPU / 8 GiB ----

resource analysisJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${name}-analysis'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Event'
      replicaTimeout: 10 * 3600
      replicaRetryLimit: 0
      eventTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
        scale: {
          minExecutions: 0
          maxExecutions: 1
          pollingInterval: 30
          rules: [
            {
              name: 'analyses-waiting'
              type: 'azure-queue'
              metadata: { queueName: 'analyses', queueLength: '1', accountName: storage.name }
              auth: [{ secretRef: 'storage-connection', triggerParameter: 'connection' }]
            }
          ]
        }
      }
      secrets: [
        { name: 'database-url', value: databaseURL }
        { name: 'storage-connection', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${az.environment().suffixes.storage}' }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: apiImage
          command: ['/usr/local/bin/tracen']
          args: [
            'worker'
            '-python', '/usr/local/bin/python'
            '-workdir', '/opt/tracen/analyzer'
            '-model-dir', '/opt/tracen/models'
            '-database', 'postgres'
            '-object-store', 'azure'
            '-shared-queue', 'azure'
            '-scratch', '/scratch'
            '-workers', string(jobWorkers)
            '-dense-workers', '1'
            '-ocr-device', 'cpu'
            '-parallel', '1'
            '-max-analysis', '9h'
            '-copy-threads', '2'
            '-exit-when-idle'
          ]
          resources: { cpu: json('4'), memory: '8Gi' }
          env: [
            { name: 'TRACEN_STORAGE_ACCOUNT', value: storage.name }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'TRACEN_DATABASE_URL', secretRef: 'database-url' }
          ]
          volumeMounts: [
            { volumeName: 'scratch', mountPath: '/scratch' }
          ]
        }
      ]
      volumes: [
        { name: 'scratch', storageType: 'AzureFile', storageName: scratchStorage.name }
      ]
    }
  }
  dependsOn: [roleAssignments]
}

// ---- the client, only when it has its own domain ----

resource web 'Microsoft.Web/staticSites@2023-12-01' = if (appOrigin != '') {
  name: '${name}-web'
  location: location
  sku: { name: 'Free', tier: 'Free' }
  properties: {}
}

output storageAccount string = storage.name
output apiIdentityClientId string = identity.properties.clientId
output apiDefaultHost string = api.properties.configuration.ingress.fqdn
output postgresHost string = postgres.properties.fullyQualifiedDomainName
output clientURL string = appOrigin == '' ? 'https://${apiDefaultHost}' : appOrigin
output webDefaultHost string = web.?properties.defaultHostname ?? ''
