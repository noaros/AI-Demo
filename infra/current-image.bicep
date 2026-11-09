// The image a container app is running now, or '' if it doesn't exist yet. A module of its own:
// reading the app and updating it in the same deployment would be a circular dependency.
param name string
param exists bool

resource app 'Microsoft.App/containerApps@2024-03-01' existing = if (exists) {
  name: name
}

output image string = exists ? app!.properties.template.containers[0].image : ''
