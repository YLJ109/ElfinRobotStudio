// 异步、限量日志：最多保留当前文件与两个轮转文件，待写队列最多256KiB。
const fs=require('node:fs/promises')
class BoundedLog {
  constructor(file,{maxBytes=5*1024*1024,maxPending=256*1024,onError=()=>{}}={}) {
    Object.assign(this,{file,maxBytes,maxPending,onError})
    this.pending=[];this.pendingBytes=0;this.droppedBytes=0;this.writing=null;this.closed=false;this.size=null
  }
  write(chunk) {
    if(this.closed)return false
    const buffer=Buffer.from(chunk)
    if(this.pendingBytes+buffer.length>this.maxPending){this.droppedBytes+=buffer.length;return false}
    this.pending.push(buffer);this.pendingBytes+=buffer.length
    if(!this.writing)this.writing=this.drain().finally(()=>{this.writing=null;if(this.pending.length)this.writePending()})
    return true
  }
  writePending(){if(!this.writing)this.writing=this.drain().finally(()=>{this.writing=null;if(this.pending.length)this.writePending()})}
  async drain() {
    try {
      if(this.size===null)this.size=await fs.stat(this.file).then(stat=>stat.size,error=>{if(error.code==='ENOENT')return 0;throw error})
      while(this.pending.length){
        let chunk=this.pending.shift();this.pendingBytes-=chunk.length
        if(this.droppedBytes){chunk=Buffer.concat([Buffer.from(`[日志过载，已丢弃 ${this.droppedBytes} 字节]\n`),chunk]);this.droppedBytes=0}
        if(this.size+chunk.length>this.maxBytes){
          for(const [from,to] of [[this.file+'.1',this.file+'.2'],[this.file,this.file+'.1']])
            await fs.rename(from,to).catch(error=>{if(error.code!=='ENOENT')throw error})
          this.size=0
        }
        // 单条异常输出也不能突破文件大小上限。
        chunk=chunk.subarray(-this.maxBytes)
        await fs.appendFile(this.file,chunk);this.size+=chunk.length
      }
    }catch(error){this.pending=[];this.pendingBytes=0;this.closed=true;this.onError(error)}
  }
  async end(){this.closed=true;while(this.writing)await this.writing}
}
module.exports={BoundedLog}
