// frontend/vue/src/api/client.js
import axios from 'axios'
import { installErrorInterceptor } from './errorHandler'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('hygie_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

api.interceptors.response.use(
  r => r,
  err => {
    const isPublic = ['/login', '/setup', '/public'].includes(window.location.pathname)
    if (err.response?.status === 401 && !isPublic) {
      localStorage.removeItem('hygie_token')
      window.dispatchEvent(new Event('hygie:unauthorized'))
    }
    return Promise.reject(err)
  }
)

installErrorInterceptor(api)

export default api
