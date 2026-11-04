import assert from 'node:assert/strict'
import test from 'node:test'
import Feature from 'ol/Feature.js'
import { elderAccessStyle } from '../src/elderAccessStyle.js'

test('模型未覆盖区默认隐藏且随时间档切换', () => {
  const feature = new Feature({ kind: 'uncovered', minutes: 15 })
  assert.equal(elderAccessStyle(feature, 15, '#15803d'), undefined)
  assert.ok(elderAccessStyle(feature, 15, '#15803d', true))
  assert.equal(elderAccessStyle(feature, 10, '#15803d', true), undefined)
  assert.equal(elderAccessStyle(new Feature({ kind: 'study_area' }), 15, '#15803d'), undefined)
})

test('每个时间档只显示本档道路和范围', () => {
  for (const selected of [5, 10, 15, 20, 25, 30]) {
    for (const minutes of [5, 10, 15, 20, 25, 30]) {
      for (const kind of ['road', 'area']) {
        const style = elderAccessStyle(new Feature({ kind, minutes }), selected, '#15803d')
        assert.equal(Boolean(style), minutes === selected)
        if (style && kind === 'area') {
          assert.equal(style.getFill().getColor(), '#15803d75')
          assert.equal(style.getStroke().getWidth(), 1.5)
        }
      }
    }
  }
})

test('切换时间仍保留设施名称和接入段', () => {
  for (const minutes of [5, 10, 15]) {
    const style = elderAccessStyle(new Feature({ kind: 'facility', name: '测试设施' }), minutes, '#15803d')
    assert.equal(style.getText().getText(), '测试设施')
    assert.ok(elderAccessStyle(new Feature({ kind: 'connector' }), minutes, '#15803d'))
  }
})

test('历史边界默认隐藏且仅高亮选中代码', () => {
  const feature = new Feature({ kind: 'village_boundary', code: '123' })
  assert.equal(elderAccessStyle(feature, 15, '#15803d'), undefined)
  assert.equal(elderAccessStyle(feature, 15, '#15803d', false, '456'), undefined)
  assert.equal(elderAccessStyle(feature, 15, '#15803d', false, '123').getStroke().getColor(), '#7c3aed')
})

test('多时长叠加显示全部范围，短时段在上，道路和灰色面仍只显示当前档', () => {
  const styles = [5, 10, 15, 20, 25, 30].map(minutes =>
    elderAccessStyle(new Feature({ kind: 'area', minutes }), 15, '#15803d', true, '', true))
  assert.ok(styles.every(Boolean))
  for (let index = 1; index < styles.length; index++) {
    assert.ok(styles[index - 1].getZIndex() > styles[index].getZIndex())
  }
  assert.equal(elderAccessStyle(new Feature({ kind: 'road', minutes: 10 }), 15, '#15803d', true, '', true), undefined)
  assert.equal(elderAccessStyle(new Feature({ kind: 'uncovered', minutes: 10 }), 15, '#15803d', true, '', true), undefined)
  assert.ok(elderAccessStyle(new Feature({ kind: 'road', minutes: 15 }), 15, '#15803d', true, '', true))
})
