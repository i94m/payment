/**
 * 顶栏布局的内容最大宽度：顶栏、面包屑、页面内容共用同一个值，
 * 保证三者左边缘对齐；窄屏时仍然铺满（w-full + max-w-*）。
 * 需要调整整体宽度时只改这一处。
 */
export const TOP_LAYOUT_MAX_WIDTH = "max-w-[1400px]"
