import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { renderWithProviders } from './helpers'
import FindingCard from '../apps/code-review-sage/components/FindingCard'
import ReviewFixSetup from '../apps/code-review-sage/components/ReviewFixSetup'
import ReviewFixTaskPanel from '../components/ReviewFixTaskPanel'
import type {
  ReviewFixFindingSnapshot,
  ReviewFixGit,
  ReviewFixGroup,
  ReviewFixMetadata,
  ReviewFixModel,
  ReviewFixState,
  ReviewFixTarget,
  ReviewFixTaskResponse,
  ReviewFixValidation,
} from '../types'

const setupMocks = vi.hoisted(() => {
  class MockSageApiError extends Error {
    code: string

    constructor(message: string, code: string) {
      super(message)
      this.code = code
    }
  }

  return {
    createFixTask: vi.fn(),
    MockSageApiError,
  }
})

vi.mock('../apps/code-review-sage/api', () => ({
  sageApi: { createFixTask: setupMocks.createFixTask },
  SageApiError: setupMocks.MockSageApiError,
}))

vi.mock('../apps/code-review-sage/components/ReviewModelPicker', () => ({
  default: ({ value, onChange, disabled }: {
    value: string
    onChange: (value: string) => void
    disabled?: boolean
  }) => (
    <input
      aria-label="Review model"
      value={value}
      disabled={disabled}
      onChange={event => onChange(event.currentTarget.value)}
    />
  ),
}))

vi.mock('../components/SimpleSelect', () => ({
  default: ({
    options,
    optionLabels,
    value,
    onChange,
    disabled,
    'aria-label': ariaLabel,
  }: {
    options: string[]
    optionLabels?: string[]
    value: string
    onChange: (value: string) => void
    disabled?: boolean
    'aria-label'?: string
  }) => (
    <select
      aria-label={ariaLabel}
      disabled={disabled}
      value={value}
      onChange={event => onChange(event.currentTarget.value)}
    >
      {options.map((option, index) => (
        <option key={option} value={option}>
          {optionLabels?.[index] ?? option}
        </option>
      ))}
    </select>
  ),
}))

const finding: ReviewFixFindingSnapshot = {
  key: 'change-1:finding:0',
  title: 'Fix target',
  severity: 'red',
  body: 'The target is inconsistent.',
  file_path: 'src/example.py',
  line: 12,
  end_line: 14,
  fingerprint: 'finding-fingerprint',
  suggested_fix: 'Align the target behavior.',
}

const target: ReviewFixTarget = {
  mode: 'current_branch',
  repo_root: '/repo',
  target_path: '/repo',
  target_ref: 'main',
  branch_name: 'main',
  head_sha: 'target-head-sha',
  dirty_fingerprint: 'target-fingerprint',
  tracked_paths: [],
  untracked_paths: [],
  upstream: 'origin/main',
  remote: 'origin',
}

const git: ReviewFixGit = {
  candidate_worktree_path: '/tmp/candidate',
  candidate_branch: 'review-fix/task-1',
  candidate_ref: 'candidate-head-sha',
  destination_worktree_path: '/repo',
  destination_branch: 'main',
  proposed_branch: 'review-fix/task-1',
  confirmed_branch: '',
  remote: 'origin',
  upstream: 'origin/main',
  push_preview: {},
  push_result: {},
  rereview_run_id: '',
}

const model: ReviewFixModel = {
  requested_model: 'auto',
  provider: 'acp',
  resolved_model_id: 'served-model',
  advertised_model_ids: ['served-model'],
  resolved_at: 1,
}

function validation(kind: string, groupRevision = 2): ReviewFixValidation {
  return {
    validation_id: `${kind}-validation`,
    group_id: 'group-1',
    group_revision: groupRevision,
    kind,
    command: kind === 'test' ? ['pytest', '-q'] : ['npm', 'run', 'build'],
    exit_code: 0,
    passed: true,
    started_at: 1,
    finished_at: 2,
    duration_secs: 1,
  }
}

function group(overrides: Partial<ReviewFixGroup> = {}): ReviewFixGroup {
  return {
    group_id: 'group-1',
    finding_keys: [finding.key],
    hard_edges: [],
    soft_edges: [],
    reasons: [],
    affected_files: ['src/example.py'],
    hard: true,
    state: 'ready_to_apply',
    revision: 2,
    candidate_patch_id: 'patch-1',
    candidate_base_sha: 'target-head-sha',
    candidate_head_sha: 'candidate-head-sha',
    patch_path: '/tmp/candidate.patch',
    diff_path: '/tmp/candidate.diff',
    validation_runs: [],
    apply_confirmed: false,
    applied_at: 0,
    commit_hash: '',
    commit_message: '',
    ...overrides,
  }
}

