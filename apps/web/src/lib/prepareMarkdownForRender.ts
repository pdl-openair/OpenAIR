/**
 * 连续 `>` 引用行在部分 Markdown 解析器中会合并成一段；行末补硬换行以贴近源文件分段。
 */
export function prepareMarkdownForRender(source: string): string {
  return source.replace(/^(>.*)$/gm, (line) => (line.endsWith("  ") ? line : `${line}  `));
}
