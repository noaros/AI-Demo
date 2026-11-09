targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment; used to name the resource group and tag resources.')
param environmentName string

@minLength(1)
@description('Region for all resources. Must offer the chosen model with GlobalStandard deployments.')
param location string

@description('Object ID of the person running azd. Gets OpenAI access so the app can run locally.')
param principalId string = ''

@description('Region for AI Search, if different: set AZURE_SEARCH_LOCATION when the main region has no Search capacity.')
param searchLocation string = ''

@description('Set by azd: true once the app has been deployed, so provisioning keeps its image.')
param apiExists bool = false

@description('Application (client) id of the Entra ID app registration users sign in to. Set by the preprovision hook.')
param apiClientId string

param openAiModelName string = 'gpt-5-mini'
param openAiModelVersion string = '2025-08-07'
param embeddingModelName string = 'text-embedding-3-small'
param embeddingModelVersion string = '1'

var tags = { 'azd-env-name': environmentName }

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: rg
  params: {
    location: location
    tags: tags
    principalId: principalId
    searchLocation: empty(searchLocation) ? location : searchLocation
    apiExists: apiExists
    apiClientId: apiClientId
    openAiModelName: openAiModelName
    openAiModelVersion: openAiModelVersion
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
  }
}

// azd stores these in .azure/<env>/.env; `azd env get-values` prints them.
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer
output AZURE_OPENAI_ENDPOINT string = resources.outputs.openAiEndpoint
output AZURE_OPENAI_DEPLOYMENT string = resources.outputs.openAiDeployment
output AZURE_COSMOS_ENDPOINT string = resources.outputs.cosmosEndpoint
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = resources.outputs.embeddingDeployment
output AZURE_SEARCH_ENDPOINT string = resources.outputs.searchEndpoint
output AUTH_TENANT_ID string = tenant().tenantId
output AUTH_CLIENT_ID string = apiClientId
output SERVICE_API_URI string = resources.outputs.apiUri
