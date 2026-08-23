// The shell: rail | detail. Two columns, not three.
//
// The lists (pull requests / reviews) and the repo picker are stacked in the
// rail; everything to the right of it is the report you are reading. A third
// column spent a fixed slice of the window on a list you had already used to get
// where you are — and when no repo was selected it held nothing but an empty
// state pointing back at the rail.
//
// Reports are the widest content in the app (finding bodies, diffs, check
// tables), so the space belongs to them.
import { useEffect } from 'react'
import { ScanSearch } from 'lucide-react'

import { useColumnResize, type CollapseConfig } from '../../hooks/useColumnResize'
import { useIsMobile } from '../../hooks/useIsMobile'
import EmptyState from './components/EmptyState'
import LeftRail from './components/LeftRail'
import PrReviewDetail from './components/PrReviewDetail'
import RunDetail from './components/RunDetail'
import { useSage } from './context'
import {
  COLLAPSED_RAIL_WIDTH, MAX_RAIL_WIDTH, MIN_RAIL_WIDTH, RAIL_COLLAPSED_KEY,
  RAIL_WIDTH_KEY, loadRailCollapsed, loadRailWidth,
} from './lib/layout'
import LearningView from './views/LearningView'
import SettingsView from './views/SettingsView'
import LocalReviewView from './views/LocalReviewView'

import { i18nT } from '../../i18n/t'

// Module-level so the hook's memoised resolver is not recreated on every render.
const RAIL_COLLAPSE: CollapseConfig = {
  width: COLLAPSED_RAIL_WIDTH,
  storageKey: RAIL_COLLAPSED_KEY,
  // A phone needs a drill-down: the expanded rail owns the viewport and the
  // detail pane returns when a review is selected.
  whenNarrow: true,
}

/** The 6px vertical drag handle between two columns. */
function Splitter({ handleProps, label }: {
  handleProps: ReturnType<typeof useColumnResize>['handleProps']
  label: string
}) {
  return (
    <div
      {...handleProps}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      title={i18nT('apps.codeReviewSage.workspace.drag_to_resize')}
      className="w-1.5 flex-shrink-0 cursor-col-resize hover:bg-accent/30 transition-colors"
      style={{ touchAction: 'none' }}
    />
  )
}

export default function Workspace() {
  const { mainView, activeRun, selectedPr } = useSage()
  const isMobile = useIsMobile()
  const rail = useColumnResize(
    RAIL_WIDTH_KEY, loadRailWidth, MIN_RAIL_WIDTH, MAX_RAIL_WIDTH,
    RAIL_COLLAPSE, loadRailCollapsed,
  )
  const mobileRailOpen = isMobile && !rail.collapsed
  const collapseRail = rail.collapse
  const mainClassName = `flex-1 min-w-0 min-h-0 flex-col ${mobileRailOpen ? 'hidden' : 'flex'}`

  // A selected review belongs in the detail pane. Collapse the expanded mobile
  // rail after selection so the report is not left in a 48px-wide sliver.
  useEffect(() => {
    if (isMobile && (selectedPr || activeRun)) collapseRail()
  }, [activeRun, collapseRail, isMobile, selectedPr])

  return (
    // overflow-hidden so a mis-sized child can never grow the shell past the
    // viewport and push the rail's identity footer below the fold — each column
    // owns its own scrolling.
    <div className="flex h-full overflow-hidden bg-bg text-text">
      <div
        style={{ width: mobileRailOpen ? '100%' : rail.width }}
        className="flex-shrink-0 min-h-0 flex"
      >
        <LeftRail collapsed={rail.collapsed} onExpand={rail.expand} />
      </div>
      {!isMobile && (
        <Splitter handleProps={rail.handleProps} label={i18nT('apps.codeReviewSage.workspace.resize_sidebar')} />
      )}

      {mainView === 'reviews' ? (
        <>

          {/* A flex COLUMN, not just a flex item: EmptyState (and any future
              child) sizes itself with flex-1, which is inert unless this element
              is itself a flex container — that bug left the empty state
              collapsed to content height and pinned to the top of the pane. */}
          <main className={mainClassName}>
            {selectedPr ? (
              <PrReviewDetail pr={selectedPr} />
            ) : activeRun ? (
              <RunDetail run={activeRun} />
            ) : (
              <EmptyState
                icon={ScanSearch}
                title={i18nT('apps.codeReviewSage.workspace.select_a_review_to_see_its_progress_and_report')}
                hint={i18nT('apps.codeReviewSage.workspace.start_a_new_one_several_can_run_at_once')}
              />
            )}
          </main>
        </>
      ) : mainView === 'local' ? (
        <main className={mainClassName}><LocalReviewView /></main>
      ) : mainView === 'learning' ? (
        <main className={mainClassName}><LearningView /></main>
      ) : (
        <main className={mainClassName}><SettingsView /></main>
      )}
    </div>
  )
}
