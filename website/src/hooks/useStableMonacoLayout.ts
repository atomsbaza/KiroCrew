import { useCallback, useEffect, useRef, useState, type MutableRefObject, type RefCallback } from 'react'

interface LayoutDimension {
  width: number
  height: number
}

interface LayoutableEditor {
  layout: (dimension?: LayoutDimension) => void
}

function sameDimension(a: LayoutDimension | null, b: LayoutDimension | null) {
  return a?.width === b?.width && a?.height === b?.height
}

function hostDimension(host: HTMLElement): LayoutDimension | null {
  const rect = host.getBoundingClientRect()
  const width = rect.width || host.clientWidth
  const height = rect.height || host.clientHeight
  return width > 0 && height > 0 ? { width, height } : null
}

/**
 * Keep a Monaco editor in sync with its host without Monaco's own continuous
 * automatic-layout observer. The observer is deliberately frame-coalesced:
 * panel drags and flex reflows can emit several size notifications in one
 * frame, but the editor only needs one layout after the final size settles.
 *
 * The callback ref is intentional. Some callers conditionally mount the host,
 * so an object ref would let the first effect run before the host exists and
 * never give the observer a chance to attach.
 */
export function useStableMonacoLayout(editorRef: MutableRefObject<LayoutableEditor | null>): {
  hostRef: RefCallback<HTMLElement>
  requestLayout: () => void
} {
  const [host, setHost] = useState<HTMLElement | null>(null)
  const hostNodeRef = useRef<HTMLElement | null>(null)
  const frameRef = useRef<number | null>(null)
  const latestSizeRef = useRef<LayoutDimension | null>(null)
  const lastLayoutSizeRef = useRef<LayoutDimension | null>(null)

  const scheduleLayout = useCallback(() => {
    if (frameRef.current !== null) return
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = null
      const size = latestSizeRef.current
      const editor = editorRef.current
      if (!size || !editor || sameDimension(size, lastLayoutSizeRef.current)) return
      editor.layout(size)
      lastLayoutSizeRef.current = size
    })
  }, [editorRef])

  const requestLayout = useCallback(() => {
    const currentHost = hostNodeRef.current
    if (!currentHost) return
    const size = hostDimension(currentHost)
    if (!size) return
    latestSizeRef.current = size
    if (!sameDimension(size, lastLayoutSizeRef.current)) scheduleLayout()
  }, [scheduleLayout])

  const hostRef = useCallback<RefCallback<HTMLElement>>((node) => {
    if (hostNodeRef.current === node) return
    hostNodeRef.current = node
    setHost(node)
  }, [])

  useEffect(() => {
    latestSizeRef.current = null
    lastLayoutSizeRef.current = null
    if (!host || typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width <= 0 || height <= 0) return
      const size = { width, height }
      if (sameDimension(size, latestSizeRef.current)) return
      latestSizeRef.current = size
      scheduleLayout()
    })

    observer.observe(host)
    requestLayout()

    return () => {
      observer.disconnect()
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current)
        frameRef.current = null
      }
      latestSizeRef.current = null
      lastLayoutSizeRef.current = null
    }
  }, [host, requestLayout, scheduleLayout])

  return { hostRef, requestLayout }
}
