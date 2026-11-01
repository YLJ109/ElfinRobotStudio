const fingerprint=value=>typeof value==='string'&&/^[a-f0-9]{64}$/i.test(value)
export function acceptanceView(value){
  const evidence=value?.evidence,build=value?.build||{}
  const current=value?.ok===true&&value.matches_current===true&&build.verified===true&&fingerprint(build.backend_sha256)&&fingerprint(build.frontend_sha256)&&build.backend_sha256===evidence?.backend_sha256&&build.frontend_sha256===evidence?.frontend_sha256
  const rows=(Array.isArray(evidence?.features)?evidence.features:[]).filter(feature=>feature&&typeof feature==='object').map(feature=>({
    id:String(feature.id||''),title:String(feature.title||''),
    source:current?(feature.source===true?'已具备':'未实现'):'当前待核验',
    offline:current?(feature.offline===true?'已通过':'未验证'):'当前待核验',
    ui:current?(feature.ui===true?'已通过':'未验证'):'当前待核验',
    field:current&&feature.field===true?'已现场验证':'未现场验证',
    unverified:Array.isArray(feature.unverified)?feature.unverified.filter(item=>typeof item==='string'):[],
    evidence:Array.isArray(feature.evidence)?feature.evidence.filter(item=>typeof item==='string'):[],
    history:current?'':[['source','源码已具备'],['offline','离线已通过'],['ui','界面已通过'],['field','现场已验证']].filter(([key])=>feature[key]===true).map(([,label])=>label).join(' · ')
  }))
  return {current,rows,hasEvidence:!!evidence}
}
