// Backend columns count Unicode characters; CodeMirror offsets use UTF-16.
export function diagnosticRanges(doc, diagnostics = []) {
  const position = (row, column) => {
    const line = doc.line(Math.max(1, Math.min(Number(row) || 1, doc.lines)))
    const characters = Array.from(line.text)
    const count = Math.max(0, Math.min((Number(column) || 1) - 1, characters.length))
    return line.from + characters.slice(0, count).join('').length
  }
  return diagnostics.map(item => {
    const from = position(item.line, item.column)
    const to = Math.max(from, position(item.end_line ?? item.line, item.end_column ?? ((item.column || 1) + 1)))
    return { from, to, message: String(item.message || '语法错误') }
  }).sort((a, b) => a.from - b.from || a.to - b.to)
}
