param location string
param tags object
param principalId string
param searchLocation string
param apiExists bool
param openAiModelName string
param openAiModelVersion string
param embeddingModelName string
param embeddingModelVersion string

var token = uniqueString(subscription().id, resourceGroup().id, location)

// Built-in role definition IDs
var acrPullRole = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var openAiUserRole = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' // Cognitive Services OpenAI User
var cosmosDataContributorRole = '00000000-0000-0000-0000-000000000002' // Cosmos DB Built-in Data Contributor (data plane)
var searchIndexReaderRole = '1407120a-92aa-4202-b7e9-c0e197c71c8f' // Search Index Data Reader: query only
var searchIndexContributorRole = '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor: load documents
var searchServiceContributorRole = '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor: create indexes

// ---------- Identity: the app's own Entra ID identity, no secrets to manage ----------

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${token}'
  location: location
  tags: tags
}

// ---------- Azure OpenAI, with API keys switched off ----------

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'oai-${token}'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'oai-${token}' // required for Entra ID auth
    disableLocalAuth: true // no key-based access; Azure won't even list the keys
    publicNetworkAccess: 'Enabled'
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: 'chat'
  sku: { name: 'GlobalStandard', capacity: 10 }
  properties: {
    model: { format: 'OpenAI', name: openAiModelName, version: openAiModelVersion }
  }
}

// Turns text into vectors (lists of numbers) for vector search. Deployments on one account
// can't be created in parallel, hence dependsOn.
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: 'embedding'
  dependsOn: [chatDeployment]
  sku: { name: 'GlobalStandard', capacity: 50 }
  properties: {
    model: { format: 'OpenAI', name: embeddingModelName, version: embeddingModelVersion }
  }
}

resource appOpenAiAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, identity.id, openAiUserRole)
  scope: openAi
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRole)
  }
}

resource devOpenAiAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(openAi.id, principalId, openAiUserRole)
  scope: openAi
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRole)
  }
}

// ---------- Cosmos DB: conversation history, also with keys switched off ----------

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: 'cosmos-${token}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [{ locationName: location, failoverPriority: 0 }]
    capabilities: [{ name: 'EnableServerless' }] // pay per request, nothing while idle
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    disableLocalAuth: true // Entra ID only, like OpenAI
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: 'helpdesk'
  properties: { resource: { id: 'helpdesk' } }
}

resource sessionsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'sessions'
  properties: {
    resource: {
      id: 'sessions'
      partitionKey: { paths: ['/id'], kind: 'Hash' } // one session per partition: point reads and writes
      defaultTtl: 604800 // Cosmos deletes a session 7 days after its last message
    }
  }
}

// Cosmos DB data access uses its own role system, not Azure RBAC. Scoped to the database, not the account.
resource appCosmosAccess 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmosDb.id, identity.id, cosmosDataContributorRole)
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRole}'
    scope: '${cosmos.id}/dbs/${cosmosDb.name}'
  }
}

resource devCosmosAccess 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = if (!empty(principalId)) {
  parent: cosmos
  name: guid(cosmosDb.id, principalId, cosmosDataContributorRole)
  properties: {
    principalId: principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRole}'
    scope: '${cosmos.id}/dbs/${cosmosDb.name}'
  }
}

// ---------- AI Search: the policy documents, searchable by keywords and by meaning ----------

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: 'srch-${token}'
  location: searchLocation
  tags: tags
  sku: { name: 'basic' } // the cheapest tier with the semantic ranker; billed per hour, used or not
  properties: {
    replicaCount: 1
    partitionCount: 1
    semanticSearch: 'free' // semantic ranker, up to 1,000 queries a month at no cost
    disableLocalAuth: true // Entra ID only, like OpenAI and Cosmos DB
  }
}

// The app may only query. Loading documents is a separate job with separate rights (you, below).
resource appSearchAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, identity.id, searchIndexReaderRole)
  scope: search
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexReaderRole)
  }
}

resource devSearchAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for role in [searchIndexContributorRole, searchServiceContributorRole]: if (!empty(principalId)) {
    name: guid(search.id, principalId, role)
    scope: search
    properties: {
      principalId: principalId
      principalType: 'User'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
    }
  }
]

// ---------- Container registry: the app pulls its image with its identity ----------

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource appAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRole)
  }
}

// ---------- Container Apps: where the API runs ----------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: tags
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

// Without this, every `azd provision` would replace the running app with the placeholder image below.
module currentApi 'current-image.bicep' = {
  name: 'current-api-image'
  params: { name: 'ca-api-${token}', exists: apiExists }
}
var apiImage = empty(currentApi.outputs.image)
  ? 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' // replaced by `azd deploy`
  : currentApi.outputs.image

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-api-${token}'
  location: location
  tags: union(tags, { 'azd-service-name': 'api' }) // tells `azd deploy` which app to update
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [appAcrPull]
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: { external: true, targetPort: 80 }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAi.properties.endpoint }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: chatDeployment.name }
            { name: 'AZURE_COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
            { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: embeddingDeployment.name }
            { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // which identity DefaultAzureCredential uses
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

output registryLoginServer string = registry.properties.loginServer
output openAiEndpoint string = openAi.properties.endpoint
output openAiDeployment string = chatDeployment.name
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output embeddingDeployment string = embeddingDeployment.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output apiUri string = 'https://${api.properties.configuration.ingress.fqdn}'
