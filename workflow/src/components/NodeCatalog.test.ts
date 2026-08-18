import { describe, expect, it } from 'vitest'
import { Zap } from 'lucide-react'
import { resolveNodeTypeIcon } from './nodeCatalogIcons'

describe('resolveNodeTypeIcon', () => {
  it('uses a safe fallback when the backend catalog is newer than the SPA', () => {
    expect(resolveNodeTypeIcon('action.future_capability')).toBe(Zap)
  })
})
