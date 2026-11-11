import test from 'node:test'
import assert from 'node:assert/strict'
import { areaMu } from './elderAccessPresentation.js'

test('面积仅换算一次且兼容旧记录', () => {
  assert.equal(areaMu(10000, 999), '15.00')
  assert.equal(areaMu(null, 15), '15.00')
  assert.equal(areaMu(0), '0.00')
  assert.equal(areaMu(null), '未记录')
  assert.equal(areaMu(NaN), '未记录')
})
