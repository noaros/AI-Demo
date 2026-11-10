// The AI gateway: API Management in front of Azure OpenAI in two regions.
// The app calls the gateway instead of a model; the gateway decides who may call, how much, and which region answers.
param location string
param tags object
param token string
param openAiAccounts array // [primary, secondary]: names of Azure OpenAI accounts with the same deployments
param allowedCallers array // object ids (oid) allowed to call the gateway
param tokensPerMinutePerUser int

var openAiUserRole = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' // Cognitive Services OpenAI User

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = [for name in openAiAccounts: { name: name }]

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: 'apim-${token}'
  location: location
  tags: tags
  sku: { name: 'BasicV2', capacity: 1 } // billed per hour, like AI Search
  identity: { type: 'SystemAssigned' } // the gateway's own identity, used to call the models
  properties: {
    publisherName: 'Contoso IT'
    publisherEmail: 'it@contoso.com'
  }
}

// The gateway may call the models; nobody needs a key.
resource gatewayOpenAiAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in openAiAccounts: {
  name: guid(openAi[i].id, apim.id, openAiUserRole)
  scope: openAi[i]
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRole)
  }
}]

// One backend per region. The circuit breaker takes a region out of rotation for 30 s after it returns a 429
// or 5xx, or for as long as its Retry-After header says.
resource backends 'Microsoft.ApiManagement/service/backends@2024-05-01' = [for (name, i) in openAiAccounts: {
  parent: apim
  name: 'openai-${i == 0 ? 'primary' : 'secondary'}'
  properties: {
    url: '${openAi[i].properties.endpoint}openai'
    protocol: 'http'
    circuitBreaker: {
      rules: [
        {
          name: 'throttled-or-failing'
          failureCondition: {
            count: 1
            interval: 'PT10S'
            statusCodeRanges: [{ min: 429, max: 429 }, { min: 500, max: 599 }]
          }
          tripDuration: 'PT30S'
          acceptRetryAfter: true
        }
      ]
    }
  }
}]

// Priority 1 gets everything while it's healthy; priority 2 only when 1's circuit is open.
resource pool 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'openai-pool'
  properties: {
    type: 'Pool'
    pool: {
      services: [for (name, i) in openAiAccounts: { id: backends[i].id, priority: i == 0 ? 1 : 2 }]
    }
  }
}

// Every path under /openai is passed through, so the app's OpenAI client works unchanged.
resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'openai'
  properties: {
    displayName: 'Azure OpenAI'
    path: 'openai'
    protocols: ['https']
    subscriptionRequired: false // callers sign in with Entra ID instead of a subscription key
  }
}

resource operations 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for method in ['GET', 'POST']: {
  parent: api
  name: toLower(method)
  properties: { displayName: method, method: method, urlTemplate: '/*' }
}]

resource policy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  dependsOn: [pool]
  properties: {
    format: 'rawxml'
    value: replace(replace(replace(loadTextContent('gateway-policy.xml'),
      '__tenantId__', tenant().tenantId),
      '__allowedCallers__', join(map(allowedCallers, id => '<value>${id}</value>'), '')),
      '__tokensPerMinute__', string(tokensPerMinutePerUser))
  }
}

output endpoint string = apim.properties.gatewayUrl
