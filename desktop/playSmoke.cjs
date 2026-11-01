// Legacy opt-in entry delegates to the same official UI/Runner workflows.
// No object patch or independent simulated task implementation remains.
const assert=require('node:assert/strict')
async function verifyPlay(options){
  assert.equal(process.env.HUYAN_NO_REAL,'1')
  options.report.play={completed:false}
  await require('./renewalSmoke.cjs').verifyRenewal(options)
  assert.equal(options.report.renewal.completed,true)
  assert.equal(options.report.robotPlay.completed,true)
  assert.equal(options.report.robotPlay.score.total,3)
  assert.equal(options.report.robotPlay.score.correct,3)
  assert.equal(options.report.robotPlay.score.score,100)
  options.report.play={completed:true,chessPlaced:options.report.renewal.chessPlaced,sorted:3,score:100,workflow:'official-renewal-and-robot-play'}
}
module.exports={verifyPlay}
