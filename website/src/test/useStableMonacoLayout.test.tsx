import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useRef } from 'react'
import { useStableMonacoLayout } from '../hooks/useStableMonacoLayout'

interface TestEditor {
  layout: ReturnType<typeof vi.fn>
}

interface TestObserver {
  callback: ResizeObserverCallback
  observe: ReturnType<typeof vi.fn>
  disconnect: ReturnType<typeof vi.fn>
  emit: (width: number, height: number) => void
}

const observers: TestObserver[] = []

class CapturingResizeObserver implements TestObserver {
  callback: ResizeObserverCallback
  observe = vi.fn()
  disconnect = vi.fn()

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    observers.push(this)
  }

  emit(width: number, height: number) {
    this.callback([{ contentRect: { width, height } } as ResizeObserverEntry], this as unknown as ResizeObserver)
  }
}

interface HarnessProps {
  editor: TestEditor
  showHost: boolean
  hostKey?: string
  width: number
  height: number
}

function Harness({ editor, showHost, hostKey, width, height }: HarnessProps) {
  const editorRef = useRef(editor)
  const { hostRef } = useStableMonacoLayout(editorRef)
  return showHost ? (
    <div
      key={hostKey}
      ref={(node) => {
        if (node) {
          vi.spyOn(node, 'getBoundingClientRect').mockReturnValue({ width, height } as DOMRect)
        }
        hostRef(node)
      }}
    />
  ) : null
}

describe('useStableMonacoLayout', () => {
  let frames: FrameRequestCallback[]
  let cancelFrame: ReturnType<typeof vi.fn>

  beforeEach(() => {
    observers.length = 0
    frames = []
    cancelFrame = vi.fn()
    vi.stubGlobal('ResizeObserver', CapturingResizeObserver)
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frames.push(callback)
      return frames.length
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(cancelFrame)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function flushFrames() {
    const pending = frames.splice(0)
    act(() => {
      pending.forEach((callback) => callback(0))
    })
  }

  it('attaches when a conditionally mounted host appears', () => {
    const editor = { layout: vi.fn() }
    const view = render(<Harness editor={editor} showHost={false} width={100} height={50} />)
    expect(observers).toHaveLength(0)

    view.rerender(<Harness editor={editor} showHost width={100} height={50} />)

    expect(observers).toHaveLength(1)
    expect(observers[0].observe).toHaveBeenCalledTimes(1)
  })

  it('coalesces resize notifications and lays out with the latest nonzero size', () => {
    const editor = { layout: vi.fn() }
    const view = render(<Harness editor={editor} showHost width={100} height={50} />)
    const observer = observers[0]
    flushFrames()
    editor.layout.mockClear()

    act(() => {
      observer.emit(0, 0)
      observer.emit(200, 100)
      observer.emit(320, 180)
    })
    expect(editor.layout).not.toHaveBeenCalled()
    expect(frames).toHaveLength(1)

    flushFrames()

    expect(editor.layout).toHaveBeenCalledTimes(1)
    expect(editor.layout).toHaveBeenCalledWith({ width: 320, height: 180 })

    act(() => observer.emit(320, 180))
    flushFrames()
    expect(editor.layout).toHaveBeenCalledTimes(1)
    view.unmount()
  })

  it('reattaches for a replacement host and cancels pending work on cleanup', () => {
    const editor = { layout: vi.fn() }
    const view = render(<Harness editor={editor} showHost hostKey="one" width={100} height={50} />)
    const firstObserver = observers[0]
    expect(firstObserver.observe).toHaveBeenCalledTimes(1)

    view.rerender(<Harness editor={editor} showHost hostKey="two" width={240} height={120} />)

    expect(firstObserver.disconnect).toHaveBeenCalledTimes(1)
    expect(observers).toHaveLength(2)
    expect(observers[1].observe).toHaveBeenCalledTimes(1)

    view.unmount()
    expect(observers[1].disconnect).toHaveBeenCalledTimes(1)
    expect(cancelFrame).toHaveBeenCalled()
    flushFrames()
    expect(editor.layout).not.toHaveBeenCalled()
  })
})
