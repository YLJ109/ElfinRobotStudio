"""One ephemeral clarification per World; no authority, persistence or model calls."""
import secrets
import time
from ..core.ai_intent import merge_clarification

class Clarifications:
    TTL=120.
    MAX_TURNS=4
    def __init__(self,clock=time.monotonic):self.clock=clock;self.pending=None
    def revoke(self,request_id):
        if self.pending and request_id==self.pending['request_id']:self.pending=None
    def resolve(self,text,token,*,context,session):
        row=self.pending
        if (not isinstance(token,dict) or not isinstance(token.get('id'),str) or type(token.get('revision')) is not int
            or not row or token['id']!=row['id'] or token['revision']!=row['revision']):
            raise ValueError('澄清请求已失效或版本不匹配，请重新提出完整要求')
        if self.clock()>=row['expires'] or context!=row['context'] or session!=row['session']:
            self.pending=None;raise ValueError('澄清已过期或现场已变化，请重新提出完整要求')
        if row['turns']>=self.MAX_TURNS:self.pending=None;raise ValueError('本次补充次数已达上限，请重新提出完整要求')
        merged=merge_clarification(row['text'],text)
        if len(merged)>2000:raise ValueError('补充后的完整要求超过2000字，请重新填写')
        self.pending=None  # consume before any asynchronous work
        return merged,{**row,'revision':row['revision']+1,'turns':row['turns']+1}
    def publish(self,text,*,context,session,request_id,previous=None):
        if not isinstance(text,str) or not 1<=len(text.strip())<=2000:return None
        now=self.clock()
        row=dict(id=previous['id'] if previous else secrets.token_urlsafe(18),revision=previous['revision'] if previous else 1,
                 turns=previous['turns'] if previous else 0,text=text[:2000],context=context,session=session,
                 expires=previous['expires'] if previous else now+self.TTL,request_id=request_id)
        if row['expires']<=now:return None
        self.pending=row
        return {'id':row['id'],'revision':row['revision']}
