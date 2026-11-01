import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { displayNumber, displayNumericText, displayNumericJson } from '../src/numberDisplay.js'
import { summarizeLog } from '../src/logSummary.js'

test('numbers show at most two decimals, omit floating tails and never invent feedback',()=>{
  assert.equal(displayNumber(406.2203392985117),'406.22')
  assert.equal(displayNumber(3.9458535389364035e-15),'0')
  assert.equal(displayNumber(-.00001),'0')
  assert.equal(displayNumber(90),'90')
  for(const value of [undefined,null,NaN,Infinity,'12.3'])assert.equal(displayNumber(value),'—')
})
test('visible command, units and exponent numbers shorten without rewriting IDs or addresses',()=>{
  assert.equal(displayNumericText('MOVEL -460,0.0000000000000039458535,406.2203392985,180,0,0 completed'),
    'MOVEL -460,0,406.22,180,0,0 completed')
  assert.equal(displayNumericText('误差0.370712mm/0.463721°; WAIT 1e-9'),'误差0.37mm/0.46°; WAIT 0')
  const identity='192.168.0.10 v1.234.567 object_123.456 uuid-12a4-f93'
  assert.equal(displayNumericText(identity),identity)
})
test('summaries and metadata format projections leave original records and numeric payloads intact',()=>{
  const record={text:'MOVEL -460,0.0000000000000039,406.220339 completed',extra:{tcp:406.220339,id:'abc123'}}
  const before=structuredClone(record),result=summarizeLog(record)
  assert.equal(result.original,record.text)
  assert.equal(result.text,'MOVEL -460,0,406.22 completed')
  assert.equal(JSON.parse(displayNumericJson(record.extra)).tcp,406.22)
  assert.deepEqual(record,before)
})
test('operator point, calibration and residual panels do not display more than two decimals',()=>{
  for(const file of ['components/ProgramEditor.vue','panels/EngineeringPanel.vue','panels/PointsPanel.vue',
    'panels/ProgramPanel.vue','panels/SimPanel.vue','panels/TeachingPanel.vue']){
    assert.doesNotMatch(readFileSync(new URL('../src/'+file,import.meta.url),'utf8'),/\.toFixed\([3-9]/,file)
  }
})
