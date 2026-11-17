import { create } from "zustand"
import { STORAGE_PREFIX } from "@/lib/config"

/** 主菜单位置：左侧侧边栏 / 顶部水平导航 */
export type NavPosition = "left" | "top"

// 按模块前缀区分（admin/merchant/agent 各自记忆），与主题偏好一致
const NAV_POSITION_KEY = `${STORAGE_PREFIX}nav-position`

function getStoredNavPosition(): NavPosition {
  if (typeof window === "undefined") return "left"
  // 同步读取：首屏直接按偏好渲染，避免「先左后顶」闪一下
  return localStorage.getItem(NAV_POSITION_KEY) === "top" ? "top" : "left"
}

interface LayoutState {
  navPosition: NavPosition
  setNavPosition: (position: NavPosition) => void
  toggleNavPosition: () => void
}

export const useLayoutStore = create<LayoutState>((set, get) => ({
  navPosition: getStoredNavPosition(),

  setNavPosition: (position) => {
    localStorage.setItem(NAV_POSITION_KEY, position)
    set({ navPosition: position })
  },

  toggleNavPosition: () => {
    get().setNavPosition(get().navPosition === "left" ? "top" : "left")
  },
}))

// 多标签页同步：一个标签切布局，其它标签跟着变
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== NAV_POSITION_KEY) return
    useLayoutStore.setState({
      navPosition: event.newValue === "top" ? "top" : "left",
    })
  })
}
