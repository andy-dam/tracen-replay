// The Azure side of the hosted service, in one resource group: the storage
// account (originals, kept copies, analysis files, frames, and the analysis
// queue), the PostgreSQL server, the Container Apps environment with the
// API, and the Static Web App for the client. The workers that run analyses
// are not here: they pull from the queue from Oracle (deploy/oracle) or, when
// the monthly compute budget allows, from a Container Apps job added later.
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

@description('The service image the API runs (the Dockerfile app stage), for example ghcr.io/andy-dam/tracen-replay:<sha>.')
param apiImage string

@description('PostgreSQL administrator login.')
param postgresAdmin string = 'tracen'

@secure()
@description('PostgreSQL administrator password.')
param postgresPassword string

@description('Public IP addresses allowed to reach PostgreSQL besides Azure services: the Oracle worker instance.')
param workerAddresses array = []

@description('Networks of the ingress in front of the API, whose X-Forwarded-For names the client (-trusted-proxy). Set after the first deploy from the peer address the API logs.')
param trustedProxies string = ''

@description('Analyses everyone together may start in 24 hours (what the free worker finishes) and in 30 days (the fence on the credit).')
param dailyTotal int = 4
param monthlyTotal int = 100

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
var apiDefaultHost = '${name}-api.${environment.properties.defaultDomain}'
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

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
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
  name: '${name}-api'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
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
          image: apiImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'PORT', value: '8765' }
            { name: 'TRACEN_DATA', value: '/tmp/tracen' }
            { name: 'TRACEN_STORAGE_ACCOUNT', value: storage.name }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'TRACEN_DATABASE_URL', secretRef: 'database-url' }
          ]
          args: concat([
            '-database', 'postgres'
            '-object-store', 'azure'
            '-shared-queue', 'azure'
            '-allowed-host', apiDefaultHost
            '-max-recordings', '10'
            '-max-recording-gb', '20'
            '-max-storage-gb', '400'
            '-daily-per-user', '2'
            '-daily-total', string(dailyTotal)
            '-monthly-total', string(monthlyTotal)
            '-max-analysis', '10h'
            '-recording-retention', '0'
            '-frame-cache-gb', '1'
          ], apiHost == '' ? [] : ['-allowed-host', apiHost],
            appOrigin == '' ? [] : ['-api-only', '-allowed-origin', appOrigin, '-cookie-samesite', 'lax'],
            trustedProxies == '' ? [] : ['-trusted-proxy', trustedProxies])
          probes: [
            { type: 'Liveness', httpGet: { path: '/healthz', port: 8765 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/readyz', port: 8765 }, periodSeconds: 30, failureThreshold: 3 }
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
