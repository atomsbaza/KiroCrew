import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Run } from '../apps/code-review-sage/lib/types'

const sage: Record<string, unknown> = {}

vi.mock('../apps/code-review-sage/context', () => ({
  useSage: () => sage,
}))

vi.mock('../apps/code-review-sage/components/PrSourcePanel', () => ({
  SourceError: () => null,
  usePrSource: () => ({ data: undefined, isLoading: false, error: null }),
}))

vi.mock('../apps/code-review-sage/components/ReviewModelPicker', () => ({
  default: () => null,
}))

vi.mock('../apps/code-review-sage/components/ReviewChat', () => ({
  default: () => null,
}))

vi.mock('../apps/code-review-sage/components/DraftReviewActions', () => ({
  default: () => null,
}))

import PrReviewDetail from '../apps/code-review-sage/components/PrReviewDetail'

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    run_id: 'run-1',
    changes: ['https://github.com/acme/widgets/pull/7'],
    status: 'error',
    started_at: '2026-08-01T00:00:00Z',
    error: 'zzz worker died',
    model: 'model-concrete',
    ...overrides,
  }
}

const pr = {
  url: 'https://github.com/acme/widgets/pull/7',
  number: 7,
  change_id: 'GH-acme-widgets-7',
  title: 'Tighten the cookie jar',
}

beforeEach(() => {
  vi.clearAllMocks()
  Object.keys(sage).forEach(key => delete sage[key])
  Object.assign(sage, {
    prRun: makeRun(),
    report: null,
    reportLoading: false,
    reportError: null,
    startReview: { mutate: vi.fn(), isPending: false, error: null },
    cancelRun: vi.fn(),
    cancelling: false,
    pool: null,
    archiveRun: vi.fn(),
    archiving: false,
    archiveError: null,
    runs: [],
    postComments: vi.fn(),
    postCommentGroups: vi.fn(async () => {}),
    posting: false,
    postError: null,
    postingSelection: undefined,
    reviewModel: 'auto',
    setReviewModel: vi.fn(),
  })
})

describe('PrReviewDetail retry model preservation', () => {
  it('uses the saved model for both failure-notice and header retries', async () => {
    const user = userEvent.setup()
    render(<PrReviewDetail pr={pr} />)

    await user.click(screen.getByRole('button', { name: /run it again/i }))
    expect((sage.startReview as { mutate: ReturnType<typeof vi.fn> }).mutate)
      .toHaveBeenCalledWith({ changes: [pr.url], model: 'model-concrete' })

    const mutate = (sage.startReview as { mutate: ReturnType<typeof vi.fn> }).mutate
    mutate.mockClear()
    await user.click(screen.getByRole('button', { name: /retry review/i }))
    expect(mutate).toHaveBeenCalledWith({ changes: [pr.url], model: 'model-concrete' })
  })
})