function response(
  state: ReviewFixState,
  groupOverrides: Partial<ReviewFixGroup> = {},
  modelOverrides: Partial<ReviewFixModel> = {},
): ReviewFixTaskResponse {
  const metadata: ReviewFixMetadata = {
    review_run_id: 'sage-run-1',
    pr_url: 'https://github.com/example/repo/pull/42',
    source_head_sha: 'source-head-sha',
    selected_finding_keys: [finding.key],
    finding_snapshots: [finding],
    state,
    revision: 7,
    target,
    model: { ...model, ...modelOverrides },
    groups: [group(groupOverrides)],
    git,
    chat: {
      session_key: 'session-1',
      slot_id: 'slot-1',
      review_run_id: 'sage-run-1',
      task_id: 'task-1',
      revision: 7,
    },
    blocked_reason: '',
    attempts: {},
    logs: [],
    diff_paths: [],
    artifact_paths: [],
    audit_log: [],
    created_at: 1,
    updated_at: 2,
  }
  return { task_id: 'task-1', revision: 7, state, review_fix: metadata }
}

function transportFor(value: ReviewFixTaskResponse) {
  return {
    status: vi.fn().mockResolvedValue(value),
    action: vi.fn().mockResolvedValue(value),
  }
}

describe('Review Fix finding selection', () => {
  it('keeps post and fix selection independent', async () => {
    const onPostToggle = vi.fn()
    const onFixToggle = vi.fn()
    renderWithProviders(
      <FindingCard
        finding={{
          dimension: 'correctness',
          severity: 'red',
          headline: finding.title,
          observation: finding.body,
          file: finding.file_path,
          line: finding.line,
        }}
        selectable
        selected={false}
        onToggle={onPostToggle}
        label="src/example.py:12"
        fixSelectable
        fixSelected={false}
        onToggleFix={onFixToggle}
      />,
    )

    await userEvent.click(screen.getByRole('checkbox', { name: /to fix/i }))
    expect(onFixToggle).toHaveBeenCalledTimes(1)
    expect(onPostToggle).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('checkbox', { name: /to draft/i }))
    expect(onPostToggle).toHaveBeenCalledTimes(1)
  })

  it('does not render fix affordances when the row is ineligible', () => {
    renderWithProviders(
      <FindingCard
        finding={{ severity: 'yellow', headline: finding.title }}
        fixSelectable={false}
        onFix={vi.fn()}
      />,
    )

    expect(screen.queryByRole('checkbox', { name: /to fix/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /ask to fix/i })).not.toBeInTheDocument()
  })
})

describe('ReviewFixSetup', () => {
  it('sends the selected findings and target settings in the create payload', async () => {
    setupMocks.createFixTask.mockResolvedValue(response('awaiting_group_confirmation'))
    const onCreated = vi.fn()
    renderWithProviders(
      <ReviewFixSetup
        findings={[finding]}
        reviewRunId="sage-run-1"
        prUrl="https://github.com/example/repo/pull/42"
        sourceHeadSha="source-head-sha"
        model="served-model"
        onModelChange={vi.fn()}
        onCreated={onCreated}
        onClose={vi.fn()}
      />,
    )

    await userEvent.type(screen.getByRole('textbox', { name: /target repository/i }), '/repo')
    fireEvent.change(screen.getByRole('combobox', { name: /target mode/i }), {
      target: { value: 'new_worktree' },
    })
    await userEvent.click(screen.getByRole('button', { name: /create fix task/i }))

    await waitFor(() => expect(setupMocks.createFixTask).toHaveBeenCalledTimes(1))
    expect(setupMocks.createFixTask).toHaveBeenCalledWith({
      target_path: '/repo',
      findings: [finding],
      review_run_id: 'sage-run-1',
      pr_url: 'https://github.com/example/repo/pull/42',
      source_head_sha: 'source-head-sha',
      target_mode: 'new_worktree',
      model: 'served-model',
    })
    expect(onCreated).toHaveBeenCalledTimes(1)
    expect(onCreated.mock.calls[0]?.[0]).toEqual(response('awaiting_group_confirmation'))
  })
})

