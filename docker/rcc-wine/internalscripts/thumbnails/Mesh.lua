-- args: assetUrl, fileExtension, width, height
-- Mesh render: the mesh is attached to a part through a SpecialMesh, like the
-- legacy Mesh thumbnail script, and then photographed.

local assetUrl, fileExtension, width, height = ...

assert(type(assetUrl) == "string" and assetUrl ~= "", "no asset URL was delivered")
assert(type(fileExtension) == "string" and fileExtension ~= "", "no file extension was delivered")
assert(type(width) == "number" and width > 0 and type(height) == "number" and height > 0,
    "no valid render size was delivered")

game:GetService("ScriptContext").ScriptsDisabled = true

local part = Instance.new("Part")
part.Parent = workspace
local mesh = Instance.new("SpecialMesh")
mesh.MeshId = assetUrl
mesh.Parent = part

local image = game:GetService("ThumbnailGenerator"):Click(fileExtension, width, height, true, true)
assert(type(image) == "string" and #image > 0, "the mesh render returned no image data")
return image
