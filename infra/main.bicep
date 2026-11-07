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

param openAiModelName string = 'gpt-5-mini'
param openAiModelVersion string = '2025-08-07'

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
    openAiModelName: openAiModelName
    openAiModelVersion: openAiModelVersion
  }
}

// azd stores these in .azure/<env>/.env; `azd env get-values` prints them.
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer
output AZURE_OPENAI_ENDPOINT string = resources.outputs.openAiEndpoint
output AZURE_OPENAI_DEPLOYMENT string = resources.outputs.openAiDeployment
output SERVICE_API_URI string = resources.outputs.apiUri
