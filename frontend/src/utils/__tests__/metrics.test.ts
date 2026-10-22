import { describe, expect, it } from 'vitest'

import { bestValue, formatMetric } from '../metrics'

describe('evaluation metrics', () => {
  it('formats ratios as percentages and counts as numbers', () => {
    expect(formatMetric('f1_iou50', 0.8125)).toBe('81.3%')
    expect(formatMetric('false_negative', 3)).toBe('3')
    expect(formatMetric('edit_distance', 1.25)).toBe('1.3')
    expect(formatMetric('pck', undefined)).toBe('—')
  })

  it('knows which direction is better and skips columns that are not rankings', () => {
    expect(bestValue('precision_iou50', [0.5, 0.75, undefined])).toBe(0.75)
    expect(bestValue('false_positive', [4, 2])).toBe(2)
    expect(bestValue('true_positive', [4, 2])).toBeUndefined()
    expect(bestValue('recall_iou50', [0.9])).toBeUndefined()
  })
})
