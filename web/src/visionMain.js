import { createApp } from 'vue'
import { createPinia } from 'pinia'
import VisionStation from './VisionStation.vue'
import './theme.css'
import { installTabNavigation } from './tabNavigation.js'

// Independent entry: no main App, desktop startup, world boot, or 3D scene.
const disposeTabs = installTabNavigation(document)
const app = createApp(VisionStation).use(createPinia())
app.mount('#app')
if (import.meta.hot) import.meta.hot.dispose(() => { app.unmount(); disposeTabs() })
