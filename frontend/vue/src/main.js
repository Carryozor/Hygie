import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'

const app = createApp({ template: '<div>Hygie v3</div>' })
app.use(createPinia())
app.mount('#app')
