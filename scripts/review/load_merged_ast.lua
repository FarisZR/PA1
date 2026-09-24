-- Review pipeline: replace the placeholder review document with the merged
-- AST written by scripts/review/diff_ast.py (path in REVIEW_MERGED_AST).

function Pandoc(doc)
  local path = os.getenv("REVIEW_MERGED_AST") or ".review/merged.json"
  local file = assert(io.open(path, "r"), "missing merged review AST: " .. path)
  local merged = pandoc.read(file:read("a"), "json")
  file:close()
  return merged
end
