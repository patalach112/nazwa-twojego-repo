-- Stage 2 probe: render the current DataModel with the real ThumbnailGenerator.
-- The JSON job delivers Settings.Arguments as varargs, exactly like the
-- ExecuteScript probe. No asset, place file or network access is required.
local fileExtension, width, height, variant = ...

assert(type(fileExtension) == "string" and fileExtension ~= "", "the file extension argument was not delivered")
assert(type(width) == "number" and width > 0, "the width argument was not delivered")
assert(type(height) == "number" and height > 0, "the height argument was not delivered")
assert(variant == "scene" or variant == "empty", "the variant argument was not delivered")

game:GetService("ScriptContext").ScriptsDisabled = true

if variant == "scene" then
    -- Two differently coloured, local parts: no texture download, no place file.
    local cube = Instance.new("Part")
    cube.Size = Vector3.new(6, 3, 4)
    cube.BrickColor = BrickColor.new("Bright red")
    cube.Anchored = true
    cube.Position = Vector3.new(-2, 6, 0)
    cube.Parent = workspace

    local ball = Instance.new("Part")
    ball.Shape = Enum.PartType.Ball
    ball.Size = Vector3.new(5, 5, 5)
    ball.BrickColor = BrickColor.new("Bright blue")
    ball.Anchored = true
    ball.Position = Vector3.new(4, 8, 0)
    ball.Parent = workspace
end

-- hideSky and crop are the documented legacy arguments; the sky is skipped so
-- the probe cannot depend on downloaded sky textures.
local image = game:GetService("ThumbnailGenerator"):Click(fileExtension, width, height, true, true)

assert(type(image) == "string" and #image > 0, "the thumbnail generator returned no image data")
return image