describe('ReviewFixTaskPanel', () => {
  it('keeps Apply disabled until both validation kinds pass', async () => {
    const transport = transportFor(
      response('ready_to_apply', { validation_runs: [validation('test')] }),
    )
    renderWithProviders(<ReviewFixTaskPanel taskId="task-1" transport={transport} />)

    expect(await screen.findByRole('button', { name: 'Apply' })).toBeDisabled()
  })

  it('sends CAS-protected apply after test and build pass', async () => {
    const transport = transportFor(
      response('ready_to_apply', {
        validation_runs: [validation('test'), validation('build')],
      }),
    )
    renderWithProviders(<ReviewFixTaskPanel taskId="task-1" transport={transport} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(transport.action).toHaveBeenCalledTimes(1))
    expect(transport.action).toHaveBeenCalledWith(
      'task-1',
      expect.objectContaining({
        action: 'apply_group',
        expected_revision: 7,
        target_fingerprint: 'target-fingerprint',
        confirmed: true,
        confirmation_intent: 'apply_review_fix_group',
        group_id: 'group-1',
        expected_group_revision: 2,
      }),
    )
  })

  it('requires both commands before running validation', async () => {
    const transport = transportFor(response('awaiting_validation', { state: 'proposed' }))
    renderWithProviders(<ReviewFixTaskPanel taskId="task-1" transport={transport} />)

    const runValidation = await screen.findByRole('button', { name: 'Run validation' })
    expect(runValidation).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox', { name: 'Test command' }), 'pytest -q')
    await userEvent.type(screen.getByRole('textbox', { name: 'Build command' }), 'npm run build')
    expect(runValidation).toBeEnabled()

    await userEvent.click(runValidation)
    await waitFor(() => expect(transport.action).toHaveBeenCalledTimes(1))
    expect(transport.action).toHaveBeenCalledWith(
      'task-1',
      expect.objectContaining({
        action: 'validate_group',
        test_command: ['pytest', '-q'],
        build_command: ['npm', 'run', 'build'],
        expected_revision: 7,
        expected_group_revision: 2,
      }),
    )
  })

  it('starts execution after all groups are confirmed', async () => {
    const transport = transportFor(
      response('awaiting_group_confirmation', { state: 'confirmed' }),
    )
    renderWithProviders(<ReviewFixTaskPanel taskId="task-1" transport={transport} />)

    const start = await screen.findByRole('button', { name: 'Start execution' })
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument()

    await userEvent.click(start)
    await waitFor(() => expect(transport.action).toHaveBeenCalledTimes(1))
    expect(transport.action).toHaveBeenCalledWith('task-1', {
      action: 'resume',
      expected_revision: 7,
      target_fingerprint: 'target-fingerprint',
      confirmed: true,
    })
  })

  it('keeps execution unavailable until grouping is confirmed', async () => {
    const transport = transportFor(
      response('awaiting_group_confirmation', { state: 'proposed' }),
    )
    renderWithProviders(<ReviewFixTaskPanel taskId="task-1" transport={transport} />)

    expect(await screen.findByRole('button', { name: 'Confirm grouping' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start execution' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument()
  })

  it('shows Pause only while execution is running', async () => {
    const transport = transportFor(response('running', { state: 'executing' }))
    renderWithProviders(<ReviewFixTaskPanel taskId="task-1" transport={transport} />)

    expect(await screen.findByRole('button', { name: 'Pause' })).toBeInTheDocument()
  })

  it.each(['awaiting_validation', 'ready_to_apply'] as const)(
    'does not show Pause in %s',
    async (state) => {
      const transport = transportFor(response(state))
      renderWithProviders(<ReviewFixTaskPanel taskId="task-1" transport={transport} />)

      expect(await screen.findByRole('heading', { name: 'Review Fix task' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument()
    },
  )


  it('offers model resolution when execution is blocked', async () => {
    const transport = transportFor(
      response('blocked_model_resolution', {}, {
        requested_model: 'auto',
        resolved_model_id: '',
      }),
    )
    renderWithProviders(<ReviewFixTaskPanel taskId="task-1" transport={transport} />)

    const resolve = await screen.findByRole('button', { name: 'Resolve model' })
    await userEvent.click(resolve)
    await waitFor(() => expect(transport.action).toHaveBeenCalledTimes(1))
    expect(transport.action).toHaveBeenCalledWith(
      'task-1',
      expect.objectContaining({
        action: 'resolve_model',
        model: 'auto',
        expected_revision: 7,
        target_fingerprint: 'target-fingerprint',
      }),
    )
  })
})
