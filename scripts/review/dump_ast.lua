-- Review pipeline: write the executed Pandoc AST of one paper version to JSON.
--
-- Runs at Quarto's pre-ast stage, so code cells are already executed and
-- chapters are included, but Quarto has not yet turned figures, tables, and
-- cross-references into custom nodes. The document itself is left unchanged.
--
-- REVIEW_AST_OUT     path of the JSON file to write
-- REVIEW_MEDIA_DIR   directory (absolute) to copy referenced images into
-- REVIEW_MEDIA_REF   the same directory as the review render will reference it

local out = os.getenv("REVIEW_AST_OUT")
local media_dir = os.getenv("REVIEW_MEDIA_DIR")
local media_ref = os.getenv("REVIEW_MEDIA_REF")

local function copy_file(source, destination)
  local input = io.open(source, "rb")
  if not input then
    return false
  end
  local data = input:read("a")
  input:close()
  local output = assert(io.open(destination, "wb"))
  output:write(data)
  output:close()
  return true
end

function Pandoc(doc)
  if not out then
    return nil
  end

  -- Generated figures live in pa1_files/, which Quarto deletes after the
  -- render; keep a copy so the review render can still show and compare them.
  local copy = doc:walk({
    Image = function(image)
      if not media_dir or image.src:match("^%a+://") then
        return nil
      end
      local name = image.src:gsub("[/\\]", "__")
      if copy_file(image.src, media_dir .. "/" .. name) then
        image.src = media_ref .. "/" .. name
        return image
      end
      return nil
    end,
  })

  local file = assert(io.open(out, "w"))
  file:write(pandoc.write(copy, "json"))
  file:close()
  return nil
end
