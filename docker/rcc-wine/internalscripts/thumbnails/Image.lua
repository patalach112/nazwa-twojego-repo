-- args: assetUrl, fileExtension, width, height
-- Texture render for Image and Decal-style assets: the item picture in the catalog.
-- RCC 2020 loads this file from internalscripts/thumbnails/<Type>.lua and passes
-- Settings.Arguments from the JSON job as varargs. Contract: docker/renderer/README.md.

local assetUrl, fileExtension, width, height = ...

assert(type(assetUrl) == "string" and assetUrl ~= "", "no asset URL was delivered")
assert(type(fileExtension) == "string" and fileExtension ~= "", "no file extension was delivered")
assert(type(width) == "number" and width > 0 and type(height) == "number" and height > 0,
    "no valid render size was delivered")

game:GetService("ScriptContext").ScriptsDisabled = true

local image = game:GetService("ThumbnailGenerator"):ClickTexture(assetUrl, fileExtension, width, height)
assert(type(image) == "string" and #image > 0, "the texture render returned no image data")
return image
