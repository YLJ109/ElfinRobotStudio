const { contextBridge, ipcRenderer } = require('electron')
const subscribe = (channel, callback) => {
  if (typeof callback !== 'function') return () => {}
  const listener = (_event, value) => callback(value)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}
contextBridge.exposeInMainWorld('elfinDesktop', Object.freeze({
  version: 1,
  minimize: () => ipcRenderer.invoke('window:minimize'),
  toggleMaximize: () => ipcRenderer.invoke('window:maximize'),
  close: () => ipcRenderer.invoke('window:close'),
  getWindowState: () => ipcRenderer.invoke('window:state'),
  openVisionWorkspace: () => ipcRenderer.invoke('vision:open'),
  recoveryState: () => ipcRenderer.invoke('desktop:recovery-state'),
  useRecovery: copyId => ipcRenderer.invoke('desktop:use-recovery',copyId),
  onWindowState: callback => subscribe('window:state-changed', callback),
  reportBoot: value => ipcRenderer.send('desktop:boot', value),
  onBootProgress: callback => subscribe('desktop:progress', callback),
  retry: () => ipcRenderer.invoke('desktop:retry'),
}))
