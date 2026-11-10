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

@description('Region for Content Safety, if different: groundedness detection is only offered in some regions.')
param contentSafetyLocation string = ''

@description('Service principal GitHub Actions signs in as to run the evaluations. Set by scripts/setup_ci.py.')
param ciPrincipalId string = ''

@description('Region of the second Azure OpenAI account, which the gateway fails over to.')
param openAiSecondaryLocation string = 'swedencentral'

@description('Tokens per minute each user may use, enforced by the gateway. Keep it below the models\' total (2 regions x 10,000), or one user can starve the rest.')
param tokensPerMinutePerUser int = 10000

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
    contentSafetyLocation: empty(contentSafetyLocation) ? location : contentSafetyLocation
    ciPrincipalId: ciPrincipalId
    openAiModelName: openAiModelName
    openAiModelVersion: openAiModelVersion
    embeddingModelName: embeddingModelName
    embeddingModelVersion: embeddingModelVersion
    openAiSecondaryLocation: openAiSecondaryLocation
    tokensPerMinutePerUser: tokensPerMinutePerUser
  }
}

// azd stores these in .azure/<env>/.env; `azd env get-values` prints them.
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer
output AZURE_OPENAI_ENDPOINT string = resources.outputs.openAiEndpoint
output AI_GATEWAY_ENDPOINT string = resources.outputs.gatewayEndpoint
output AZURE_OPENAI_DEPLOYMENT string = resources.outputs.openAiDeployment
output AZURE_COSMOS_ENDPOINT string = resources.outputs.cosmosEndpoint
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = resources.outputs.embeddingDeployment
output AZURE_OPENAI_JUDGE_DEPLOYMENT string = resources.outputs.judgeDeployment
output AZURE_SEARCH_ENDPOINT string = resources.outputs.searchEndpoint
output AUTH_TENANT_ID string = tenant().tenantId
output AUTH_CLIENT_ID string = apiClientId
output CONTENT_SAFETY_ENDPOINT string = resources.outputs.contentSafetyEndpoint
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.appInsightsConnectionString
output AZURE_AI_PROJECT_ENDPOINT string = resources.outputs.foundryProjectEndpoint
output SERVICE_API_URI string = resources.outputs.apiUri
