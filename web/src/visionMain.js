import { installPanelFolding } from './panelFolding.js'
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import VisionStation from './VisionStation.vue'
import './theme.css'
import { installTabNavigation } from './tabNavigation.js'
import { numberPrecision, displayValue } from './numberDisplay.js'

// Independent entry: no main App, desktop startup, world boot, or 3D scene.
const disposeTabs = installTabNavigation(document)
const app = createApp(VisionStation).use(createPinia())
app.directive('two-decimals', numberPrecision)
app.config.globalProperties.$displayValue = displayValue
app.mount('#app')
if (import.meta.hot) import.meta.hot.dispose(() => { app.unmount(); disposeTabs() })

const disposeFolding = installPanelFolding(document)
if (import.meta.hot) import.meta.hot.dispose(disposeFolding)
