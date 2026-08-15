import {
  memo,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  type ComponentProps,
} from 'react'
import { monacoLang, useIsDark } from './MonacoCodeBlock'
import { kirocrewDark, kirocrewLight } from './monacoTheme'
import Clickable from './Clickable'
import { copyToClipboard } from '../utils/clipboard'
import { useStableMonacoLayout } from '../hooks/useStableMonacoLayout'

import { i18nT } from '../i18n/t'
const MonacoDiffEditor = lazy(async () => {
  const { ensureMonacoLocal } = await import('../utils/monacoLocal')
  await ensureMonacoLocal()
  const { DiffEditor } = await import('@monaco-editor/react')
  return { default: DiffEditor }
})

type DiffEditorProps = ComponentProps<typeof MonacoDiffEditor>
type DiffBeforeMount = NonNullable<DiffEditorProps['beforeMount']>
type DiffOnMount = NonNullable<DiffEditorProps['onMount']>
type DiffDisposable = { dispose: () => void }

function extOf(fp: string) {
  const i = fp.lastIndexOf('.')
  return i >= 0 ? fp.slice(i + 1).toLowerCase() : ''
}

/**
 * Side-by-side / unified Monaco diff viewer.
 * Used inside DetailPanel when a file-change chip is clicked.
 */
export default memo(function DiffPanel({ filePath, original, modified, sideBySide = true, lineNumbers = false }: {
  filePath: string
  original: string
  modified: string
  sideBySide?: boolean
  lineNumbers?: boolean
}) {
  const isDark = useIsDark()
  const editorRef = useRef<{ layout: () => void } | null>(null)
  const themesRegisteredRef = useRef(false)
  const diffSubscriptionRef = useRef<DiffDisposable | null>(null)
  const { hostRef: editorHostRef, requestLayout } = useStableMonacoLayout(editorRef)
  const lang = monacoLang(extOf(filePath)) || 'plaintext'
  const beforeMount = useCallback<DiffBeforeMount>((monaco) => {
    if (themesRegisteredRef.current) return
    themesRegisteredRef.current = true
    monaco.editor.defineTheme('kirocrew-dark', kirocrewDark)
    monaco.editor.defineTheme('kirocrew-light', kirocrewLight)
  }, [themesRegisteredRef])
  const onMount = useCallback<DiffOnMount>((editor) => {
    if (editorRef.current) return
    editorRef.current = editor
    requestLayout()
    // Jump to the first change once the diff is computed (async).
    let settled = false
    const revealFirstChange = () => {
      if (settled) return
      settled = true
      const subscription = diffSubscriptionRef.current
      diffSubscriptionRef.current = null
      subscription?.dispose()
      const first = editor.getLineChanges()?.[0]
      if (first) editor.getModifiedEditor().revealLineInCenter(first.modifiedStartLineNumber || first.modifiedEndLineNumber || 1)
    }
    const subscription = editor.onDidUpdateDiff(revealFirstChange)
    if (settled) subscription.dispose()
    else diffSubscriptionRef.current = subscription
  }, [requestLayout])
  useEffect(() => () => {
    diffSubscriptionRef.current?.dispose()
    diffSubscriptionRef.current = null
    editorRef.current = null
  }, [])
  // Show the banner only when both sides carry content and it's the same.
  // Both-empty is a degenerate "new empty file" state, not a meaningful
  // identical comparison — let it fall through to the editor gracefully.
  const isIdentical = original === modified && (!!original || !!modified)

  return (
    <div className="relative w-full h-full flex flex-col">
      {isIdentical ? (
        <div className="flex-1 flex items-center justify-center">
          <span className="text-muted text-sm">
            {i18nT('components.diffPanel.contents_identical')}
          </span>
        </div>
      ) : (
        <div ref={editorHostRef} className="flex-1 min-h-0 overflow-hidden">
          <Suspense fallback={<div className="flex items-center justify-center h-full text-muted text-sm">{i18nT('components.diffPanel.loading_diff')}</div>}>
            <MonacoDiffEditor
              original={original}
              modified={modified}
              language={lang}
              theme={isDark ? 'kirocrew-dark' : 'kirocrew-light'}
              beforeMount={beforeMount}
              onMount={onMount}
              options={{
                readOnly: true,
                renderSideBySide: sideBySide,
                // Monaco silently overrides renderSideBySide when the editor is
                // narrower than renderSideBySideInlineBreakpoint (default
                // 900px) because useInlineViewWhenSpaceIsLimited defaults to
                // true. The chat side panel is well under 900px at every
                // usable width, so the split-view toggle appeared to do
                // nothing — the editor always fell back to the inline view.
                // Opt out so renderSideBySide is authoritative: the toggle is
                // an explicit user choice and Monaco should not second-guess
                // it. Side-by-side in a narrow panel is cramped but it is what
                // the user asked for, and each side scrolls horizontally.
                useInlineViewWhenSpaceIsLimited: false,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                fontSize: 13,
                lineNumbers: lineNumbers ? 'on' : 'off',
                // Layout is driven by useStableMonacoLayout so panel resize and
                // flex reflow cannot make Monaco's observer repaint in a loop.
                automaticLayout: false,
                renderValidationDecorations: 'off',
                guides: { indentation: false },
                stickyScroll: { enabled: false },
                renderLineHighlight: 'none',
                scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
              }}
              height="100%"
            />
          </Suspense>
        </div>
      )}
      <Clickable
        className="shrink-0 flex items-center px-5 py-3 border-t border-border text-[11px] font-mono truncate text-muted cursor-pointer hover:text-text transition-colors"
        title={i18nT('components.diffPanel.click_to_copy_path')}
        onClick={() => copyToClipboard(filePath)}
      >
        {filePath}
      </Clickable>
    </div>
  )
})
