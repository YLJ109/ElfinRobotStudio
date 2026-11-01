import { installPanelFolding } from './panelFolding.js'
import { createApp, watch } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './theme.css'
import { installTabNavigation } from './tabNavigation.js'
import { createDesktopStartup } from './desktopStartup.js'
import { useWorld } from './store.js'
import { numberPrecision, displayValue } from './numberDisplay.js'

const disposeTabNavigation=installTabNavigation(document)
if(import.meta.hot)import.meta.hot.dispose(disposeTabNavigation)
const pinia = createPinia()
const disposeDesktopStartup = createDesktopStartup({ bridge: globalThis.elfinDesktop, world: useWorld(pinia), watch })
if(import.meta.hot)import.meta.hot.dispose(disposeDesktopStartup)
const app = createApp(App).use(pinia).directive('two-decimals', numberPrecision)
app.config.globalProperties.$displayValue = displayValue
app.mount('#app')

const disposeFolding = installPanelFolding(document)
if (import.meta.hot) import.meta.hot.dispose(disposeFolding)
