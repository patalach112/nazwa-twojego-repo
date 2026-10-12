-- args: assetUrl, fileExtension, width, height, hideSky, crop
-- Model and Hat render: the uploaded model is inserted into an empty DataModel and
-- photographed from the thumbnail camera. Used for the catalog picture of 3D items.

local assetUrl, fileExtension, width, height, hideSky, crop = ...

assert(type(assetUrl) == "string" and assetUrl ~= "", "no asset URL was delivered")
assert(type(fileExtension) == "string" and fileExtension ~= "", "no file extension was delivered")
assert(type(width) == "number" and width > 0 and type(height) == "number" and height > 0,
    "no valid render size was delivered")

game:GetService("ScriptContext").ScriptsDisabled = true

local model = game:GetObjects(assetUrl)[1]
assert(model ~= nil, "the model asset could not be loaded")
model.Parent = workspace

local image = game:GetService("ThumbnailGenerator"):Click(fileExtension, width, height, hideSky == true, crop == true)
assert(type(image) == "string" and #image > 0, "the model render returned no image data")
return image
